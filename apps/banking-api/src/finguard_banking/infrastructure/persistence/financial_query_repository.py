from __future__ import annotations

import time
from typing import Any

import asyncpg

from finguard_banking.domain.errors import AccountNotFoundError
from finguard_banking.domain.financial_query import FinancialQuery
from finguard_banking.domain.financial_query_result import (
    FinancialQueryResult,
    Movement,
    QueryGroup,
    QueryTrace,
)
from finguard_banking.infrastructure.persistence.query_compiler import (
    compile_aggregate,
    compile_movements,
)

# The currency and the existence of the account come from the balances view, not from
# the accounts table: the query role cannot reach it, and that is exactly the point.
_ACCOUNT_SQL = """
    SELECT b.currency
    FROM assistant_read.account_balances_v1 b
    WHERE b.account_id = $1
"""


class PostgresFinancialQueryRepository:
    def __init__(self, pool: asyncpg.Pool[Any], reporting_timezone: str) -> None:
        self._pool = pool
        self._timezone = reporting_timezone

    async def run(self, query: FinancialQuery) -> FinancialQueryResult:
        aggregate = compile_aggregate(query, self._timezone)
        movements = compile_movements(query, self._timezone)

        started = time.perf_counter()
        async with self._pool.acquire() as connection:
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

        groups = [
            QueryGroup(
                key=str(row["key"]),
                total_minor=int(row["total_minor"]),
                movement_count=int(row["movement_count"]),
            )
            for row in rows
        ]

        return FinancialQueryResult(
            account_id=query.account_id,
            currency=currency,
            # The total comes from the groups: one query, one count.
            total_minor=sum(group.total_minor for group in groups),
            movement_count=sum(group.movement_count for group in groups),
            groups=groups,
            supporting_movements=[
                Movement(
                    transaction_id=row["transaction_id"],
                    occurred_at=row["occurred_at"],
                    description=row["description"],
                    merchant_name=row["merchant_name"],
                    category=row["category"],
                    amount_minor=int(row["amount_minor"]),
                    direction=row["direction"],
                )
                for row in supporting
            ],
            trace=QueryTrace(
                sql=" ".join(aggregate.sql.split()),
                parameters=[str(value) for value in aggregate.parameters],
                elapsed_ms=round(elapsed_ms, 2),
            )
            if query.explain
            else None,
        )
