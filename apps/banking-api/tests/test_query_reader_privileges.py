"""The §8.6 barrier: what the query role CANNOT do.

These checks are not about application code, they are about PostgreSQL privileges.
If the query compiler had a hole, this is what would still be standing.
"""

from collections.abc import AsyncIterator

import asyncpg
import pytest

from finguard_banking.bootstrap.config import get_settings

pytestmark = pytest.mark.integration

TABLES = ["ledger_entries", "ledger_transactions", "accounts", "customers"]


@pytest.fixture
async def reader() -> AsyncIterator[asyncpg.Connection]:
    conn: asyncpg.Connection = await asyncpg.connect(get_settings().query_database_url)
    try:
        yield conn
    finally:
        await conn.close()


@pytest.fixture
async def owner() -> AsyncIterator[asyncpg.Connection]:
    conn: asyncpg.Connection = await asyncpg.connect(get_settings().database_url)
    try:
        yield conn
    finally:
        await conn.close()


async def test_it_can_read_the_authorised_views(reader: asyncpg.Connection) -> None:
    await reader.fetchval("SELECT COUNT(*) FROM assistant_read.transactions_v1")
    await reader.fetchval("SELECT COUNT(*) FROM assistant_read.account_balances_v1")


@pytest.mark.parametrize("table", TABLES)
async def test_the_tables_exist_but_the_reader_cannot_see_them(
    reader: asyncpg.Connection, owner: asyncpg.Connection, table: str
) -> None:
    # For the owner the table is there...
    assert await owner.fetchval(
        "SELECT COUNT(*) FROM pg_tables WHERE schemaname = 'public' AND tablename = $1", table
    )

    # ...and for the reader it does not even exist. Without USAGE on the public
    # schema it cannot confirm which tables are there: stronger than a denied
    # permission.
    with pytest.raises(asyncpg.UndefinedTableError):
        await reader.fetchval(f"SELECT COUNT(*) FROM {table}")  # noqa: S608


async def test_it_cannot_write_even_where_it_can_read(reader: asyncpg.Connection) -> None:
    # The views are all it reaches, and its session is read-only.
    with pytest.raises(
        (
            asyncpg.ReadOnlySQLTransactionError,
            asyncpg.InsufficientPrivilegeError,
            asyncpg.ErrorInAssignmentError,
        )
    ):
        await reader.execute("CREATE TEMP TABLE attempt (id int)")


async def test_its_session_is_read_only(reader: asyncpg.Connection) -> None:
    assert await reader.fetchval("SHOW default_transaction_read_only") == "on"


async def test_it_has_a_per_statement_time_limit(reader: asyncpg.Connection) -> None:
    # Without this, a badly shaped query could hold the database indefinitely (§8.6).
    assert await reader.fetchval("SHOW statement_timeout") == "3s"
