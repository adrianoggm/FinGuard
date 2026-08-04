"""RankedMovementQuery · Result · Handler — the nth largest movement of a period.

A use case of its own rather than a wider `Metric`, which is the distinction that decides the shape:
a maximum is one row with an identity, a date and a shop, while an aggregate is a total with groups.
Sharing one query for both would mean a result whose half is null on either side.

Thin, like the summary handler beside it: the plan arrives validated, the repository knows the SQL,
and the only judgment here is that a rank past the end of the period is nothing rather than the last
row.
"""

from dataclasses import dataclass
from uuid import UUID

from finguard_banking.application.bus.query_bus import Query
from finguard_banking.application.queries.movement_summary import SummaryMovement
from finguard_banking.domain.models.financial_query import FinancialQuery
from finguard_banking.domain.ports.financial_query_repository import FinancialQueryRepository


@dataclass(frozen=True)
class RankedMovementResult:
    account_id: UUID
    rank: int
    # None when the period holds fewer movements than the rank asked for. "El quinto" of three has
    # no answer, and returning the smallest would be a real figure presented as the fifth largest.
    #
    # Flat, like every other Result piece: no domain object leaves this layer, so the amount arrives
    # as minor units beside its direction rather than as a Money.
    movement: SummaryMovement | None = None
    # What the amount is denominated in. It travels beside the figure for the same reason the
    # aggregate's does: the caller was writing "EUR" next to every ranked movement because nothing
    # told it otherwise, so an account in USD read as euros. None when there is no movement, since
    # there is then no amount to denominate.
    currency: str | None = None


@dataclass(frozen=True)
class RankedMovementQuery(Query[RankedMovementResult]):
    plan: FinancialQuery
    rank: int = 1


class RankedMovementHandler:
    def __init__(self, queries: FinancialQueryRepository) -> None:
        self._queries = queries

    async def handle(self, query: RankedMovementQuery) -> RankedMovementResult:
        found = await self._queries.ranked(query.plan, rank=query.rank)
        return RankedMovementResult(
            account_id=query.plan.account_id,
            rank=query.rank,
            currency=found.amount.currency if found is not None else None,
            movement=SummaryMovement(
                transaction_id=found.transaction_id,
                occurred_at=found.occurred_at,
                description=found.description,
                merchant_name=found.merchant_name,
                category=found.category,
                amount_minor=found.amount.amount_minor,
                direction=found.direction,
                category_is_custom=found.category_is_custom,
            )
            if found is not None
            else None,
        )
