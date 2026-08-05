"""A holder sees their own accounts, and only their own.

`test_account_isolation` covers the property this replaces: a session reads the account it
declared. That was the whole rule while a holder had one account. With a current account, a piggy
bank and a fund it is two rules, and they pull in opposite directions:

    widen    the other accounts of the same person become visible
    hold     everybody else's stay invisible

The second is the one worth writing tests for. It is the only place in this repository where a
mistake shows somebody else's money, and it is a mistake that reads as a working query.

What enforces it is not the row-level security policies — `relforcerowsecurity` is off, see 0007 —
but the privilege barrier plus `my_accounts_v1`, which every read view now filters through. These
tests go through the views, because that is what actually decides.
"""

import uuid

import asyncpg
import pytest
from support import as_pool, create_account, create_contact, record_movement

from finguard_banking.domain.enums import AccountKind
from finguard_banking.infrastructure.persistence.account_repository import (
    PostgresAccountRepository,
)
from finguard_banking.infrastructure.persistence.financial_query_repository import declare

pytestmark = pytest.mark.integration


async def a_holder_with_two_accounts(
    connection: asyncpg.Connection,
) -> tuple[uuid.UUID, uuid.UUID]:
    """One person, a current account and a piggy bank, with a movement in each."""
    customer = uuid.uuid4()
    await connection.execute(
        "INSERT INTO customers (id, full_name) VALUES ($1, $2)", customer, "Laura"
    )
    current = await create_account(connection, customer_id=customer)
    piggy = await create_account(connection, customer_id=customer, kind="SAVINGS")
    bank = await create_account(connection)
    await record_movement(connection, account=current, counterparty=bank, amount_minor=100_00)
    await record_movement(connection, account=piggy, counterparty=bank, amount_minor=30_00)
    return current, piggy


async def visible_accounts(connection: asyncpg.Connection, declared: uuid.UUID) -> set[uuid.UUID]:
    async with connection.transaction():
        await declare(connection, declared)
        rows = await connection.fetch("SELECT account_id FROM assistant_read.my_accounts_v1")
    return {row["account_id"] for row in rows}


async def visible_movements(connection: asyncpg.Connection, declared: uuid.UUID) -> set[uuid.UUID]:
    async with connection.transaction():
        await declare(connection, declared)
        rows = await connection.fetch(
            "SELECT DISTINCT account_id FROM assistant_read.transactions_v1"
        )
    return {row["account_id"] for row in rows}


# --- what widens ------------------------------------------------------------------------


async def test_declaring_one_account_opens_the_holders_others(
    connection: asyncpg.Connection,
) -> None:
    """The feature. A piggy bank the holder owns is theirs to read, and until now it was not."""
    current, piggy = await a_holder_with_two_accounts(connection)

    assert await visible_accounts(connection, current) == {current, piggy}


async def test_the_movements_of_both_accounts_are_readable(
    connection: asyncpg.Connection,
) -> None:
    """`my_accounts_v1` is the one place the rule lives, and every view filters through it. This is
    what says the other four actually do."""
    current, piggy = await a_holder_with_two_accounts(connection)

    assert await visible_movements(connection, current) == {current, piggy}


async def test_it_works_from_either_end(connection: asyncpg.Connection) -> None:
    """Declaring the piggy bank opens the current account too. The scope is the holder, not a
    primary account — there is no such thing here, and inventing one would be an unstated rule."""
    current, piggy = await a_holder_with_two_accounts(connection)

    assert await visible_accounts(connection, piggy) == {current, piggy}


# --- what must not ----------------------------------------------------------------------


async def test_another_holders_accounts_stay_invisible(connection: asyncpg.Connection) -> None:
    """The one that matters. A mistake here shows somebody else's money and reads as a working
    query — no error, no empty result, just more rows than there should be."""
    current, _ = await a_holder_with_two_accounts(connection)
    stranger = await create_account(connection)
    bank = await create_account(connection)
    await record_movement(connection, account=stranger, counterparty=bank, amount_minor=500_00)

    assert stranger not in await visible_accounts(connection, current)
    assert stranger not in await visible_movements(connection, current)


async def test_a_stranger_cannot_be_reached_by_declaring_them(
    connection: asyncpg.Connection,
) -> None:
    """Declaring an account is not a claim this service believes on its own — the credential is
    what authorised it. What this asserts is the shape: declaring somebody else's account opens
    *their* scope and never adds it to yours, so there is no session in which two holders are
    visible at once."""
    current, _ = await a_holder_with_two_accounts(connection)
    stranger = await create_account(connection)

    assert current not in await visible_accounts(connection, stranger)


async def test_declaring_an_account_that_does_not_exist_shows_nothing(
    connection: asyncpg.Connection,
) -> None:
    """The holder lookup finds nobody and the setting ends up empty. Empty has to mean the same as
    unset — `''` is not a uuid, and before `NULLIF` this raised a type error instead of answering
    "no such account", which is a different thing to tell a caller."""
    await a_holder_with_two_accounts(connection)

    assert await visible_accounts(connection, uuid.uuid4()) == set()


async def test_the_directory_lists_both_accounts_with_their_balances(
    connection: asyncpg.Connection,
) -> None:
    """What the endpoint is for, through the repository that serves it.

    The balance comes from `account_balances_v1` and not from a sum written here, so this also says
    the join holds: an account is listed with its own figure and not with the other one's.
    """
    current, piggy = await a_holder_with_two_accounts(connection)

    found = await PostgresAccountRepository(as_pool(connection)).all_of(current)

    assert {h.account_id: h.balance.amount_minor for h in found} == {current: 100_00, piggy: 30_00}


async def test_the_directory_never_lists_another_holders_account(
    connection: asyncpg.Connection,
) -> None:
    """The refusal, at the layer a route will actually call.

    `my_accounts_v1` is tested above, but this is the path with a repository, a pool and a
    `declare()` in it — and a missing declaration reads as a working query that happens to return
    every account in the bank.
    """
    current, _ = await a_holder_with_two_accounts(connection)
    stranger = await create_account(connection)

    found = await PostgresAccountRepository(as_pool(connection)).all_of(current)

    assert stranger not in {holding.account_id for holding in found}


async def test_a_savings_account_says_that_is_what_it_is(
    connection: asyncpg.Connection,
) -> None:
    """The kind travels, because it decides what a figure means. An ASSET balance is a cost basis
    and not a valuation, and a client that could not tell them apart would add them up."""
    _, piggy = await a_holder_with_two_accounts(connection)

    found = await PostgresAccountRepository(as_pool(connection)).all_of(piggy)

    assert {h.account_id: h.kind for h in found}[piggy] is AccountKind.SAVINGS


async def test_contacts_are_scoped_the_same_way(connection: asyncpg.Connection) -> None:
    """Every view filters through the same place, so a contact list follows the account list. A
    contact list is arguably more private than the movements: it is who somebody knows."""
    current, _ = await a_holder_with_two_accounts(connection)
    stranger = await create_account(connection)
    await create_contact(connection, current, "Marta Solis Vega", "+34600333444")
    await create_contact(connection, stranger, "Nadie Ajeno", "+34699999999")

    async with connection.transaction():
        await declare(connection, current)
        names = {
            r["display_name"]
            for r in await connection.fetch("SELECT display_name FROM assistant_read.contacts_v1")
        }

    assert names == {"Marta Solis Vega"}
