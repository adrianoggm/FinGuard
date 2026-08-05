"""Writes the holder's category for a movement. The ledger itself is not touched.

Runs as the owner, like every write. What the bank inferred stays in the transaction's metadata;
this table holds the opinion beside it, and `transactions_v1` prefers the opinion when there is one.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import asyncpg

from finguard_banking.domain.errors import MovementNotFoundError

# Does this account have an entry in that transaction? A movement somebody cannot see is a movement
# they cannot label, and the answer is "no movement" rather than "not yours" — a caller who could
# tell those apart could enumerate other people's movements one identifier at a time.
_MINE = """
    SELECT 1 FROM ledger_entries WHERE transaction_id = $1 AND account_id = $2 LIMIT 1
"""

_ASSIGN = """
    INSERT INTO movement_categories (account_id, transaction_id, category)
    VALUES ($1, $2, $3)
    ON CONFLICT (account_id, transaction_id)
        DO UPDATE SET category = EXCLUDED.category, assigned_at = NOW()
"""

_CLEAR = "DELETE FROM movement_categories WHERE account_id = $1 AND transaction_id = $2"


class PostgresMovementCategoryRepository:
    """Implements MovementCategoryPort over PostgreSQL."""

    def __init__(self, pool: asyncpg.Pool[Any]) -> None:
        self._pool = pool

    async def assign(self, account_id: UUID, transaction_id: UUID, category: str) -> None:
        async with self._pool.acquire() as connection, connection.transaction():
            await self._must_be_mine(connection, account_id, transaction_id)
            await connection.execute(_ASSIGN, account_id, transaction_id, category)

    async def clear(self, account_id: UUID, transaction_id: UUID) -> None:
        """Removing an opinion falls back to what the bank inferred, not to nothing.

        A movement that never had an override is not an error to clear: the request said "let the
        bank decide", and that is already true.
        """
        async with self._pool.acquire() as connection, connection.transaction():
            await self._must_be_mine(connection, account_id, transaction_id)
            await connection.execute(_CLEAR, account_id, transaction_id)

    async def _must_be_mine(self, connection: Any, account_id: UUID, transaction_id: UUID) -> None:
        if await connection.fetchval(_MINE, transaction_id, account_id) is None:
            raise MovementNotFoundError(transaction_id)
