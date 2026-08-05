"""Filing one of your own movements under a different category.

A thin command: the decision is the user's, the vocabulary is banking's, and there is nothing here
to plan. It exists as a command rather than a call from the route because it writes, and writes go
through the bus where the audit seam is.
"""

from dataclasses import dataclass
from uuid import UUID

from finguard_orchestrator.application.bus.command_bus import Command
from finguard_orchestrator.domain.ports.movement_categories import MovementCategoryPort


@dataclass(frozen=True)
class CategoriseMovementCommand(Command[None]):
    account_id: UUID
    transaction_id: UUID
    # None clears the override, which falls back to what the bank inferred rather than to nothing.
    category: str | None


class CategoriseMovementHandler:
    def __init__(self, categories: MovementCategoryPort) -> None:
        self._categories = categories

    async def handle(self, command: CategoriseMovementCommand) -> None:
        await self._categories.set(command.account_id, command.transaction_id, command.category)
