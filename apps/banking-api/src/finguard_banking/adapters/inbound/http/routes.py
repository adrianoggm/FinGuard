from datetime import date, datetime
from typing import Annotated, Self
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, model_validator
from pydantic.alias_generators import to_camel

from finguard_banking.application.get_balance import GetBalance
from finguard_banking.application.run_financial_query import RunFinancialQuery
from finguard_banking.bootstrap.dependencies import (
    database_answers,
    get_balance,
    reporting_today,
    run_financial_query,
)
from finguard_banking.domain.enums import Direction, GroupBy, Metric
from finguard_banking.domain.financial_query import DateRange, FinancialQuery
from finguard_banking.domain.relative_period import RelativePeriod, resolve

Balances = Annotated[GetBalance, Depends(get_balance)]
Queries = Annotated[RunFinancialQuery, Depends(run_financial_query)]
DatabaseAnswers = Annotated[bool, Depends(database_answers)]
Today = Annotated[date, Depends(reporting_today)]


class _Payload(BaseModel):
    """Outwards everything is camelCase, like the contracts in vision.md §13."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)


class FinancialQueryRequest(BaseModel):
    """Request body.

    Accepts either an explicit range or a relative period; the domain only knows
    absolute ranges, so the resolution happens here, at the edge, which is where the
    clock lives. Per-field limits are validated by the domain: they are not duplicated
    here so that they cannot drift apart.
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")

    account_id: UUID
    metric: Metric = Metric.SUM
    direction: Direction = Direction.ALL
    categories: tuple[str, ...] = ()
    date_range: DateRange | None = None
    period: RelativePeriod | None = None
    group_by: GroupBy = GroupBy.NONE
    supporting_limit: int = 20
    explain: bool = False

    @model_validator(mode="after")
    def _exactly_one_range(self) -> Self:
        if (self.date_range is None) == (self.period is None):
            raise ValueError("provide exactly one of dateRange or period")
        return self

    def to_query(self, today: date) -> FinancialQuery:
        if self.date_range is not None:
            date_range = self.date_range
        elif self.period is not None:
            date_range = resolve(self.period, today)
        else:  # pragma: no cover - prevented by the validator
            raise ValueError("missing period")

        return FinancialQuery(
            account_id=self.account_id,
            metric=self.metric,
            direction=self.direction,
            categories=self.categories,
            date_range=date_range,
            group_by=self.group_by,
            supporting_limit=self.supporting_limit,
            explain=self.explain,
        )


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


router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    """The process answers. It deliberately does not check dependencies."""
    return {"status": "ok"}


@router.get("/ready")
async def ready(database_answers: DatabaseAnswers) -> JSONResponse:
    """Ready to serve: the database answers."""
    if not database_answers:
        return JSONResponse({"status": "unavailable", "database": "down"}, status_code=503)
    return JSONResponse({"status": "ok", "database": "up"})


@router.get("/v1/accounts/{account_id}/balance")
async def read_balance(account_id: UUID, balances: Balances) -> BalanceResponse:
    return BalanceResponse.model_validate(await balances(account_id))


@router.post("/v1/financial-queries")
async def post_financial_query(
    body: FinancialQueryRequest, queries: Queries, today: Today
) -> FinancialQueryResponse:
    """Runs a validated query plan (§13.2).

    The body is the intermediate representation from §9.2, not SQL: any field we do
    not recognise is rejected. If it carries a relative period, it is resolved here.
    """
    return FinancialQueryResponse.model_validate(await queries(body.to_query(today)))
