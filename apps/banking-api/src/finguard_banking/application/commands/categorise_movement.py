"""Assigning and clearing the holder's category for a movement.

Commands rather than queries: they write. `category=None` is the clear, which keeps one command for
one decision — "what should this movement be filed under, including 'whatever the bank thinks'".

The category is checked against the ledger's own list here rather than in the route, because it is
a domain rule: an override the assistant could never name back to the user is a label that works on
one screen and nowhere else.
"""

from dataclasses import dataclass
from uuid import UUID

from finguard_banking.application.bus.command_bus import Command
from finguard_banking.domain.constants import CATEGORIES
from finguard_banking.domain.errors import UnknownCategoryError
from finguard_banking.domain.ports.movement_categories import MovementCategoryPort


@dataclass(frozen=True)
class CategoriseMovementCommand(Command[None]):
    account_id: UUID
    transaction_id: UUID
    # None clears the override and falls back to what the bank inferred.
    category: str | None


class CategoriseMovementHandler:
    def __init__(self, categories: MovementCategoryPort) -> None:
        self._categories = categories

    async def handle(self, command: CategoriseMovementCommand) -> None:
        if command.category is None:
            await self._categories.clear(command.account_id, command.transaction_id)
            return
        if command.category not in CATEGORIES:
            raise UnknownCategoryError(command.category)
        await self._categories.assign(command.account_id, command.transaction_id, command.category)
