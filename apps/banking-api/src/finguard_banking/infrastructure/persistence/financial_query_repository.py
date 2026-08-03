from __future__ import annotations

import time
from typing import Any

import asyncpg

from finguard_banking.domain.errors import AccountNotFoundError
from finguard_banking.domain.models.financial_query import FinancialQuery
from finguard_banking.domain.models.financial_query_result import FinancialQueryResult, QueryTrace
from finguard_banking.domain.value_objects.money import Money
from finguard_banking.infrastructure.mappers import row_mapper
from finguard_banking.infrastructure.persistence.query_compiler import (
    compile_aggregate,
    compile_movements,
)

# The currency and the existence of the account come from the balances view, not from
# the accounts table: the query role cannot reach it, and that is exactly the point.
# The account the session may read. `SET LOCAL` scopes it to the transaction, so it cannot
# leak to the next caller borrowing this pooled connection — a plain SET would.
#
# It is set from the plan and the compiler also filters on it: the same fact enforced twice
# from two independent places, which is the whole point. A compiler that dropped its WHERE
# would still see nothing, and a session that forgot this sees nothing either, because
# current_setting(..., true) is NULL and NULL fails every comparison.
_DECLARE_ACCOUNT = "SELECT set_config('app.account_id', $1::text, true)"

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
            await connection.execute(_DECLARE_ACCOUNT, str(query.account_id))
            currency = await connection.fetchval(_ACCOUNT_SQL, query.account_id)
            # An account that does not exist is not an account without movements.
            if currency is None:
                raise AccountNotFoundError(query.account_id)

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
