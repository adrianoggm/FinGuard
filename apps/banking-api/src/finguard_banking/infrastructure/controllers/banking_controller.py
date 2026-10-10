"""HTTP surface of the banking core. Routes only.

Shapes live in dto/, conversion in mappers/, and every route delegates to a use case —
a controller never reaches a repository.
"""

from datetime import date, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import AfterValidator

from finguard_banking.application import (
    CategoriseMovementCommand,
    CommandBus,
    GetBalanceQuery,
    ListAccountsQuery,
    ListCardChargesQuery,
    ListCardsQuery,
    ListContactsQuery,
    ListMovementsQuery,
    MovementSummaryQuery,
    QueryBus,
    RankedMovementQuery,
)
from finguard_banking.bootstrap.dependencies import (
    database_answers,
    get_command_bus,
    get_query_bus,
    reporting_today,
    reporting_zone,
)
from finguard_banking.domain import constants
from finguard_banking.domain.constants import MAX_CARD_WINDOW_DAYS, MAX_SUPPORTING_MOVEMENTS
from finguard_banking.infrastructure.dto.banking_dto import (
    BalanceResponse,
    CardChargesResponse,
    CardsResponse,
    CategoriesResponse,
    CategoryRequest,
    ContactsResponse,
    FinancialQueryRequest,
    FinancialQueryResponse,
    HoldingsResponse,
    MovementListRequest,
    MovementListResponse,
    RankedMovementRequest,
    RankedMovementResponse,
    known_zone,
)
from finguard_banking.infrastructure.mappers import http_mapper

Queries = Annotated[QueryBus, Depends(get_query_bus)]
Commands = Annotated[CommandBus, Depends(get_command_bus)]
DatabaseAnswers = Annotated[bool, Depends(database_answers)]
Today = Annotated[date, Depends(reporting_today)]
DeploymentZone = Annotated[str, Depends(reporting_zone)]

# What day the caller is having, and what its day is measured in. The same pair a relative period
# carries in a query body, spelled as query parameters because a card window is a GET — and here
# for the same reason: neither describes a window alone.
Reference = Annotated[date | None, Query()]
CallerZone = Annotated[str | None, Query(max_length=64), AfterValidator(known_zone)]

health_router = APIRouter(tags=["health"])
accounts_router = APIRouter(prefix="/v1/accounts", tags=["accounts"])
queries_router = APIRouter(prefix="/v1/financial-queries", tags=["queries"])


@health_router.get("/health", summary="Liveness")
async def health() -> dict[str, str]:
    """The process answers. It deliberately does not check dependencies."""
    return {"status": "ok"}


@health_router.get("/version", summary="Which build is answering")
async def version(request: Request) -> dict[str, str]:
    settings = request.app.state.settings
    return {
        "service": "banking-api",
        "sha": str(settings.build_sha),
        "ref": str(settings.build_ref),
        "source_sha256": str(settings.build_source_sha256),
    }


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
    zone: DeploymentZone,
    days: Annotated[int, Query(ge=1, le=MAX_CARD_WINDOW_DAYS)] = 30,
    reference: Reference = None,
    timezone: CallerZone = None,
) -> CardsResponse:
    """Each card with what has been charged to it in the window, computed in the caller's zone.

    A rolling window rather than "this month", and the answer says which date it starts from. A
    month is what a caller assumes when nothing says otherwise, and a figure labelled as a month
    that is really thirty days is wrong on the 1st by everything that happened on the 31st.

    `days` counts days, both ends included. It used to subtract `days` from the reference and hand
    the result over as the start, which is one too many: with the reference day inside the window as
    well, "the last 30 days" spanned thirty-one dates, and the extra one was the far end — on the
    1st of a month, the whole of the previous month's last day, in a figure labelled as thirty days.

    `reference` and `timezone` are the caller's day and the zone it was measured in, exactly as on
    the query endpoint, and when they arrive they win. This route used to resolve "thirty days ago"
    against *this* deployment's clock and bin the charges in a zone hardcoded one layer down, so a
    holder in the Canaries read the chat on their day and their card charges on Madrid's.
    """
    until = reference or today
    since = until - timedelta(days=days - 1)
    result = await queries.ask(
        ListCardsQuery(
            account_id=account_id,
            since=since,
            until=until,
            reporting_timezone=timezone or zone,
        )
    )
    return http_mapper.cards_to_dto(result)


@accounts_router.get(
    "/{account_id}/cards/{card_id}/charges", summary="What was bought with one card"
)
async def read_card_charges(
    account_id: UUID,
    card_id: UUID,
    queries: Queries,
    today: Today,
    zone: DeploymentZone,
    days: Annotated[int, Query(ge=1, le=MAX_CARD_WINDOW_DAYS)] = 30,
    limit: Annotated[int, Query(ge=1, le=MAX_SUPPORTING_MOVEMENTS)] = 50,
    reference: Reference = None,
    timezone: CallerZone = None,
) -> CardChargesResponse:
    """The charges behind a card's total, newest first.

    A card screen that states a total and cannot show the movements behind it is asking to be
    trusted, which is the objection this product is built against.

    A card belonging to another account answers with an empty list rather than a 404: the view
    filters on the declared account, so the id selects nothing. That is also the honest answer —
    telling a caller that a card exists but is not theirs is telling them something.
    """
    result = await queries.ask(
        ListCardChargesQuery(
            account_id=account_id,
            card_id=card_id,
            since=(reference or today) - timedelta(days=days - 1),
            until=reference or today,
            limit=limit,
            reporting_timezone=timezone or zone,
        )
    )
    return http_mapper.card_charges_to_dto(result)


@accounts_router.get("/{account_id}/holder-accounts", summary="Every account this holder has")
async def read_holder_accounts(account_id: UUID, queries: Queries) -> HoldingsResponse:
    """The declared account, plus the others belonging to the same person.

    Under `/{account_id}/` and not at the root, because the account in the path is what gets
    declared: it is the credential's proof of who is asking, and `my_accounts_v1` widens from it to
    the holder. A route without one would have nothing to declare and therefore no scope.

    Additive by construction. A caller that never asks keeps seeing exactly the account it names,
    which is what every existing read path does.
    """
    return http_mapper.holdings_to_dto(await queries.ask(ListAccountsQuery(account_id)))


@accounts_router.get("/{account_id}/contacts", summary="Everybody this account can pay")
async def read_contacts(account_id: UUID, queries: Queries) -> ContactsResponse:
    """The whole list, through the same restricted role and view as every other read.

    A payment screen cannot work without it: asking somebody to type a name they can only get right
    from a list they have not seen is how money reaches the wrong Marta.
    """
    return http_mapper.contacts_to_dto(await queries.ask(ListContactsQuery(account_id)))


@accounts_router.post("/{account_id}/movements/list", summary="List ledger movements")
async def list_account_movements(
    account_id: UUID, body: MovementListRequest, queries: Queries, today: Today
) -> MovementListResponse:
    """Newest-first paginated ledger rows; no reporting period is required."""
    plan = http_mapper.movement_list_plan_from_dto(body, account_id, today)
    return http_mapper.movement_list_to_dto(await queries.ask(ListMovementsQuery(plan)))


@accounts_router.put(
    "/{account_id}/movements/{transaction_id}/category",
    status_code=204,
    summary="File a movement under a category, or let the bank decide",
)
async def categorise_movement(
    account_id: UUID, transaction_id: UUID, body: CategoryRequest, commands: Commands
) -> Response:
    """The holder's own category, stored beside the bank's rather than over it.

    A `null` category clears the override and falls back to what the bank inferred — the ledger is
    never edited, so there is always something to fall back to.

    204, because the answer is that it happened. Returning the movement would mean reading it back
    through a different path than the one the screen will use anyway.
    """
    await commands.dispatch(
        CategoriseMovementCommand(
            account_id=account_id, transaction_id=transaction_id, category=body.category
        )
    )
    return Response(status_code=204)


@queries_router.get("/categories", summary="The categories this ledger uses")
async def read_categories() -> CategoriesResponse:
    """A closed list, and the same one the planner is constrained to.

    It comes from here because a movement's category is a fact about the ledger. The assistant keeps
    its own copy to build the decoder's schema at startup; this is what a screen should offer, and
    the two disagreeing shows up as a rejected override rather than as a wrong figure.
    """
    return CategoriesResponse(categories=list(constants.CATEGORIES))


@queries_router.post("/ranked", summary="The nth largest movement of a period")
async def post_ranked_movement(
    body: RankedMovementRequest, queries: Queries, today: Today
) -> RankedMovementResponse:
    """One movement, chosen by size (§9.2 does not cover this, and that was the gap).

    Its own route rather than a flag on the query above: the two answers have nothing in common — a
    total with groups against a single row — so one response would carry half its fields null on
    either side.

    A period with fewer movements than the rank comes back with none, and that is not an error: "el
    quinto" of three has no answer, and 404 would say the account does not exist.
    """
    plan = http_mapper.ranked_plan_from_dto(body, today)
    result = await queries.ask(RankedMovementQuery(plan, rank=body.rank))
    return http_mapper.ranked_to_dto(result)


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
