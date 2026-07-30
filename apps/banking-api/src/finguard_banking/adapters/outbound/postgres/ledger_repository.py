from __future__ import annotations

from typing import Any
from uuid import UUID

import asyncpg

from finguard_banking.domain.balance import Balance
from finguard_banking.domain.errors import AccountNotFoundError

# El saldo se deriva de los asientos, no de una columna mutable: el libro mayor es
# la fuente de verdad (ADR-001, vision.md §8.5).
#
# Signo desde el punto de vista del titular: un DEBIT sobre su cuenta le quita
# dinero y un CREDIT se lo da. Es el mismo criterio del ejemplo de Bizum del spec,
# donde enviar 25 € se anota como débito en la cuenta del usuario.
#
# Solo cuentan los asientos de transacciones BOOKED; lo demás no ha ocurrido aún.
_BALANCE_SQL = """
    SELECT
        a.currency,
        NOW() AS as_of,
        COALESCE(SUM(
            CASE WHEN asientos.entry_type = 'CREDIT' THEN asientos.amount_minor
                 ELSE -asientos.amount_minor END
        ), 0) AS booked_minor
    FROM accounts a
    LEFT JOIN (
        SELECT e.account_id, e.entry_type, e.amount_minor
        FROM ledger_entries e
        JOIN ledger_transactions t ON t.id = e.transaction_id
        WHERE t.status = 'BOOKED'
    ) asientos ON asientos.account_id = a.id
    WHERE a.id = $1
    GROUP BY a.currency
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
