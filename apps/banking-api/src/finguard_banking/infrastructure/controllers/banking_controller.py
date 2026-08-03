"""HTTP surface of the banking core. Routes only.

Shapes live in dto/, conversion in mappers/, and every route delegates to a use case —
a controller never reaches a repository.
"""

from datetime import date, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from finguard_banking.application import (
    GetBalanceQuery,
    ListCardsQuery,
    ListContactsQuery,
    MovementSummaryQuery,
    QueryBus,
)
from finguard_banking.bootstrap.dependencies import (
    database_answers,
    get_query_bus,
    reporting_today,
)
from finguard_banking.domain.constants import MAX_CARD_WINDOW_DAYS
from finguard_banking.infrastructure.dto.banking_dto import (
    BalanceResponse,
    CardsResponse,
    ContactsResponse,
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


@accounts_router.get("/{account_id}/cards", summary="The cards this account holds")
async def read_cards(
    account_id: UUID,
    queries: Queries,
    today: Today,
    days: Annotated[int, Query(ge=1, le=MAX_CARD_WINDOW_DAYS)] = 30,
) -> CardsResponse:
    """Each card with what has been charged to it in the window, computed in the reporting zone.

    A rolling window rather than "this month", and the answer says which date it starts from. A
    month is what a caller assumes when nothing says otherwise, and a figure labelled as a month
    that is really thirty days is wrong on the 1st by everything that happened on the 31st.
    """
    since = today - timedelta(days=days)
    result = await queries.ask(ListCardsQuery(account_id=account_id, since=since))
    return http_mapper.cards_to_dto(result)


@accounts_router.get("/{account_id}/contacts", summary="Everybody this account can pay")
async def read_contacts(account_id: UUID, queries: Queries) -> ContactsResponse:
    """The whole list, through the same restricted role and view as every other read.

    A payment screen cannot work without it: asking somebody to type a name they can only get right
    from a list they have not seen is how money reaches the wrong Marta.
    """
    return http_mapper.contacts_to_dto(await queries.ask(ListContactsQuery(account_id)))


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
