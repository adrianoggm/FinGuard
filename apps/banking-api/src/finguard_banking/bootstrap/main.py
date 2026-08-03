from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import asyncpg
from fastapi import FastAPI

from finguard_banking.application import (
    CancelPaymentCommand,
    CancelPaymentHandler,
    CommandBus,
    ConfirmPaymentCommand,
    ConfirmPaymentHandler,
    GetBalanceHandler,
    GetBalanceQuery,
    ListContactsHandler,
    ListContactsQuery,
    MovementSummaryHandler,
    MovementSummaryQuery,
    PreparePaymentCommand,
    PreparePaymentHandler,
    QueryBus,
)
from finguard_banking.bootstrap.config import get_settings
from finguard_banking.infrastructure import (
    PostgresAuditLog,
    PostgresContactRepository,
    PostgresFinancialQueryRepository,
    PostgresLedgerRepository,
    PostgresPaymentRepository,
    accounts_router,
    health_router,
    payments_router,
    queries_router,
    register_error_handlers,
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

    # Handlers are wired once the pools exist: a handler holds its repository, and the
    # repository holds the pool it reads from.
    ledger = PostgresLedgerRepository(query_pool)
    financial_queries = PostgresFinancialQueryRepository(
        query_pool, settings.reporting_timezone, settings.groups_limit
    )
    app.state.query_bus.register(GetBalanceQuery, GetBalanceHandler(ledger))
    app.state.query_bus.register(MovementSummaryQuery, MovementSummaryHandler(financial_queries))

    # Payments write, so they hold the owner pool. Contacts are resolved through it too:
    # the read-only role reaches the views, and the payment path needs the contact row it
    # is about to point a ledger movement at.
    contacts = PostgresContactRepository(pool)
    # The contact list is read through the restricted role, like every other read: the
    # payment path needs the owner pool, and showing a list does not.
    app.state.query_bus.register(
        ListContactsQuery, ListContactsHandler(PostgresContactRepository(query_pool))
    )
    payments = PostgresPaymentRepository(
        pool,
        clearing_reference=settings.clearing_account_reference,
        validity_seconds=settings.payment_validity_seconds,
    )
    # Rebuilt here rather than in create_app: the trail needs the pool, and every command is
    # audited by the bus so no handler can be the one that forgets.
    app.state.command_bus = bus = CommandBus(audit=PostgresAuditLog(pool))
    bus.register(PreparePaymentCommand, PreparePaymentHandler(contacts, payments))
    bus.register(ConfirmPaymentCommand, ConfirmPaymentHandler(payments))
    bus.register(CancelPaymentCommand, CancelPaymentHandler(payments))

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
            {"name": "payments", "description": "Payments: prepare, then confirm (§4.1)."},
        ],
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    # The buses exist before startup so tests can register a fake handler without
    # bringing a database up.
    app.state.query_bus = QueryBus()
    app.state.command_bus = CommandBus()

    app.include_router(health_router)
    app.include_router(accounts_router)
    app.include_router(queries_router)
    app.include_router(payments_router)
    register_error_handlers(app)
    return app


app = create_app()
