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
    MAX_PAYMENT_MINOR,
    MAX_PERIOD_DAYS,
    MAX_RECIPIENT_NAME,
    MAX_SUPPORTING_MOVEMENTS,
)
from finguard_banking.domain.enums import Direction, GroupBy, Metric, Period


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
    amount_minor: int
    direction: str


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

    No account and no contact identifier: the account is in the path and the recipient is a name
    resolved against that account's own contacts. A body that could name either would let a caller
    pay out of an account they do not hold, or to somebody who is not in their list.

    The amount is in minor units, like everywhere else. `gt=0` because a payment of nothing is not
    a payment, and a negative one is a withdrawal wearing a payment's clothes.
    """

    recipient_name: str = Field(min_length=1, max_length=MAX_RECIPIENT_NAME)
    amount_minor: int = Field(gt=0, le=MAX_PAYMENT_MINOR)
    currency: str = Field(pattern="^[A-Z]{3}$")


class PaymentPreviewResponse(_Payload):
    """What the user is asked to approve. The balance it leaves is part of the question."""

    intent_id: UUID
    recipient: str
    recipient_phone: str
    amount_minor: int
    currency: str
    balance_before_minor: int
    balance_after_minor: int
    expires_at: datetime


class PaymentReceiptResponse(_Payload):
    """Proof, with the ledger movement to check it against."""

    intent_id: UUID
    transaction_id: UUID
    recipient: str
    recipient_phone: str
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
