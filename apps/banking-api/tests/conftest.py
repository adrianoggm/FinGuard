import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

import asyncpg
import pytest

from finguard_banking.bootstrap.config import get_settings
from finguard_banking.domain.enums import (
    AccountStatus,
    EntryType,
    TransactionStatus,
    TransactionType,
)


@pytest.fixture
async def connection() -> AsyncIterator[asyncpg.Connection]:
    """A connection whose work is undone at the end: tests leave no trace.

    The ledger is immutable, so a test could not delete its own data: the only
    possible cleanup is never committing the transaction.
    """
    conn: asyncpg.Connection = await asyncpg.connect(get_settings().database_url)
    transaction = conn.transaction()
    await transaction.start()
    try:
        yield conn
    finally:
        await transaction.rollback()
        await conn.close()


async def create_account(conn: asyncpg.Connection, currency: str = "EUR") -> uuid.UUID:
    customer_id = uuid.uuid4()
    account_id = uuid.uuid4()
    await conn.execute(
        "INSERT INTO customers (id, full_name) VALUES ($1, $2)", customer_id, "Test customer"
    )
    await conn.execute(
        "INSERT INTO accounts (id, customer_id, account_reference, currency, status) "
        "VALUES ($1, $2, $3, $4, $5)",
        account_id,
        customer_id,
        f"ES-{account_id.hex[:12]}",
        currency,
        AccountStatus.ACTIVE.value,
    )
    return account_id


async def create_transaction(
    conn: asyncpg.Connection,
    status: str = TransactionStatus.BOOKED.value,
    *,
    category: str | None = None,
    merchant: str | None = None,
    occurred_at: datetime | None = None,
    description: str = "Test movement",
) -> uuid.UUID:
    transaction_id = uuid.uuid4()
    metadata: dict[str, str] = {}
    if category is not None:
        metadata["category"] = category
    if merchant is not None:
        metadata["merchant_name"] = merchant

    await conn.execute(
        "INSERT INTO ledger_transactions "
        "(id, transaction_type, status, reference, description, metadata, occurred_at) "
        "VALUES ($1, $2, $3, $4, $5, $6::jsonb, COALESCE($7::timestamptz, NOW()))",
        transaction_id,
        TransactionType.TRANSFER.value,
        status,
        f"ref-{transaction_id.hex[:12]}",
        description,
        json.dumps(metadata),
        occurred_at,
    )
    return transaction_id


async def post_entry(
    conn: asyncpg.Connection,
    transaction_id: uuid.UUID,
    account_id: uuid.UUID,
    entry_type: str,
    amount_minor: int,
    currency: str = "EUR",
) -> uuid.UUID:
    entry_id = uuid.uuid4()
    await conn.execute(
        "INSERT INTO ledger_entries "
        "(id, transaction_id, account_id, entry_type, amount_minor, currency) "
        "VALUES ($1, $2, $3, $4, $5, $6)",
        entry_id,
        transaction_id,
        account_id,
        entry_type,
        amount_minor,
        currency,
    )
    return entry_id


async def record_movement(
    conn: asyncpg.Connection,
    account: uuid.UUID,
    counterparty: uuid.UUID,
    amount_minor: int,
    **kwargs: Any,
) -> uuid.UUID:
    """A complete, balanced movement. A positive amount means money into `account`."""
    transaction_id = await create_transaction(conn, **kwargs)
    incoming = amount_minor > 0
    amount = abs(amount_minor)
    debit, credit = EntryType.DEBIT.value, EntryType.CREDIT.value
    await post_entry(conn, transaction_id, account, credit if incoming else debit, amount)
    await post_entry(conn, transaction_id, counterparty, debit if incoming else credit, amount)
    return transaction_id


async def force_deferred_checks(conn: asyncpg.Connection) -> None:
    """Fires the deferred triggers without committing.

    The balance invariant is evaluated at COMMIT; this evaluates it now, which is the
    only way to check it without leaving data behind.
    """
    await conn.execute("SET CONSTRAINTS ALL IMMEDIATE")


def as_pool(conn: asyncpg.Connection) -> Any:
    """Makes a single connection look like a pool.

    Repositories ask for a pool and call `acquire()`. Tests must reuse the fixture's
    connection, because the data lives in an uncommitted transaction that another
    connection would not see.
    """

    class _SingleConnectionPool:
        def acquire(self) -> Any:
            @asynccontextmanager
            async def borrowed() -> AsyncIterator[asyncpg.Connection]:
                yield conn

            return borrowed()

    return _SingleConnectionPool()
