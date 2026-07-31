"""The wire format of the banking endpoints (vision.md §13).

DTOs, not domain models: camelCase, `from`/`to` and the whole request shape live here,
so a change to the contract cannot reach the core. The domain speaks snake_case, knows
nothing about HTTP and carries Money instead of loose amount/currency pairs — the
mapper is the only thing that knows both shapes.
"""

from datetime import date, datetime
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

from finguard_banking.domain.constants import (
    DEFAULT_SUPPORTING_MOVEMENTS,
    MAX_PERIOD_DAYS,
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
