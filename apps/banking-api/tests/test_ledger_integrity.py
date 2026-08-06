"""The ledger invariants are enforced by PostgreSQL, not by the code that writes.

Each test checks that the database rejects what must not get in, no matter which
service or which console attempts it.
"""

import asyncpg
import pytest
from support import create_account, create_transaction, force_deferred_checks, post_entry

pytestmark = pytest.mark.integration


async def test_a_balanced_transaction_is_valid(connection: asyncpg.Connection) -> None:
    source = await create_account(connection)
    target = await create_account(connection)
    transaction = await create_transaction(connection)

    await post_entry(connection, transaction, source, "DEBIT", 2500)
    await post_entry(connection, transaction, target, "CREDIT", 2500)

    await force_deferred_checks(connection)


async def test_an_unbalanced_transaction_is_rejected(connection: asyncpg.Connection) -> None:
    source = await create_account(connection)
    target = await create_account(connection)
    transaction = await create_transaction(connection)

    await post_entry(connection, transaction, source, "DEBIT", 2500)
    await post_entry(connection, transaction, target, "CREDIT", 2400)

    with pytest.raises(asyncpg.RaiseError, match="unbalanced"):
        await force_deferred_checks(connection)


async def test_a_lone_entry_is_not_a_transaction(connection: asyncpg.Connection) -> None:
    account = await create_account(connection)
    transaction = await create_transaction(connection)

    await post_entry(connection, transaction, account, "DEBIT", 2500)

    with pytest.raises(asyncpg.RaiseError, match="unbalanced"):
        await force_deferred_checks(connection)


async def test_mixing_currencies_does_not_balance(connection: asyncpg.Connection) -> None:
    in_euros = await create_account(connection, currency="EUR")
    in_dollars = await create_account(connection, currency="USD")
    transaction = await create_transaction(connection)

    # The amounts match, but 2500 EUR debited is not offset by 2500 USD credited.
    await post_entry(connection, transaction, in_euros, "DEBIT", 2500, currency="EUR")
    await post_entry(connection, transaction, in_dollars, "CREDIT", 2500, currency="USD")

    with pytest.raises(asyncpg.RaiseError, match="unbalanced"):
        await force_deferred_checks(connection)


async def test_an_entry_cannot_use_a_currency_other_than_its_account(
    connection: asyncpg.Connection,
) -> None:
    source = await create_account(connection, currency="EUR")
    target = await create_account(connection, currency="EUR")
    transaction = await create_transaction(connection)

    # It balances in USD, but both accounts are in euros.
    await post_entry(connection, transaction, source, "DEBIT", 2500, currency="USD")
    await post_entry(connection, transaction, target, "CREDIT", 2500, currency="USD")

    with pytest.raises(asyncpg.RaiseError, match="currency other than"):
        await force_deferred_checks(connection)


@pytest.mark.parametrize("amount", [0, -2500])
async def test_an_entry_without_a_positive_amount_is_rejected(
    connection: asyncpg.Connection, amount: int
) -> None:
    account = await create_account(connection)
    transaction = await create_transaction(connection)

    with pytest.raises(asyncpg.CheckViolationError):
        await post_entry(connection, transaction, account, "DEBIT", amount)


async def test_an_invented_entry_type_is_rejected(connection: asyncpg.Connection) -> None:
    account = await create_account(connection)
    transaction = await create_transaction(connection)

    with pytest.raises(asyncpg.CheckViolationError):
        await post_entry(connection, transaction, account, "POSTING", 2500)


async def test_an_entry_cannot_be_modified(connection: asyncpg.Connection) -> None:
    source = await create_account(connection)
    target = await create_account(connection)
    transaction = await create_transaction(connection)
    entry = await post_entry(connection, transaction, source, "DEBIT", 2500)
    await post_entry(connection, transaction, target, "CREDIT", 2500)

    with pytest.raises(asyncpg.RaiseError, match="immutable"):
        await connection.execute("UPDATE ledger_entries SET amount_minor = 1 WHERE id = $1", entry)


async def test_an_entry_cannot_be_deleted(connection: asyncpg.Connection) -> None:
    source = await create_account(connection)
    target = await create_account(connection)
    transaction = await create_transaction(connection)
    entry = await post_entry(connection, transaction, source, "DEBIT", 2500)
    await post_entry(connection, transaction, target, "CREDIT", 2500)

    with pytest.raises(asyncpg.RaiseError, match="immutable"):
        await connection.execute("DELETE FROM ledger_entries WHERE id = $1", entry)


async def test_a_transaction_cannot_be_deleted(connection: asyncpg.Connection) -> None:
    transaction = await create_transaction(connection)

    with pytest.raises(asyncpg.RaiseError, match="immutable"):
        await connection.execute("DELETE FROM ledger_transactions WHERE id = $1", transaction)
