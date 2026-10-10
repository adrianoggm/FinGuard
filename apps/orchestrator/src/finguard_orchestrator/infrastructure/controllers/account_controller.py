"""Deterministic banking screens, separate from conversational orchestration."""

from datetime import date, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response

from finguard_orchestrator.application import (
    CategoriseMovementCommand,
    CommandBus,
    GetAccountSummaryQuery,
    GetBreakdownQuery,
    GetCardChargesQuery,
    GetMovementsQuery,
    ListCardsQuery,
    ListContactsQuery,
    QueryBus,
)
from finguard_orchestrator.application.queries.browse_account import MAX_DAYS, MAX_MOVEMENTS
from finguard_orchestrator.bootstrap.dependencies import (
    get_command_bus,
    get_query_bus,
    known_categories,
    questions_enabled,
    reporting_today,
    requires,
)
from finguard_orchestrator.domain.enums import GroupBy
from finguard_orchestrator.domain.errors import InvalidValueError
from finguard_orchestrator.domain.value_objects.caller import Caller
from finguard_orchestrator.domain.value_objects.scope import Scope
from finguard_orchestrator.infrastructure.dto.llm_dto import (
    AccountSummaryResponse,
    BreakdownResponse,
    CategoriesResponse,
    CategoryRequest,
    ChargesResponse,
    ContactsResponse,
    MovementsResponse,
    WalletResponse,
)
from finguard_orchestrator.infrastructure.mappers import llm_mapper

router = APIRouter(tags=["assistant"])

Queries = Annotated[QueryBus, Depends(get_query_bus)]
Commands = Annotated[CommandBus, Depends(get_command_bus)]
Today = Annotated[date, Depends(reporting_today)]
Who = Annotated[Caller, Depends(requires(Scope.QUESTIONS_READ))]
Categories = Annotated[tuple[str, ...], Depends(known_categories)]


@router.get(
    "/v1/account/summary",
    summary="The account as a screen needs it",
    dependencies=[Depends(questions_enabled)],
)
async def account_summary(who: Who, queries: Queries, today: Today) -> AccountSummaryResponse:
    result = await queries.ask(
        GetAccountSummaryQuery(
            account_id=who.account_id, today=today, reporting_timezone=who.reporting_timezone
        )
    )
    return llm_mapper.summary_to_dto(result)


@router.get(
    "/v1/account/movements",
    summary="The account's movements, optionally narrowed",
    dependencies=[Depends(questions_enabled)],
)
async def account_movements(
    who: Who,
    queries: Queries,
    today: Today,
    days: Annotated[int, Query(ge=1, le=MAX_DAYS)] = 90,
    limit: Annotated[int, Query(ge=1, le=MAX_MOVEMENTS)] = 50,
    category: Annotated[str | None, Query(max_length=40)] = None,
    include_internal: Annotated[bool, Query()] = False,
    account: Annotated[UUID | None, Query()] = None,
    across_accounts: Annotated[bool, Query()] = False,
    merchant: Annotated[str | None, Query(max_length=140)] = None,
) -> MovementsResponse:
    result = await queries.ask(
        GetMovementsQuery(
            account_id=who.account_id,
            today=today,
            reporting_timezone=who.reporting_timezone,
            days=days,
            limit=limit,
            category=category,
            merchant=merchant,
            include_internal=include_internal,
            only_account_id=account,
            across_accounts=across_accounts,
        )
    )
    return llm_mapper.movements_to_dto(result)


@router.get(
    "/v1/account/breakdown",
    summary="Where the money went, grouped",
    dependencies=[Depends(questions_enabled)],
)
async def account_breakdown(
    who: Who,
    queries: Queries,
    today: Today,
    by: GroupBy = GroupBy.CATEGORY,
    days: Annotated[int, Query(ge=1, le=MAX_DAYS)] = 30,
) -> BreakdownResponse:
    if by is GroupBy.NONE:
        raise InvalidValueError("a breakdown needs something to group by")
    result = await queries.ask(
        GetBreakdownQuery(
            account_id=who.account_id,
            today=today,
            reporting_timezone=who.reporting_timezone,
            days=days,
            group_by=by,
        )
    )
    return llm_mapper.breakdown_to_dto(result)


@router.get(
    "/v1/cards",
    summary="The cards this account holds",
    dependencies=[Depends(questions_enabled)],
)
async def cards(
    who: Who,
    queries: Queries,
    today: Today,
    days: Annotated[int, Query(ge=1, le=MAX_DAYS)] = 30,
) -> WalletResponse:
    result = await queries.ask(
        ListCardsQuery(
            account_id=who.account_id,
            since=today - timedelta(days=days - 1),
            today=today,
            reporting_timezone=who.reporting_timezone,
        )
    )
    return llm_mapper.wallet_to_dto(result)


@router.get(
    "/v1/cards/{card_id}/charges",
    summary="What was bought with one card",
    dependencies=[Depends(questions_enabled)],
)
async def card_charges(
    card_id: UUID,
    who: Who,
    queries: Queries,
    today: Today,
    days: Annotated[int, Query(ge=1, le=MAX_DAYS)] = 30,
    limit: Annotated[int, Query(ge=1, le=MAX_MOVEMENTS)] = 50,
) -> ChargesResponse:
    result = await queries.ask(
        GetCardChargesQuery(
            account_id=who.account_id,
            card_id=card_id,
            since=today - timedelta(days=days - 1),
            today=today,
            reporting_timezone=who.reporting_timezone,
            limit=limit,
        )
    )
    return llm_mapper.charges_to_dto(result)


@router.get(
    "/v1/categories",
    summary="The categories a movement can be filed under",
    dependencies=[Depends(questions_enabled)],
)
async def categories(who: Who, known: Categories) -> CategoriesResponse:
    return CategoriesResponse(categories=list(known))


@router.put(
    "/v1/movements/{transaction_id}/category",
    status_code=204,
    summary="File a movement under a category, or let the bank decide",
    dependencies=[Depends(questions_enabled), Depends(requires(Scope.MOVEMENTS_CATEGORISE))],
)
async def categorise(
    transaction_id: UUID, body: CategoryRequest, who: Who, commands: Commands
) -> Response:
    await commands.dispatch(
        CategoriseMovementCommand(
            account_id=who.account_id, transaction_id=transaction_id, category=body.category
        )
    )
    return Response(status_code=204)


@router.get("/v1/contacts", summary="Everybody this account can pay")
async def contacts(who: Who, queries: Queries) -> ContactsResponse:
    return llm_mapper.contacts_to_dto(await queries.ask(ListContactsQuery(who.account_id)))
