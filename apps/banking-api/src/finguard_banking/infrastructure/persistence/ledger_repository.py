from __future__ import annotations

from typing import Any
from uuid import UUID

import asyncpg

from finguard_banking.domain.errors import AccountNotFoundError
from finguard_banking.domain.models.balance import Balance
from finguard_banking.infrastructure.mappers import row_mapper

# The balance is derived from the entries, never read from a mutable column: the ledger
# is the source of truth (ADR-001, vision.md §8.5). The derivation — what counts as
# booked and with which sign — lives in the view, not duplicated here.
# The account the session may read. `SET LOCAL` scopes it to the transaction, so it cannot
# leak to the next caller borrowing this pooled connection — a plain SET would.
#
# It is set from the plan and the compiler also filters on it: the same fact enforced twice
# from two independent places, which is the whole point. A compiler that dropped its WHERE
# would still see nothing, and a session that forgot this sees nothing either, because
# current_setting(..., true) is NULL and NULL fails every comparison.
_DECLARE_ACCOUNT = "SELECT set_config('app.account_id', $1::text, true)"

_BALANCE_SQL = """
    SELECT b.currency, b.booked_minor, NOW() AS as_of
    FROM assistant_read.account_balances_v1 b
    WHERE b.account_id = $1
"""


class PostgresLedgerRepository:
    def __init__(self, pool: asyncpg.Pool[Any]) -> None:
        self._pool = pool

    async def balance_of(self, account_id: UUID) -> Balance:
        async with self._pool.acquire() as connection, connection.transaction():
            await connection.execute(_DECLARE_ACCOUNT, str(account_id))
            row = await connection.fetchrow(_BALANCE_SQL, account_id)

        if row is None:
            raise AccountNotFoundError(account_id)

        return row_mapper.to_balance(account_id, row)
