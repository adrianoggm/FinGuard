from typing import Protocol

from finguard_banking.domain.models.movement_list import MovementListPage, MovementListPlan


class MovementListPort(Protocol):
    async def list(self, plan: MovementListPlan) -> MovementListPage: ...
