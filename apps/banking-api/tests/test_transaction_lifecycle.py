"""Can a booked movement still change?

ADR-002 says the ledger is immutable and migration 0001 implemented that as "no UPDATE, no
DELETE". These are the things that were still possible, each of which changes an account's
balance after the movement was recorded — and none of which any test would have caught.
"""

import uuid

import asyncpg
import pytest
from support import create_account, create_transaction, post_entry

pytestmark = pytest.mark.integration

DEBIT, CREDIT = "DEBIT", "CREDIT"


async def pending_pair(connection: asyncpg.Connection) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """A balanced PENDING transaction: the state a payment is in before it is confirmed."""
    account, bank = await create_account(connection), await create_account(connection)
    transaction = await create_transaction(connection, status="PENDING")
    await post_entry(connection, transaction, account, DEBIT, 4210)
    await post_entry(connection, transaction, bank, CREDIT, 4210)
    return transaction, account, bank


async def book(connection: asyncpg.Connection, transaction: uuid.UUID) -> None:
    await connection.execute(
        "UPDATE ledger_transactions SET status = 'BOOKED' WHERE id = $1", transaction
    )


async def test_a_pending_transaction_can_be_booked(connection: asyncpg.Connection) -> None:
    transaction, _, _ = await pending_pair(connection)

    await book(connection, transaction)

    status = await connection.fetchval(
        "SELECT status FROM ledger_transactions WHERE id = $1", transaction
    )
    assert status == "BOOKED"


async def test_a_booked_transaction_accepts_no_further_entries(
    connection: asyncpg.Connection,
) -> None:
    """The hole. A new debit and a new credit keep it balanced, so the balance trigger passes —
    and the account's balance changes after the movement was recorded."""
    transaction, account, bank = await pending_pair(connection)
    await book(connection, transaction)

    with pytest.raises(asyncpg.PostgresError, match="only a PENDING"):
        await post_entry(connection, transaction, account, DEBIT, 999_99)


async def test_a_booked_transaction_cannot_be_unbooked(connection: asyncpg.Connection) -> None:
    transaction, _, _ = await pending_pair(connection)
    await book(connection, transaction)

    with pytest.raises(asyncpg.PostgresError, match="terminal"):
        await connection.execute(
            "UPDATE ledger_transactions SET status = 'PENDING' WHERE id = $1", transaction
        )


async def test_a_cancelled_transaction_cannot_be_booked(connection: asyncpg.Connection) -> None:
    transaction, _, _ = await pending_pair(connection)
    await connection.execute(
        "UPDATE ledger_transactions SET status = 'CANCELLED' WHERE id = $1", transaction
    )

    with pytest.raises(asyncpg.PostgresError, match="terminal"):
        await book(connection, transaction)


async def test_an_unbalanced_transaction_cannot_be_booked(connection: asyncpg.Connection) -> None:
    """Asserted again at the transition, not only when the entries landed. The insert check is
    per statement; this one is per transition, and the transition is what makes the figures
    visible to the read views."""
    account = await create_account(connection)
    transaction = await create_transaction(connection, status="PENDING")
    await post_entry(connection, transaction, account, DEBIT, 4210)
    # The balancing credit is deliberately missing. The deferred insert check fires at commit,
    # so inside this transaction the row exists and the books do not balance.

    with pytest.raises(asyncpg.PostgresError, match="does not balance"):
        await book(connection, transaction)


async def test_an_empty_transaction_cannot_be_booked(connection: asyncpg.Connection) -> None:
    # Zero entries balance trivially. A booked transaction with none is a movement that moved
    # nothing, appearing in the ledger as a real event.
    transaction = await create_transaction(connection, status="PENDING")

    with pytest.raises(asyncpg.PostgresError, match="no entries"):
        await book(connection, transaction)


async def test_an_unknown_status_is_refused(connection: asyncpg.Connection) -> None:
    """The column accepted any 30-character string, so a typo was a new state — and the read
    views filter on `status = 'BOOKED'`, so a transaction in a misspelled state simply stops
    existing as far as every figure is concerned."""
    with pytest.raises(asyncpg.PostgresError):
        await create_transaction(connection, status="BOKED")
