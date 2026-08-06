"""Fixtures only. The helpers are library code and live in tests/support/."""

from collections.abc import AsyncIterator

import asyncpg
import pytest

from finguard_banking.bootstrap.config import get_settings


@pytest.fixture
async def connection() -> AsyncIterator[asyncpg.Connection]:
    """A connection whose work is undone at the end: tests leave no trace.

    The ledger is immutable, so a test could not delete its own data: the only possible
    cleanup is never committing the transaction.
    """
    conn: asyncpg.Connection = await asyncpg.connect(get_settings().database_url)
    transaction = conn.transaction()
    await transaction.start()
    try:
        yield conn
    finally:
        await transaction.rollback()
        await conn.close()


@pytest.fixture
async def reader_connection() -> AsyncIterator[asyncpg.Connection]:
    """A connection as `finguard_query_reader`, the role the query path actually uses.

    It exists because the isolation tests all went through the read views, which filter
    correctly — and that is exactly how row-level security came to be bypassed for two
    migrations without a single test noticing. The owner is not subject to the policies, by
    design, so asking whether the data is protected means asking as this role.

    Read-only and against committed data: it sees the seed, not another fixture's uncommitted
    work.
    """
    conn: asyncpg.Connection = await asyncpg.connect(get_settings().query_database_url)
    try:
        yield conn
    finally:
        await conn.close()
