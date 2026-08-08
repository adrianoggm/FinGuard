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
    MAX_SELECTED_ACCOUNTS,
    MAX_SELECTED_TRANSACTIONS,
    MAX_SUPPORTING_MOVEMENTS,
)
from finguard_banking.domain.enums import AccountKind, Direction, GroupBy, Metric, Period, Rail


class _Payload(BaseModel):
    """Outwards everything is camelCase, like the contracts in vision.md §13."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class _Body(_Payload):
    """Inbound bodies reject anything they do not recognise."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


def known_zone(value: str | None) -> str | None:
    """A zone this machine has, or a refusal.

    Refused rather than ignored. Falling back silently is the dangerous outcome: the caller
    believes it asked about Canarian days and is answered about peninsular ones, with a real
    figure and no error.

    A function rather than only a validator, because the card routes take the zone as a query
    parameter instead of in a body. Two copies of "which zones exist" is how one endpoint ends up
    stricter than the one beside it.
    """
    if value is None:
        return None
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"unknown time zone: {value!r}") from exc
    return value


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
        return known_zone(value)


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
    excluded_categories: tuple[str, ...] = ()
    # Shops, matched case-insensitively. Free text on purpose: the ledger's merchant names
    # are data, not vocabulary.
    merchants: tuple[str, ...] = ()
    excluded_merchants: tuple[str, ...] = ()
    date_range: DateRangeRequest | None = None
    period: RelativePeriodRequest | None = None
    group_by: GroupBy = GroupBy.NONE
    # Money the holder moved between their own accounts. Absent means excluded, which is what
    # makes "¿cuánto gasté?" mean money that left rather than money that moved.
    include_internal: bool = False
    # Narrow to internal movements rather than merely adding them to ordinary movements.
    internal_only: bool = False
    # Every account this session can see rather than the one in `accountId`. It cannot widen past
    # the holder: it drops the compiler's filter and leaves the view's, which is the scope rule.
    across_accounts: bool = False
    # Narrow to one account within what the session can already see. Never what gets declared —
    # that is `accountId`, the credential's — because declaring an account opens its holder scope.
    only_account_id: UUID | None = None
    # Several explicitly selected accounts, still within the holder opened by accountId.
    only_account_ids: tuple[UUID, ...] = Field(default=(), max_length=MAX_SELECTED_ACCOUNTS)
    transaction_ids: tuple[UUID, ...] = Field(default=(), max_length=MAX_SELECTED_TRANSACTIONS)
    supporting_limit: int = Field(
        default=DEFAULT_SUPPORTING_MOVEMENTS, ge=0, le=MAX_SUPPORTING_MOVEMENTS
    )
    explain: bool = False

    @model_validator(mode="after")
    def _exactly_one_range(self) -> Self:
        if (self.date_range is None) == (self.period is None):
            raise ValueError("provide exactly one of dateRange or period")
        if self.only_account_id is not None and self.only_account_ids:
            raise ValueError("use onlyAccountId or onlyAccountIds, not both")
        if self.internal_only and not self.include_internal:
            raise ValueError("internalOnly requires includeInternal")
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
    category_is_custom: bool
    # Which layer decided the category — BANK, SYSTEM, USER or NONE — and whether the money stayed
    # inside the household. Both are facts a screen needs to label a figure honestly.
    category_source: str = "NONE"
    is_internal: bool = False
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
    excluded_categories: tuple[str, ...] = ()
    merchants: tuple[str, ...] = ()
    excluded_merchants: tuple[str, ...] = ()
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
    period_start: date
    period_end: date
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
    # Another account of this holder, for the INTERNAL rail. An identifier and not a number: an id
    # the caller was shown cannot resolve to a different account than the one they picked. Whether
    # it is *theirs* is checked where the intent is prepared, against the view that listed it.
    to_account_id: UUID | None = None

    @model_validator(mode="after")
    def _one_recipient(self) -> "PreparePaymentRequest":
        named = self.recipient_name is not None
        chosen = self.recipient_contact_id is not None
        typed = self.recipient_iban is not None

        if self.to_account_id is not None:
            if self.rail is not Rail.INTERNAL:
                raise ValueError("one of your own accounts is only an internal destination")
            if typed or chosen or named:
                raise ValueError("an internal transfer goes to one of your accounts, only")
            return self
        if self.rail is Rail.INTERNAL:
            raise ValueError("an internal transfer needs toAccountId")

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
    # Which of the holder's accounts an internal transfer landed in.
    to_account_id: UUID | None = None
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
    # Which of the holder's accounts an internal transfer landed in.
    to_account_id: UUID | None = None
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


class HoldingResponse(_Payload):
    """One of the holder's accounts.

    `bookedMinor` and `currency` travel as a pair, like everywhere else money crosses this
    boundary: an amount whose currency is implicit is an amount somebody eventually labels in
    euros.

    `kind` is what tells a client whether the figure can be added to a total. On an `ASSET` it is
    the cost basis and not a valuation, so a screen that sums all three without saying so is
    reporting a purchase price as if it were worth.
    """

    account_id: UUID
    kind: AccountKind
    display_name: str | None
    currency: str
    booked_minor: int
    status: str


class HoldingsResponse(_Payload):
    """Every account this holder has, the declared one included.

    No total. Accounts in different currencies do not have one, and a field here would have to be
    filled with something — the caller decides, because only the caller knows whether it is about
    to print the number or compare it.
    """

    accounts: list[HoldingResponse] = []


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
