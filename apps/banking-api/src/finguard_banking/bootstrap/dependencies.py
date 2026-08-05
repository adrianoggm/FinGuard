from __future__ import annotations

from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import asyncpg
from fastapi import Request

from finguard_banking.application import CommandBus, QueryBus
from finguard_banking.bootstrap.config import get_settings


# asyncpg.Pool is only generic for the type checker, so these annotations cannot be
# evaluated at runtime: hence `from __future__ import annotations`.
def _pool(request: Request) -> asyncpg.Pool[Any]:
    """Owner pool: writes and health checks."""
    pool: asyncpg.Pool[Any] = request.app.state.pool
    return pool


def get_query_bus(request: Request) -> QueryBus:
    """Controllers ask the bus, never a handler or a repository directly."""
    bus: QueryBus = request.app.state.query_bus
    return bus


def get_command_bus(request: Request) -> CommandBus:
    bus: CommandBus = request.app.state.command_bus
    return bus


def reporting_zone(request: Request) -> str:
    """The zone this deployment measures days in, used only when a caller did not say.

    Beside `reporting_today` because the two are one setting read two ways, and a route that took
    one without the other would resolve a date in one zone and bin timestamps in another.
    """
    return get_settings().reporting_timezone


def reporting_today(request: Request) -> date:
    """What day it is in the reporting time zone.

    The clock lives at the edge: the domain resolves periods with a pure function and
    never reads the time, so its results do not depend on when they run.
    """
    return datetime.now(ZoneInfo(reporting_zone(request))).date()


async def database_answers(request: Request) -> bool:
    """Whether the database answers. The HTTP layer need not know how we ask."""
    try:
        async with _pool(request).acquire() as connection:
            await connection.execute("SELECT 1")
    except (OSError, asyncpg.PostgresError):
        return False
    return True
