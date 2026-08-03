"""Result ↔ DTO, plus DTO → plan. The only place that knows both shapes.

Queries answer with Results, never with domain models, so what arrives here is already
flat. What this adds is the wire format: camelCase, `from`/`to`, and the `amountMinor` +
`currency` pair the §13 contract asks for.
"""

from datetime import date

from finguard_banking.application import (
    GetBalanceResult,
    MovementSummaryResult,
    SummaryGroup,
    SummaryMovement,
    SummaryTrace,
)
from finguard_banking.domain.models.financial_query import FinancialQuery
from finguard_banking.domain.value_objects.category import Category
from finguard_banking.domain.value_objects.date_range import DateRange
from finguard_banking.domain.value_objects.relative_period import RelativePeriod, resolve
from finguard_banking.infrastructure.dto.banking_dto import (
    BalanceResponse,
    FinancialQueryRequest,
    FinancialQueryResponse,
    GroupResponse,
    MovementResponse,
    TraceResponse,
)


def plan_from_dto(request: FinancialQueryRequest, today: date) -> FinancialQuery:
    """Builds the plan. A relative period is resolved here, at the edge, because this is
    where the clock lives — the domain only ever sees absolute dates.

    `today` is this service's own idea of the day, used only when the caller did not send a
    reference. When it did, the caller's date and zone travel together and win: the window
    a figure is about has to be the one whoever asked was thinking of.
    """
    zone = request.period.timezone if request.period is not None else None
    if request.date_range is not None:
        date_range = DateRange(start=request.date_range.start, end=request.date_range.end)
    elif request.period is not None:
        relative = RelativePeriod(
            period=request.period.period,
            n=request.period.n,
            reference=request.period.reference,
        )
        date_range = resolve(relative, today)
    else:  # pragma: no cover - prevented by the DTO validator
        raise ValueError("missing period")

    return FinancialQuery(
        account_id=request.account_id,
        date_range=date_range,
        metric=request.metric,
        direction=request.direction,
        categories=tuple(Category(value) for value in request.categories),
        merchants=tuple(request.merchants),
        group_by=request.group_by,
        supporting_limit=request.supporting_limit,
        explain=request.explain,
        reporting_timezone=zone,
    )


def balance_to_dto(result: GetBalanceResult) -> BalanceResponse:
    return BalanceResponse(
        account_id=result.account_id,
        available_minor=result.available_minor,
        booked_minor=result.booked_minor,
        currency=result.currency,
        as_of=result.as_of,
    )


def _group_to_dto(group: SummaryGroup) -> GroupResponse:
    return GroupResponse(
        key=group.key, total_minor=group.total_minor, movement_count=group.movement_count
    )


def _movement_to_dto(movement: SummaryMovement) -> MovementResponse:
    return MovementResponse(
        transaction_id=movement.transaction_id,
        occurred_at=movement.occurred_at,
        description=movement.description,
        merchant_name=movement.merchant_name,
        category=movement.category,
        amount_minor=movement.amount_minor,
        direction=movement.direction,
    )


def _trace_to_dto(trace: SummaryTrace) -> TraceResponse:
    return TraceResponse(sql=trace.sql, parameters=trace.parameters, elapsed_ms=trace.elapsed_ms)


def summary_to_dto(result: MovementSummaryResult) -> FinancialQueryResponse:
    return FinancialQueryResponse(
        account_id=result.account_id,
        currency=result.currency,
        total_minor=result.total_minor,
        movement_count=result.movement_count,
        groups=[_group_to_dto(group) for group in result.groups],
        supporting_movements=[_movement_to_dto(m) for m in result.movements],
        trace=_trace_to_dto(result.trace) if result.trace is not None else None,
    )
