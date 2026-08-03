"""Can one account's session reach another's rows?

This is the gate that was listed as **unmeasured**, and it was unmeasured for a reason
worth writing down: the property is about two holders, and the development token table had
one entry. The seeded data always had three. So the claim "a caller cannot read another
account" had nothing to fail against.

The application already enforced it by construction — the account travels from the
credential, and the request body has nowhere to name one. What these pin is the layer
underneath: that a bug in the compiler or the adapter would not get through either.
"""

import uuid
from datetime import UTC, date, datetime

import asyncpg
import pytest
from support import as_pool, create_account, record_movement

from finguard_banking.domain.enums import Direction
from finguard_banking.domain.models.financial_query import FinancialQuery
from finguard_banking.domain.value_objects.date_range import DateRange
from finguard_banking.infrastructure.persistence.financial_query_repository import (
    PostgresFinancialQueryRepository,
)

pytestmark = pytest.mark.integration

JULY = DateRange(start=date(2026, 7, 1), end=date(2026, 7, 31))
VIEW = "SELECT count(*) FROM assistant_read.transactions_v1"


def plan(account: uuid.UUID) -> FinancialQuery:
    return FinancialQuery(account_id=account, date_range=JULY, direction=Direction.EXPENSE)


async def two_accounts(connection: asyncpg.Connection) -> tuple[uuid.UUID, uuid.UUID]:
    mine, theirs = await create_account(connection), await create_account(connection)
    bank = await create_account(connection)
    for account in (mine, theirs):
        await record_movement(
            connection,
            account=account,
            counterparty=bank,
            amount_minor=-4210,
            occurred_at=datetime(2026, 7, 3, 12, tzinfo=UTC),
            category="GROCERIES",
        )
    return mine, theirs


async def test_a_session_reads_only_the_account_it_declared(
    connection: asyncpg.Connection,
) -> None:
    mine, theirs = await two_accounts(connection)
    repository = PostgresFinancialQueryRepository(as_pool(connection), "Europe/Madrid")

    answer = await repository.run(plan(mine))

    assert answer.movement_count == 1, "one movement was recorded for this account"


async def test_declaring_one_account_hides_the_other(connection: asyncpg.Connection) -> None:
    """The compiler also filters by account, so this asks the question the compiler cannot
    be trusted with: with the session declaring one account and no WHERE at all, does the
    other account's row exist?"""
    mine, theirs = await two_accounts(connection)

    async with connection.transaction():
        await connection.execute("SELECT set_config('app.account_id', $1::text, true)", str(mine))
        visible = await connection.fetchval(VIEW)

    assert visible == 1, "the view returned rows belonging to an account it was not told about"


async def test_a_session_that_declares_nothing_sees_nothing(
    connection: asyncpg.Connection,
) -> None:
    """The fail-safe direction. Forgetting to declare the account costs an empty answer,
    which somebody notices, rather than somebody else's figures, which nobody does."""
    await two_accounts(connection)

    assert await connection.fetchval(VIEW) == 0


async def test_declaring_an_account_that_does_not_exist_reveals_nothing(
    connection: asyncpg.Connection,
) -> None:
    await two_accounts(connection)

    async with connection.transaction():
        await connection.execute(
            "SELECT set_config('app.account_id', $1::text, true)", str(uuid.uuid4())
        )

        assert await connection.fetchval(VIEW) == 0
