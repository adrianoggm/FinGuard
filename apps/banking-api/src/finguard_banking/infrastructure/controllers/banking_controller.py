"""HTTP surface of the banking core. Routes only.

Shapes live in dto/, conversion in mappers/, and every route delegates to a use case —
a controller never reaches a repository.
"""

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from finguard_banking.application import GetBalanceQuery, MovementSummaryQuery, QueryBus
from finguard_banking.bootstrap.dependencies import (
    database_answers,
    get_query_bus,
    reporting_today,
)
from finguard_banking.infrastructure.dto.banking_dto import (
    BalanceResponse,
    FinancialQueryRequest,
    FinancialQueryResponse,
)
from finguard_banking.infrastructure.mappers import http_mapper

Queries = Annotated[QueryBus, Depends(get_query_bus)]
DatabaseAnswers = Annotated[bool, Depends(database_answers)]
Today = Annotated[date, Depends(reporting_today)]

health_router = APIRouter(tags=["health"])
accounts_router = APIRouter(prefix="/v1/accounts", tags=["accounts"])
queries_router = APIRouter(prefix="/v1/financial-queries", tags=["queries"])


@health_router.get("/health", summary="Liveness")
async def health() -> dict[str, str]:
    """The process answers. It deliberately does not check dependencies."""
    return {"status": "ok"}


@health_router.get("/ready", summary="Readiness")
async def ready(database_answers: DatabaseAnswers) -> JSONResponse:
    """Ready to serve: the database answers."""
    if not database_answers:
        return JSONResponse({"status": "unavailable", "database": "down"}, status_code=503)
    return JSONResponse({"status": "ok", "database": "up"})


@accounts_router.get("/{account_id}/balance", summary="Balance of an account")
async def read_balance(account_id: UUID, queries: Queries) -> BalanceResponse:
    """Balance derived from the ledger entries, never from a mutable column."""
    return http_mapper.balance_to_dto(await queries.ask(GetBalanceQuery(account_id)))


@queries_router.post("", summary="Run a financial query")
async def post_financial_query(
    body: FinancialQueryRequest, queries: Queries, today: Today
) -> FinancialQueryResponse:
    """Runs a validated query plan (§13.2).

    The body is the intermediate representation from §9.2, not SQL: any field we do not
    recognise is rejected. A relative period is resolved before the plan is built.
    """
    plan = http_mapper.plan_from_dto(body, today)
    return http_mapper.summary_to_dto(await queries.ask(MovementSummaryQuery(plan)))
