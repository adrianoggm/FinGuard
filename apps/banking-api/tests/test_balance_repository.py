"""The balance comes from the entries, not from a column someone wrote."""

import uuid

import asyncpg
import pytest
from conftest import as_pool, create_account, create_transaction, post_entry, record_movement

from finguard_banking.domain.errors import AccountNotFoundError
from finguard_banking.infrastructure.persistence.ledger_repository import (
    PostgresLedgerRepository,
)

pytestmark = pytest.mark.integration


async def test_an_account_without_movements_has_a_zero_balance(
    connection: asyncpg.Connection,
) -> None:
    account = await create_account(connection)
    repository = PostgresLedgerRepository(as_pool(connection))

    balance = await repository.balance_of(account)

    assert balance.booked_minor == 0
    assert balance.available_minor == 0
    assert balance.currency == "EUR"


async def test_a_debit_subtracts_and_a_credit_adds(connection: asyncpg.Connection) -> None:
    source = await create_account(connection)
    target = await create_account(connection)
    transaction = await create_transaction(connection)
    await post_entry(connection, transaction, source, "DEBIT", 2500)
    await post_entry(connection, transaction, target, "CREDIT", 2500)
    repository = PostgresLedgerRepository(as_pool(connection))

    # Sending money is recorded as a debit on the sender's account.
    assert (await repository.balance_of(source)).booked_minor == -2500
    assert (await repository.balance_of(target)).booked_minor == 2500


async def test_the_balance_accumulates_several_transactions(
    connection: asyncpg.Connection,
) -> None:
    account = await create_account(connection)
    counterparty = await create_account(connection)
    for amount in (10_000, 2_549):
        await record_movement(connection, account, counterparty, amount)
    repository = PostgresLedgerRepository(as_pool(connection))

    assert (await repository.balance_of(account)).booked_minor == 12_549


async def test_what_is_not_booked_does_not_count(connection: asyncpg.Connection) -> None:
    account = await create_account(connection)
    counterparty = await create_account(connection)
    await record_movement(connection, account, counterparty, 5_000, status="BOOKED")
    await record_movement(connection, account, counterparty, 99_999, status="PENDING")

    repository = PostgresLedgerRepository(as_pool(connection))

    assert (await repository.balance_of(account)).booked_minor == 5_000


async def test_an_account_that_does_not_exist_differs_from_one_at_zero(
    connection: asyncpg.Connection,
) -> None:
    repository = PostgresLedgerRepository(as_pool(connection))

    with pytest.raises(AccountNotFoundError):
        await repository.balance_of(uuid.uuid4())
