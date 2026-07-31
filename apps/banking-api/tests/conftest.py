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
