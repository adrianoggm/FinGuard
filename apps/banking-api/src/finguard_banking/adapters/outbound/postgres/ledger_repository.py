from __future__ import annotations

from typing import Any
from uuid import UUID

import asyncpg

from finguard_banking.domain.balance import Balance
from finguard_banking.domain.errors import AccountNotFoundError

# El saldo se deriva de los asientos, no de una columna mutable: el libro mayor es
# la fuente de verdad (ADR-001, vision.md §8.5). La derivación —qué cuenta como
# contabilizado y con qué signo— vive en la vista, no repetida aquí.
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
            # Sin reservas todavía: disponible y contabilizado son lo mismo.
            available_minor=booked_minor,
            booked_minor=booked_minor,
            currency=row["currency"],
            as_of=row["as_of"],
        )
