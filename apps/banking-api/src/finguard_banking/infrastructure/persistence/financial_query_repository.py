from __future__ import annotations

import time
from typing import Any

import asyncpg

from finguard_banking.domain.errors import AccountNotFoundError
from finguard_banking.domain.models.financial_query import FinancialQuery
from finguard_banking.domain.models.financial_query_result import (
    FinancialQueryResult,
    Movement,
    QueryTrace,
)
from finguard_banking.domain.value_objects.money import Money
from finguard_banking.infrastructure.mappers import row_mapper
from finguard_banking.infrastructure.persistence.query_compiler import (
    compile_aggregate,
    compile_movements,
    compile_ranked,
)

# The account travels in a session variable, and now so does the holder it belongs to.
#
# It is set from the plan and the compiler also filters on the account: the same fact enforced
# twice from two independent places. A compiler that dropped its WHERE would still see only the
# holder's rows, and a session that declared nothing sees none at all — `current_setting(..., true)`
# is NULL, and NULL fails every comparison.
#
# `SET LOCAL` through `set_config(..., true)` scopes both to the transaction, so neither leaks to
# the next caller borrowing the same pooled connection — the property this has always had and the
# reason it is two statements in one transaction rather than a connection setting.
#
# The holder is looked up from the account rather than taken from the caller. That is not a
# widening of trust: the credential already proved this account, and everything it opens is another
# account of the same person. What it avoids is banking's read contract growing a second identifier
# that every caller has to get right, on the same day the views learn to use it.
#
# Through `holder_of`, not a plain SELECT. The query reader has no privilege on any base table —
# that barrier is what actually enforces isolation — so it cannot read `accounts` to find out. The
# function is SECURITY DEFINER with a pinned search_path: one question it may ask, and no others.
_DECLARE_ACCOUNT = "SELECT set_config('app.account_id', $1::text, true)"
_DECLARE_HOLDER = """
    SELECT set_config(
        'app.customer_id',
        COALESCE(assistant_read.holder_of($1::uuid)::text, ''),
        true
    )
"""


async def declare(connection: Any, account_id: Any) -> None:
    """Say whose rows this transaction may see.

    Both settings, always together. `my_accounts_v1` admits the declared account *or* the declared
    holder's accounts, so a session that set only the first would still work and see one account —
    which is exactly the bug this pair exists to make impossible to reintroduce quietly.
    """
    await connection.execute(_DECLARE_ACCOUNT, str(account_id))
    await connection.execute(_DECLARE_HOLDER, str(account_id))


_ACCOUNT_SQL = """
    SELECT b.currency
    FROM assistant_read.account_balances_v1 b
    WHERE b.account_id = $1
"""


class PostgresFinancialQueryRepository:
    def __init__(
        self, pool: asyncpg.Pool[Any], reporting_timezone: str, group_limit: int | None = None
    ) -> None:
        self._pool = pool
        self._timezone = reporting_timezone
        self._group_limit = group_limit

    async def ranked(self, query: FinancialQuery, *, rank: int) -> Movement | None:
        """The nth largest movement of the period, or None when there are fewer than n.

        None rather than the smallest: "el quinto" of three movements has no answer, and returning
        the last one would be a real figure presented as the fifth largest.

        The account is declared exactly as it is in `run`, before anything is read. A new statement
        is a new chance to forget it, so the declaration is part of every one of them rather than
        something the caller is trusted to have done.
        """
        zone = query.reporting_timezone or self._timezone
        compiled = compile_ranked(query, zone, rank=rank)

        async with self._pool.acquire() as connection, connection.transaction():
            # The credential's account, always, and never the one being asked about. `declare`
            # resolves the holder from what it is given, so declaring a narrowed account opens
            # ITS holder's scope — for a stranger's id, a leak wearing the shape of a filter.
            await declare(connection, str(query.account_id))
            currency = await connection.fetchval(_ACCOUNT_SQL, query.scoped_to)
            if currency is None:
                raise AccountNotFoundError(query.scoped_to)
            row = await connection.fetchrow(compiled.sql, *compiled.parameters)

        return row_mapper.to_movement(row, currency) if row is not None else None

    async def run(self, query: FinancialQuery) -> FinancialQueryResult:
        # The caller's zone when it sent one, this deployment's otherwise. Which day a
        # purchase at 23:30 belongs to is decided here, so binning in a zone other than the
        # one the window was chosen in answers a different question with a real figure.
        zone = query.reporting_timezone or self._timezone

        aggregate = (
            compile_aggregate(query, zone, self._group_limit)
            if self._group_limit is not None
            else compile_aggregate(query, zone)
        )
        movements = compile_movements(query, zone)

        started = time.perf_counter()
        async with self._pool.acquire() as connection, connection.transaction():
            # Declared before anything is read. The views filter on it and row-level
            # security enforces it underneath, so this is the one statement that decides
            # whose rows exist for the rest of the transaction.
            await declare(connection, str(query.account_id))
            # The narrowed account when there is one, and it is looked up through the same scoped
            # view: an account that is not this holder's has no currency here, so it answers 404 —
            # the same as one that does not exist, which is all a caller probing should learn.
            currency = await connection.fetchval(_ACCOUNT_SQL, query.scoped_to)
            # An account that does not exist is not an account without movements.
            if currency is None:
                raise AccountNotFoundError(query.scoped_to)

            rows = await connection.fetch(aggregate.sql, *aggregate.parameters)
            supporting = (
                await connection.fetch(movements.sql, *movements.parameters)
                if query.supporting_limit
                else []
            )
        elapsed_ms = (time.perf_counter() - started) * 1000

        groups = [row_mapper.to_group(row, currency) for row in rows]

        return FinancialQueryResult(
            account_id=query.account_id,
            # The total comes from the groups: one query, one count.
            total=Money(sum(group.total.amount_minor for group in groups), currency),
            movement_count=sum(group.movement_count for group in groups),
            groups=groups,
            supporting_movements=[row_mapper.to_movement(row, currency) for row in supporting],
            trace=QueryTrace(
                sql=" ".join(aggregate.sql.split()),
                parameters=[str(value) for value in aggregate.parameters],
                elapsed_ms=round(elapsed_ms, 2),
            )
            if query.explain
            else None,
        )
