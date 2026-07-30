from __future__ import annotations

from typing import Any

import asyncpg
from fastapi import Request

from finguard_banking.adapters.outbound.postgres.financial_query_repository import (
    PostgresFinancialQueryRepository,
)
from finguard_banking.adapters.outbound.postgres.ledger_repository import (
    PostgresLedgerRepository,
)
from finguard_banking.application.get_balance import GetBalance
from finguard_banking.application.run_financial_query import RunFinancialQuery
from finguard_banking.bootstrap.config import get_settings
from finguard_banking.ports.financial_query_repository import FinancialQueryRepository
from finguard_banking.ports.ledger_repository import LedgerRepository


# asyncpg.Pool solo es genérico para el type checker, así que estas anotaciones no
# pueden evaluarse en tiempo de ejecución: de ahí el `from __future__ import annotations`.
def _pool(request: Request) -> asyncpg.Pool[Any]:
    """Pool del propietario: escrituras y comprobación de estado."""
    pool: asyncpg.Pool[Any] = request.app.state.pool
    return pool


def _query_pool(request: Request) -> asyncpg.Pool[Any]:
    """Pool de solo lectura: todo lo que se consulta pasa por aquí (§8.6)."""
    pool: asyncpg.Pool[Any] = request.app.state.query_pool
    return pool


def get_ledger_repository(request: Request) -> LedgerRepository:
    return PostgresLedgerRepository(_query_pool(request))


def get_balance(request: Request) -> GetBalance:
    return GetBalance(get_ledger_repository(request))


def get_financial_query_repository(request: Request) -> FinancialQueryRepository:
    return PostgresFinancialQueryRepository(_query_pool(request), get_settings().reporting_timezone)


def run_financial_query(request: Request) -> RunFinancialQuery:
    return RunFinancialQuery(get_financial_query_repository(request))


async def database_answers(request: Request) -> bool:
    """Si la base de datos contesta. La capa HTTP no necesita saber cómo se pregunta."""
    try:
        async with _pool(request).acquire() as connection:
            await connection.execute("SELECT 1")
    except (OSError, asyncpg.PostgresError):
        return False
    return True
