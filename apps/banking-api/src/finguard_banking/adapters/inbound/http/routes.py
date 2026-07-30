from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from finguard_banking.application.get_balance import GetBalance
from finguard_banking.application.run_financial_query import RunFinancialQuery
from finguard_banking.bootstrap.dependencies import (
    database_answers,
    get_balance,
    run_financial_query,
)
from finguard_banking.domain.financial_query import FinancialQuery

Balances = Annotated[GetBalance, Depends(get_balance)]
Queries = Annotated[RunFinancialQuery, Depends(run_financial_query)]
DatabaseAnswers = Annotated[bool, Depends(database_answers)]


class _Respuesta(BaseModel):
    """Hacia fuera todo va en camelCase, como los contratos de vision.md §13."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)


class BalanceResponse(_Respuesta):
    account_id: UUID
    available_minor: int
    booked_minor: int
    currency: str
    as_of: datetime


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
async def post_financial_query(query: FinancialQuery, queries: Queries) -> FinancialQueryResponse:
    """Ejecuta un plan de consulta validado (§13.2).

    El cuerpo es la representación intermedia del §9.2, no SQL: el modelo de dominio
    rechaza cualquier campo que no reconozca.
    """
    return FinancialQueryResponse.model_validate(await queries(query))
