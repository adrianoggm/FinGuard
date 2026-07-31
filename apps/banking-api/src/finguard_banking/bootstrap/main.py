from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import asyncpg
from fastapi import FastAPI

from finguard_banking.bootstrap.config import get_settings
from finguard_banking.infrastructure import error_handlers
from finguard_banking.infrastructure.controllers.banking_controller import (
    accounts_router,
    health_router,
    queries_router,
)

DESCRIPTION = """
Deterministic financial core of FinGuard. No AI.

The balance is **derived from the ledger entries**, never read from a mutable column,
and every figure comes back with the movements that back it. A financial query is a
validated plan (§9.2) compiled into parameterised SQL — SQL produced by an LLM is never
executed.

Everything that is queried goes through a read-only role that reaches two views and
nothing else.
"""


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Two pools with different roles (vision.md §8.6): the owner writes and checks
    # health, a read-only one serves everything that is queried. The read path being
    # unable to write does not depend on this code being correct.
    #
    # ponytail: if the database is down, startup fails and the error shows up at once.
    # To start without a database, move pool creation to /ready.
    settings = get_settings()
    pool: asyncpg.Pool[Any] = await asyncpg.create_pool(
        settings.database_url, min_size=1, max_size=5
    )
    query_pool: asyncpg.Pool[Any] = await asyncpg.create_pool(
        settings.query_database_url, min_size=1, max_size=5
    )
    app.state.pool = pool
    app.state.query_pool = query_pool
    try:
        yield
    finally:
        await query_pool.close()
        await pool.close()


def create_app() -> FastAPI:
    """Composition root: the only place that wires concrete implementations."""
    app = FastAPI(
        title="FinGuard Banking API",
        version="0.1.0",
        description=DESCRIPTION,
        summary="Deterministic financial core",
        openapi_tags=[
            {"name": "health", "description": "Liveness and readiness."},
            {"name": "accounts", "description": "Accounts and balances (§13.1)."},
            {"name": "queries", "description": "Financial queries over the ledger (§13.2)."},
        ],
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    app.include_router(health_router)
    app.include_router(accounts_router)
    app.include_router(queries_router)
    error_handlers.register(app)
    return app


app = create_app()
