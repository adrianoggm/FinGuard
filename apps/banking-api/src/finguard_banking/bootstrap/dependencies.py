from __future__ import annotations

from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

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


# asyncpg.Pool is only generic for the type checker, so these annotations cannot be
# evaluated at runtime: hence `from __future__ import annotations`.
def _pool(request: Request) -> asyncpg.Pool[Any]:
    """Owner pool: writes and health checks."""
    pool: asyncpg.Pool[Any] = request.app.state.pool
    return pool


def _query_pool(request: Request) -> asyncpg.Pool[Any]:
    """Read-only pool: everything that is queried goes through here (§8.6)."""
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


def reporting_today(request: Request) -> date:
    """What day it is in the reporting time zone.

    The clock lives at the edge: the domain resolves periods with a pure function and
    never reads the time, so its results do not depend on when they run.
    """
    return datetime.now(ZoneInfo(get_settings().reporting_timezone)).date()


async def database_answers(request: Request) -> bool:
    """Whether the database answers. The HTTP layer need not know how we ask."""
    try:
        async with _pool(request).acquire() as connection:
            await connection.execute("SELECT 1")
    except (OSError, asyncpg.PostgresError):
        return False
    return True
