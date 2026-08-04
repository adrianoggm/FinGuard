"""The wire format of the banking endpoints (vision.md §13).

DTOs, not domain models: camelCase, `from`/`to` and the whole request shape live here,
so a change to the contract cannot reach the core. The domain speaks snake_case, knows
nothing about HTTP and carries Money instead of loose amount/currency pairs — the
mapper is the only thing that knows both shapes.
"""

from datetime import date, datetime
from typing import Self
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel

from finguard_banking.domain.constants import (
    DEFAULT_SUPPORTING_MOVEMENTS,
    MAX_CATEGORY_LENGTH,
    MAX_PAYMENT_MINOR,
    MAX_PERIOD_DAYS,
    MAX_RANK,
    MAX_RECIPIENT_NAME,
    MAX_SUPPORTING_MOVEMENTS,
)
from finguard_banking.domain.enums import Direction, GroupBy, Metric, Period, Rail


class _Payload(BaseModel):
    """Outwards everything is camelCase, like the contracts in vision.md §13."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class _Body(_Payload):
    """Inbound bodies reject anything they do not recognise."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class DateRangeRequest(_Body):
    start: date = Field(alias="from")
    end: date = Field(alias="to")


class RelativePeriodRequest(_Body):
    period: Period
    n: int | None = Field(default=None, ge=1, le=MAX_PERIOD_DAYS)
    reference: date | None = None
    # The zone `reference` was measured in. Optional for compatibility, and the pair is
    # what actually describes a window: a caller in the Canaries and one in Madrid do not
    # share a "today" between midnight and 01:00.
    timezone: str | None = None

    @field_validator("timezone")
    @classmethod
    def _known_zone(cls, value: str | None) -> str | None:
        """Refused rather than ignored. Falling back silently is the dangerous outcome:
        the caller believes it asked about Canarian days and is answered about peninsular
        ones, with a real figure and no error."""
        if value is None:
            return None
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown time zone: {value!r}") from exc
        return value


class FinancialQueryRequest(_Body):
    """Either an explicit range or a relative period, never both.

    The per-field business limits are the domain's: they are checked when the plan is
    built, so they cannot drift between two definitions. What is validated here is the
    shape.
    """

    account_id: UUID
    metric: Metric = Metric.SUM
    direction: Direction = Direction.ALL
    categories: tuple[str, ...] = ()
    # Shops, matched case-insensitively. Free text on purpose: the ledger's merchant names
    # are data, not vocabulary.
    merchants: tuple[str, ...] = ()
    date_range: DateRangeRequest | None = None
    period: RelativePeriodRequest | None = None
    group_by: GroupBy = GroupBy.NONE
    supporting_limit: int = Field(
        default=DEFAULT_SUPPORTING_MOVEMENTS, ge=0, le=MAX_SUPPORTING_MOVEMENTS
    )
    explain: bool = False

    @model_validator(mode="after")
    def _exactly_one_range(self) -> Self:
        if (self.date_range is None) == (self.period is None):
            raise ValueError("provide exactly one of dateRange or period")
        return self


class BalanceResponse(_Payload):
    account_id: UUID
    available_minor: int
    booked_minor: int
    currency: str
    as_of: datetime


class GroupResponse(_Payload):
    key: str
    total_minor: int
    movement_count: int


class MovementResponse(_Payload):
    transaction_id: UUID
    occurred_at: datetime
    description: str
    merchant_name: str | None
    category: str | None
    # Whose opinion the category is. False means the bank's.
    category_is_custom: bool = False
    amount_minor: int
    direction: str


class RankedMovementRequest(_Body):
    """The same filters as a query, plus which position is being asked about.

    It reuses the query's shape deliberately: the filters of "the largest fuel expense this year"
    are the filters of "how much did I spend on fuel this year", and a second declaration of them
    would be a second place to get the window wrong.

    `metric`, `group_by` and `supporting_limit` are absent because they mean nothing here — there is
    no aggregate to group and no total to support. A field with no meaning is a field a caller will
    eventually set.
    """

    account_id: UUID
    direction: Direction = Direction.ALL
    categories: tuple[str, ...] = ()
    merchants: tuple[str, ...] = ()
    date_range: DateRangeRequest | None = None
    period: RelativePeriodRequest | None = None
    reporting_timezone: str | None = None
    # 1 is the largest. Bounded because it becomes an OFFSET.
    rank: int = Field(default=1, ge=1, le=MAX_RANK)


class RankedMovementResponse(_Payload):
    """One movement, or none.

    `movement` is absent when the period holds fewer than `rank` of them. Absent rather than a row
    of zeros: "el quinto" of three has no answer, and a shape that always carries a movement would
    make the caller invent one.
    """

    account_id: UUID
    rank: int
    movement: MovementResponse | None = None
    # Beside the movement rather than inside it, exactly like the aggregate's: it is a fact about
    # the account, not about the row. Absent when no movement matched, because there is then no
    # amount to denominate.
    currency: str | None = None


class TraceResponse(_Payload):
    sql: str
    parameters: list[str]
    elapsed_ms: float


class FinancialQueryResponse(_Payload):
    account_id: UUID
    currency: str
    total_minor: int
    movement_count: int
    groups: list[GroupResponse]
    supporting_movements: list[MovementResponse]
    trace: TraceResponse | None = None


class PreparePaymentRequest(_Body):
    """What somebody intends to pay (§4.1).

    No account: it is in the path, and a body that could name one would let a caller pay out of an
    account they do not hold.

    The recipient is a name *or* the identifier of a contact the caller was shown, and exactly one
    of the two. The identifier is here because a name can be ambiguous forever — two contacts called
    Marta Solis Vega are a question no spelling answers — and it is safe for the same reason the
    name is: both are resolved against this account's own contacts, through a view that does not
    contain anybody else's.

    Exactly one, enforced here rather than in the handler. Both would be two answers to "who gets
    paid" with no rule about which wins; neither is a payment to nobody. 422 is the honest answer to
    a body that cannot be read one way.

    The amount is in minor units, like everywhere else. `gt=0` because a payment of nothing is not
    a payment, and a negative one is a withdrawal wearing a payment's clothes.
    """

    amount_minor: int = Field(gt=0, le=MAX_PAYMENT_MINOR)
    currency: str = Field(pattern="^[A-Z]{3}$")
    recipient_name: str | None = Field(default=None, min_length=1, max_length=MAX_RECIPIENT_NAME)
    recipient_contact_id: UUID | None = None
    # BIZUM unless the caller says otherwise, so every client written before transfers existed
    # keeps meaning what it meant. It is the one default here, and it is a default about the past.
    rail: Rail = Rail.BIZUM
    # An account the holder typed. Only for a transfer, and only when the recipient is not a
    # favourite — with it, a name is required, because the destination is then two fields the
    # caller supplied rather than a row this service already had.
    #
    # It arrives as its own field, never inside a sentence. A language model transcribing
    # twenty-four characters is a checksum-valid stranger waiting to happen, and the check digits
    # catch a slip of the finger rather than a confident misreading.
    recipient_iban: str | None = Field(default=None, min_length=15, max_length=42)

    @model_validator(mode="after")
    def _one_recipient(self) -> "PreparePaymentRequest":
        named = self.recipient_name is not None
        chosen = self.recipient_contact_id is not None
        typed = self.recipient_iban is not None

        if typed:
            if self.rail is not Rail.TRANSFER:
                raise ValueError("an account number is only a destination for a transfer")
            if chosen:
                raise ValueError(
                    "a transfer goes to a saved contact or to a typed account, not both"
                )
            if not named:
                raise ValueError("a transfer to a typed account needs a name to print beside it")
            return self

        if named == chosen:
            raise ValueError("name the recipient either as recipientName or as recipientContactId")
        return self


class PaymentPreviewResponse(_Payload):
    """What the user is asked to approve. The balance it leaves is part of the question.

    The destination is spelled by rail: a Bizum carries the phone, a transfer carries the account.
    Both are optional on the wire and exactly one is ever present, which is weaker than the domain
    union behind it — a response shape is read by clients that cannot be changed in the same commit,
    so it grows fields rather than replacing them.

    The IBAN is sent whole and unmasked. Somebody approving a transfer is being asked to recognise
    twenty-four characters, and four of them with dots in between is not a thing anybody can check.
    """

    intent_id: UUID
    rail: str
    recipient: str
    recipient_phone: str | None = None
    recipient_iban: str | None = None
    amount_minor: int
    currency: str
    balance_before_minor: int
    balance_after_minor: int
    expires_at: datetime


class PaymentReceiptResponse(_Payload):
    """Proof, with the ledger movement to check it against."""

    intent_id: UUID
    transaction_id: UUID
    rail: str
    recipient: str
    recipient_phone: str | None = None
    recipient_iban: str | None = None
    amount_minor: int
    currency: str
    balance_after_minor: int
    confirmed_at: datetime


class ContactResponse(_Payload):
    contact_id: UUID
    display_name: str
    phone: str


class ContactsResponse(_Payload):
    """The whole list. A payment screen shows who you can pay, so a page here would mean the name
    somebody was looking for is the one that did not fit."""

    contacts: list[ContactResponse] = []


class CardResponse(_Payload):
    """A card as a screen needs it. Four digits and never a number.

    `spentMinor` is what was charged since `since`, which travels beside it: a figure whose window
    is implicit is a figure somebody will eventually label as a month.
    """

    card_id: UUID
    display_name: str
    brand: str
    kind: str
    last_four: str
    expires_month: int
    expires_year: int
    status: str
    # null means the balance is the limit, which is a debit card — not a limit of zero.
    spending_limit_minor: int | None
    spent_minor: int
    charge_count: int


class CardsResponse(_Payload):
    since: str
    cards: list[CardResponse] = []


class CardChargeResponse(_Payload):
    """One charge on a card. Unsigned: every row is money out, and the screen writes the minus."""

    transaction_id: UUID
    occurred_at: datetime
    description: str
    merchant_name: str | None
    category: str | None
    amount_minor: int


class CardChargesResponse(_Payload):
    card_id: UUID
    since: str
    charges: list[CardChargeResponse] = []


class CategoryRequest(_Body):
    """What a movement should be filed under.

    `null` clears the override, which falls back to what the bank inferred. That is a different
    request from "file it under nothing", and there is no way to express the second — a movement
    always has a category, and the only question is whose.
    """

    category: str | None = Field(default=None, max_length=MAX_CATEGORY_LENGTH)


class CategoriesResponse(_Payload):
    """The categories this ledger uses. A closed list, and the same one the planner is constrained
    to: a category the assistant could not name back is not one a screen should offer."""

    categories: list[str] = []
