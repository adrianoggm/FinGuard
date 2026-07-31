from __future__ import annotations

from typing import Any
from uuid import UUID

import asyncpg

from finguard_banking.domain.balance import Balance
from finguard_banking.domain.errors import AccountNotFoundError

# The balance is derived from the entries, never read from a mutable column: the
# ledger is the source of truth (ADR-001, vision.md §8.5). The derivation — what
# counts as booked and with which sign — lives in the view, not duplicated here.
_BALANCE_SQL = """
    SELECT b.currency, b.booked_minor, NOW() AS as_of
    FROM assistant_read.account_balances_v1 b
    WHERE b.account_id = $1
"""


class PostgresLedgerRepository:
    def __init__(self, pool: asyncpg.Pool[Any]) -> None:
        self._pool = pool

    async def balance_of(self, account_id: UUID) -> Balance:
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(_BALANCE_SQL, account_id)

        if row is None:
            raise AccountNotFoundError(account_id)

        booked_minor = int(row["booked_minor"])
        return Balance(
            account_id=account_id,
            # No holds yet: available and booked are the same figure.
            available_minor=booked_minor,
            booked_minor=booked_minor,
            currency=row["currency"],
            as_of=row["as_of"],
        )
