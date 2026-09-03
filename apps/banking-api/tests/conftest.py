"""Fixtures only. The helpers are library code and live in tests/support/."""

import uuid
from collections.abc import AsyncIterator
from typing import Any

import asyncpg
import pytest

from finguard_banking.bootstrap.config import get_settings

# Statements that *are* the transaction, rather than statements inside one. Passed straight
# through: wrapping `ROLLBACK TO SAVEPOINT` in a savepoint of its own destroys the savepoint it
# was about to return to, and `SET CONSTRAINTS ALL IMMEDIATE` — how `force_deferred_checks` fires
# the balance trigger without committing — has to raise into the test rather than into a rollback.
_TRANSACTION_CONTROL = frozenset(
    {"BEGIN", "START", "COMMIT", "ROLLBACK", "SAVEPOINT", "RELEASE", "SET"}
)


class _StatementScoped(asyncpg.Connection):  # type: ignore[type-arg]
    """A connection where one refused statement does not take the rest of the test with it.

    The fixture below holds each test inside a single transaction it never commits, because an
    immutable ledger admits no other cleanup. The cost is that a statement PostgreSQL *refuses*
    aborts that transaction, and every later statement in it then fails with
    `InFailedSQLTransactionError` regardless of what it asks.

    That makes one useful shape impossible to write: assert the database refused an UPDATE, **and
    then read the row back to prove it is unchanged**. The `pytest.raises` passes and the verifying
    SELECT dies on the aborted transaction. `test_audit.py::test_the_trail_cannot_be_rewritten`
    shows the other face of the same problem — its second `pytest.raises` was satisfied by
    `InFailedSQLTransactionError` left over from its first, so it passed without ever reaching the
    append-only trigger it names.

    So each data statement gets its own savepoint: released when it succeeds, rolled back to when
    it raises. The error still reaches the test; the transaction survives it. This is exactly
    psql's `ON_ERROR_ROLLBACK`, and it is the difference between a test that proves a control
    refused something and one that only proves it complained.
    """

    async def execute(self, query: str, *args: Any, **kwargs: Any) -> Any:
        # `rstrip(";")` and not just `split()`: asyncpg's own Transaction emits `ROLLBACK;` with
        # the semicolon attached, and a `ROLLBACK;` that slips past this check gets wrapped in a
        # savepoint, ends the transaction, and leaves the RELEASE below with nothing to release.
        head = query.strip().split(None, 1)
        keyword = head[0].rstrip(";").upper() if head else ""
        if not keyword or keyword in _TRANSACTION_CONTROL or not self.is_in_transaction():
            return await super().execute(query, *args, **kwargs)

        # `super().execute` for the savepoint statements too, so they never re-enter this method.
        savepoint = f"stmt_{uuid.uuid4().hex}"
        await super().execute(f"SAVEPOINT {savepoint}")
        try:
            result = await super().execute(query, *args, **kwargs)
        except BaseException:
            await super().execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            raise
        await super().execute(f"RELEASE SAVEPOINT {savepoint}")
        return result


@pytest.fixture
async def connection() -> AsyncIterator[asyncpg.Connection]:
    """A connection whose work is undone at the end: tests leave no trace.

    The ledger is immutable, so a test could not delete its own data: the only possible
    cleanup is never committing the transaction.
    """
    conn: asyncpg.Connection = await asyncpg.connect(
        get_settings().database_url, connection_class=_StatementScoped
    )
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
