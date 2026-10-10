"""A paginated movement-list use case, separate from the aggregate query use case."""

from dataclasses import dataclass

from finguard_banking.application.bus.query_bus import Query
from finguard_banking.domain.models.movement_list import MovementListPage, MovementListPlan
from finguard_banking.domain.ports.movement_list import MovementListPort


@dataclass(frozen=True)
class ListMovementsQuery(Query[MovementListPage]):
    plan: MovementListPlan


class ListMovementsHandler:
    def __init__(self, movements: MovementListPort) -> None:
        self._movements = movements

    async def handle(self, query: ListMovementsQuery) -> MovementListPage:
        return await self._movements.list(query.plan)
