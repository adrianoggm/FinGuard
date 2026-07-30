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
from finguard_banking.domain.financial_query import (
    DateRange,
    Direction,
    FinancialQuery,
    GroupBy,
    Metric,
)
from finguard_banking.domain.relative_period import RelativePeriod, resolve

Balances = Annotated[GetBalance, Depends(get_balance)]
Queries = Annotated[RunFinancialQuery, Depends(run_financial_query)]
DatabaseAnswers = Annotated[bool, Depends(database_answers)]
Today = Annotated[date, Depends(reporting_today)]


class _Respuesta(BaseModel):
    """Hacia fuera todo va en camelCase, como los contratos de vision.md §13."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)


class BalanceResponse(_Respuesta):
    account_id: UUID
    available_minor: int
    booked_minor: int
    currency: str
    as_of: datetime


class FinancialQueryRequest(BaseModel):
    """Cuerpo de la petición.

    Admite el rango explícito o un periodo relativo; el dominio solo conoce rangos
    absolutos, así que la resolución ocurre aquí, en el borde, que es donde vive el
    reloj. Los límites de cada campo los valida el dominio: aquí no se duplican para
    que no puedan divergir.
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
    def _un_solo_periodo(self) -> Self:
        if (self.date_range is None) == (self.period is None):
            raise ValueError("indica exactamente uno de dateRange o period")
        return self

    def a_consulta(self, hoy: date) -> FinancialQuery:
        if self.date_range is not None:
            rango = self.date_range
        elif self.period is not None:
            rango = resolve(self.period, hoy)
        else:  # pragma: no cover - lo impide el validador
            raise ValueError("falta el periodo")

        return FinancialQuery(
            account_id=self.account_id,
            metric=self.metric,
            direction=self.direction,
            categories=self.categories,
            date_range=rango,
            group_by=self.group_by,
            supporting_limit=self.supporting_limit,
            explain=self.explain,
        )


class GroupResponse(_Respuesta):
    key: str
    total_minor: int
    movement_count: int


class MovementResponse(_Respuesta):
    transaction_id: UUID
    occurred_at: datetime
    description: str
    merchant_name: str | None
    category: str | None
    amount_minor: int
    direction: str


class TraceResponse(_Respuesta):
    sql: str
    parameters: list[str]
    elapsed_ms: float


class FinancialQueryResponse(_Respuesta):
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
    """El proceso responde. No comprueba dependencias a propósito."""
    return {"status": "ok"}


@router.get("/ready")
async def ready(database_answers: DatabaseAnswers) -> JSONResponse:
    """Listo para servir: la base de datos contesta."""
    if not database_answers:
        return JSONResponse({"status": "unavailable", "database": "down"}, status_code=503)
    return JSONResponse({"status": "ok", "database": "up"})


@router.get("/v1/accounts/{account_id}/balance")
async def read_balance(account_id: UUID, balances: Balances) -> BalanceResponse:
    return BalanceResponse.model_validate(await balances(account_id))


@router.post("/v1/financial-queries")
async def post_financial_query(
    body: FinancialQueryRequest, queries: Queries, hoy: Today
) -> FinancialQueryResponse:
    """Ejecuta un plan de consulta validado (§13.2).

    El cuerpo es la representación intermedia del §9.2, no SQL: se rechaza cualquier
    campo que no se reconozca. Si trae un periodo relativo, se resuelve aquí.
    """
    return FinancialQueryResponse.model_validate(await queries(body.a_consulta(hoy)))
