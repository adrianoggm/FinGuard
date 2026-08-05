from typing import Protocol, runtime_checkable
from uuid import UUID


@runtime_checkable
class MovementCategoryPort(Protocol):
    """The holder's own category for one of their movements.

    `assign` replaces any previous opinion — one per movement per holder — and `clear` removes it,
    which falls back to what the bank inferred rather than to nothing.

    Both raise `MovementNotFoundError` when the account has no entry in that transaction. That check
    is not politeness: without it a caller could file opinions about movements they cannot see, and
    the rows would sit in the table forever joining to nothing.
    """

    async def assign(self, account_id: UUID, transaction_id: UUID, category: str) -> None: ...

    async def clear(self, account_id: UUID, transaction_id: UUID) -> None: ...
