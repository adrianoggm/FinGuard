"""Domain ↔ DTO. The only place that knows both shapes.

Money is flattened back into `amountMinor` + `currency` on the way out because that is
what the §13 contract says. The pair only exists on the wire: inside, the currency
travels with the figure.
"""

from datetime import date

from finguard_banking.domain.balance import Balance
from finguard_banking.domain.financial_query import FinancialQuery
from finguard_banking.domain.financial_query_result import (
    FinancialQueryResult,
    Movement,
    QueryGroup,
    QueryTrace,
)
from finguard_banking.domain.relative_period import RelativePeriod, resolve
from finguard_banking.domain.value_objects.date_range import DateRange
from finguard_banking.infrastructure.dto.banking_dto import (
    BalanceResponse,
    FinancialQueryRequest,
    FinancialQueryResponse,
    GroupResponse,
    MovementResponse,
    TraceResponse,
)


def query_from_dto(request: FinancialQueryRequest, today: date) -> FinancialQuery:
    """Builds the plan. A relative period is resolved here, at the edge, because this
    is where the clock lives — the domain only ever sees absolute dates."""
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
        categories=request.categories,
        group_by=request.group_by,
        supporting_limit=request.supporting_limit,
        explain=request.explain,
    )


def balance_to_dto(balance: Balance) -> BalanceResponse:
    return BalanceResponse(
        account_id=balance.account_id,
        available_minor=balance.available.amount_minor,
        booked_minor=balance.booked.amount_minor,
        currency=balance.currency,
        as_of=balance.as_of,
    )


def _group_to_dto(group: QueryGroup) -> GroupResponse:
    return GroupResponse(
        key=group.key,
        total_minor=group.total.amount_minor,
        movement_count=group.movement_count,
    )


def _movement_to_dto(movement: Movement) -> MovementResponse:
    return MovementResponse(
        transaction_id=movement.transaction_id,
        occurred_at=movement.occurred_at,
        description=movement.description,
        merchant_name=movement.merchant_name,
        category=movement.category,
        amount_minor=movement.amount.amount_minor,
        direction=movement.direction,
    )


def _trace_to_dto(trace: QueryTrace) -> TraceResponse:
    return TraceResponse(sql=trace.sql, parameters=trace.parameters, elapsed_ms=trace.elapsed_ms)


def result_to_dto(result: FinancialQueryResult) -> FinancialQueryResponse:
    return FinancialQueryResponse(
        account_id=result.account_id,
        currency=result.currency,
        total_minor=result.total.amount_minor,
        movement_count=result.movement_count,
        groups=[_group_to_dto(group) for group in result.groups],
        supporting_movements=[_movement_to_dto(m) for m in result.supporting_movements],
        trace=_trace_to_dto(result.trace) if result.trace is not None else None,
    )
