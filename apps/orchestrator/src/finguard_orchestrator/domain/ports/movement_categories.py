from typing import Protocol, runtime_checkable
from uuid import UUID


@runtime_checkable
class MovementCategoryPort(Protocol):
    """The holder's own category for one of their movements.

    One method for both directions: `None` means "let the bank decide", which is a choice rather
    than an absence — the ledger keeps what it inferred, so there is always something to fall back
    to.
    """

    async def set(self, account_id: UUID, transaction_id: UUID, category: str | None) -> None: ...
