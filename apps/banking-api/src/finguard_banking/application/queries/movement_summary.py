"""MovementSummaryQuery · MovementSummaryResult · MovementSummaryHandler.

Named after what is asked, not after the machinery: `RunFinancialQueryQuery` would carry
the word twice. The plan itself — the validated §9.2 representation — travels inside the
query as a domain object, which is fine: it is input, not an answer.
"""

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from finguard_banking.application.bus.query_bus import Query
from finguard_banking.domain.models.financial_query import FinancialQuery
from finguard_banking.domain.models.financial_query_result import FinancialQueryResult
from finguard_banking.domain.ports.financial_query_repository import FinancialQueryRepository


@dataclass(frozen=True)
class SummaryGroup:
    key: str
    total_minor: int
    movement_count: int


@dataclass(frozen=True)
class SummaryMovement:
    transaction_id: UUID
    occurred_at: datetime
    description: str
    merchant_name: str | None
    category: str | None
    amount_minor: int
    direction: str
    # Whose opinion the category is. Last, with a default, because a dataclass will not take a
    # defaulted field before a required one.
    category_is_custom: bool = False
    category_source: str = "NONE"
    is_internal: bool = False


@dataclass(frozen=True)
class SummaryTrace:
    sql: str
    parameters: list[str]
    elapsed_ms: float


@dataclass(frozen=True)
class MovementSummaryResult:
    account_id: UUID
    currency: str
    total_minor: int
    movement_count: int
    groups: list[SummaryGroup] = field(default_factory=list)
    movements: list[SummaryMovement] = field(default_factory=list)
    trace: SummaryTrace | None = None


@dataclass(frozen=True)
class MovementSummaryQuery(Query[MovementSummaryResult]):
    plan: FinancialQuery


class MovementSummaryHandler:
    """Resolves a financial query.

    The plan arrives already validated by the domain. The check that the caller owns the
    account will go here (§12 of the vision).
    """

    def __init__(self, queries: FinancialQueryRepository) -> None:
        self._queries = queries

    async def handle(self, query: MovementSummaryQuery) -> MovementSummaryResult:
        return _to_result(await self._queries.run(query.plan))


def _to_result(result: FinancialQueryResult) -> MovementSummaryResult:
    return MovementSummaryResult(
        account_id=result.account_id,
        currency=result.currency,
        total_minor=result.total.amount_minor,
        movement_count=result.movement_count,
        groups=[
            SummaryGroup(
                key=group.key,
                total_minor=group.total.amount_minor,
                movement_count=group.movement_count,
            )
            for group in result.groups
        ],
        movements=[
            SummaryMovement(
                transaction_id=movement.transaction_id,
                occurred_at=movement.occurred_at,
                description=movement.description,
                merchant_name=movement.merchant_name,
                category=movement.category,
                category_is_custom=movement.category_is_custom,
                category_source=movement.category_source,
                is_internal=movement.is_internal,
                amount_minor=movement.amount.amount_minor,
                direction=movement.direction,
            )
            for movement in result.supporting_movements
        ],
        trace=SummaryTrace(
            sql=result.trace.sql,
            parameters=result.trace.parameters,
            elapsed_ms=result.trace.elapsed_ms,
        )
        if result.trace is not None
        else None,
    )
