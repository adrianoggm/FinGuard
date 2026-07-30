from __future__ import annotations

import time
from typing import Any

import asyncpg

from finguard_banking.adapters.outbound.postgres.query_compiler import (
    compilar_agregado,
    compilar_movimientos,
)
from finguard_banking.domain.errors import AccountNotFoundError
from finguard_banking.domain.financial_query import FinancialQuery
from finguard_banking.domain.financial_query_result import (
    FinancialQueryResult,
    Movement,
    QueryGroup,
    QueryTrace,
)

# La moneda y la existencia de la cuenta salen de la vista de saldos, no de la tabla
# accounts: el rol de consulta no la alcanza, y es justo lo que tiene que pasar.
_CUENTA_SQL = """
    SELECT b.currency
    FROM assistant_read.account_balances_v1 b
    WHERE b.account_id = $1
"""


class PostgresFinancialQueryRepository:
    def __init__(self, pool: asyncpg.Pool[Any], reporting_timezone: str) -> None:
        self._pool = pool
        self._timezone = reporting_timezone

    async def run(self, query: FinancialQuery) -> FinancialQueryResult:
        agregado = compilar_agregado(query, self._timezone)
        movimientos = compilar_movimientos(query, self._timezone)

        comienzo = time.perf_counter()
        async with self._pool.acquire() as connection:
            moneda = await connection.fetchval(_CUENTA_SQL, query.account_id)
            # Una cuenta que no existe no es una cuenta sin movimientos.
            if moneda is None:
                raise AccountNotFoundError(query.account_id)

            filas = await connection.fetch(agregado.sql, *agregado.parameters)
            justificantes = (
                await connection.fetch(movimientos.sql, *movimientos.parameters)
                if query.supporting_limit
                else []
            )
        transcurrido_ms = (time.perf_counter() - comienzo) * 1000

        grupos = [
            QueryGroup(
                key=str(fila["clave"]),
                total_minor=int(fila["total_minor"]),
                movement_count=int(fila["movement_count"]),
            )
            for fila in filas
        ]

        return FinancialQueryResult(
            account_id=query.account_id,
            currency=moneda,
            # El total sale de los grupos: una sola consulta, un solo recuento.
            total_minor=sum(grupo.total_minor for grupo in grupos),
            movement_count=sum(grupo.movement_count for grupo in grupos),
            groups=grupos,
            supporting_movements=[
                Movement(
                    transaction_id=fila["transaction_id"],
                    occurred_at=fila["occurred_at"],
                    description=fila["description"],
                    merchant_name=fila["merchant_name"],
                    category=fila["category"],
                    amount_minor=int(fila["amount_minor"]),
                    direction=fila["direction"],
                )
                for fila in justificantes
            ],
            trace=QueryTrace(
                sql=" ".join(agregado.sql.split()),
                parameters=[str(valor) for valor in agregado.parameters],
                elapsed_ms=round(transcurrido_ms, 2),
            )
            if query.explain
            else None,
        )
