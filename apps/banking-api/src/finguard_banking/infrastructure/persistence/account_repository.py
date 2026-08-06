"""Reads the holder's accounts. Through the views and the restricted role, like every other read.

There is no `WHERE account_id = ...` in the SQL below, and that is not an omission.
`my_accounts_v1` is the one place the scope rule lives (migration 0016) and every read view filters
through it, so a second filter here would be the same rule written twice — which is what 0016
removed.

What decides what this returns is `declare()`, which sets both the account and the holder it
belongs to. It is inside the transaction rather than something the caller is trusted to have done:
a new statement is a new chance to forget it.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import asyncpg

from finguard_banking.domain.enums import AccountKind, AccountStatus
from finguard_banking.domain.models.holding import Holding
from finguard_banking.domain.value_objects.money import Money
from finguard_banking.infrastructure.persistence.financial_query_repository import declare

# The balance comes from `account_balances_v1` rather than being summed here, so the figure in this
# list and the figure the balance endpoint returns are the same query. Two ways to compute a balance
# is two balances, and the one nobody is looking at is the one that drifts.
#
# An inner join is safe: that view is built from `my_accounts_v1` with a LEFT JOIN onto the
# movements, so an account with nothing in it is a row with zero rather than a missing row.
#
# Ordered so a screen and a sentence list them the same way, and spelled out rather than left to
# `ORDER BY kind`: alphabetically that is ASSET, CURRENT, SAVINGS, which puts the investment first.
# Nobody thinks about their money starting from what they cannot spend, and the assistant's own
# sentence already says the current account first — the two disagreeing is a small thing that reads
# as a bug in whichever one you are looking at.
_LIST_SQL = """
    SELECT a.account_id, a.kind, a.display_name, a.status, b.currency, b.booked_minor
    FROM assistant_read.my_accounts_v1 a
    JOIN assistant_read.account_balances_v1 b ON b.account_id = a.account_id
    ORDER BY CASE a.kind WHEN 'CURRENT' THEN 0 WHEN 'SAVINGS' THEN 1 ELSE 2 END,
             a.display_name NULLS LAST
"""


class PostgresAccountRepository:
    """Implements AccountDirectoryPort over the read views."""

    def __init__(self, pool: asyncpg.Pool[Any]) -> None:
        self._pool = pool

    async def all_of(self, account_id: UUID) -> tuple[Holding, ...]:
        """Every account this holder has, the declared one included.

        No limit and no paging. A person has a handful of accounts, and the one that did not fit
        would be the one they were asking about.
        """
        async with self._pool.acquire() as connection, connection.transaction():
            await declare(connection, str(account_id))
            rows = await connection.fetch(_LIST_SQL)
        return tuple(
            Holding(
                account_id=row["account_id"],
                kind=AccountKind(row["kind"]),
                display_name=row["display_name"],
                # `int(...)`, like every other reader of this column. The view's figure is a
                # `SUM()`, which Postgres widens to numeric and asyncpg hands back as a Decimal,
                # and `Money` refuses anything that is not exactly an int. That refusal is the
                # point — it is what keeps a float from ever reaching money — so the conversion
                # belongs here at the boundary rather than being relaxed there.
                balance=Money(int(row["booked_minor"]), row["currency"]),
                status=AccountStatus(row["status"]),
            )
            for row in rows
        )
