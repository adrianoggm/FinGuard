"""Four layers of classification, and which one a figure came from.

A movement's category used to be two things pretending to be one: what the bank said and what the
holder overrode. `system_category` sits between them and tags sit outside them entirely, and the
point of this file is the *order* and the boundary — which layer wins, and why a tag is not a
category however much it looks like one.
"""

import uuid
from datetime import date

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

WHOLE_YEAR = DateRange(start=date(2026, 1, 1), end=date(2026, 12, 31))

_CLASSIFY = (
    "INSERT INTO movement_classifications "
    "(account_id, transaction_id, category, classifier_version) VALUES ($1, $2, $3, $4)"
)
_OVERRIDE = (
    "INSERT INTO movement_categories (account_id, transaction_id, category) VALUES ($1, $2, $3)"
)
_TAG = "INSERT INTO movement_tags (account_id, transaction_id, tag) VALUES ($1, $2, $3)"


def queries(connection: asyncpg.Connection) -> PostgresFinancialQueryRepository:
    return PostgresFinancialQueryRepository(as_pool(connection), "Europe/Madrid")


async def a_movement(
    connection: asyncpg.Connection, bank_says: str | None
) -> tuple[uuid.UUID, uuid.UUID]:
    account = await create_account(connection)
    shop = await create_account(connection)
    transaction = await record_movement(
        connection,
        account=account,
        counterparty=shop,
        amount_minor=-5_000,
        category=bank_says,
        merchant="Fnac",
    )
    return account, transaction


async def category_of(connection: asyncpg.Connection, account: uuid.UUID) -> tuple:
    """The effective category and which layer decided it, straight off the read view."""
    async with connection.transaction():
        await connection.execute(
            "SELECT set_config('app.account_id', $1::text, true)", str(account)
        )
        row = await connection.fetchrow(
            "SELECT category, category_source FROM assistant_read.transactions_v1 "
            "WHERE account_id = $1",
            account,
        )
    return row["category"], row["category_source"]


async def test_the_bank_decides_when_nobody_else_has(connection: asyncpg.Connection) -> None:
    account, _ = await a_movement(connection, bank_says="LEISURE")

    assert await category_of(connection, account) == ("LEISURE", "BANK")


async def test_this_service_overrides_the_bank(connection: asyncpg.Connection) -> None:
    """`system_category` is recomputable and the bank's is not, so it has to win — otherwise a
    better classifier could never correct anything the bank got wrong."""
    account, transaction = await a_movement(connection, bank_says="LEISURE")
    await connection.execute(_CLASSIFY, account, transaction, "SUBSCRIPTIONS", "v1")

    assert await category_of(connection, account) == ("SUBSCRIPTIONS", "SYSTEM")


async def test_the_holder_overrides_both(connection: asyncpg.Connection) -> None:
    """The whole point of an override. A holder who corrected a movement must not have it
    corrected back by the next run of the classifier."""
    account, transaction = await a_movement(connection, bank_says="LEISURE")
    await connection.execute(_CLASSIFY, account, transaction, "SUBSCRIPTIONS", "v1")
    await connection.execute(_OVERRIDE, account, transaction, "HEALTH")

    assert await category_of(connection, account) == ("HEALTH", "USER")


async def test_a_movement_nobody_classified_says_so(connection: asyncpg.Connection) -> None:
    """NONE rather than a guess. An answer that invents a bucket is worse than one saying the
    movement is unfiled, because only the second is correctable."""
    account, _ = await a_movement(connection, bank_says=None)

    assert await category_of(connection, account) == (None, "NONE")


async def test_a_reclassification_moves_the_figure(connection: asyncpg.Connection) -> None:
    """The layer is not decoration: it reaches the aggregate every screen and every sentence read,
    without either of them knowing the layers exist."""
    account, transaction = await a_movement(connection, bank_says="LEISURE")
    await connection.execute(_CLASSIFY, account, transaction, "SUBSCRIPTIONS", "v1")

    answer = await queries(connection).run(
        FinancialQuery(account_id=account, date_range=WHOLE_YEAR, direction=Direction.EXPENSE)
    )

    assert answer.total.amount_minor == 5_000
    assert answer.supporting_movements[0].category == "SUBSCRIPTIONS"


async def test_a_tag_is_not_a_category(connection: asyncpg.Connection) -> None:
    """The boundary this migration exists to draw.

    A category partitions — exactly one per movement — which is what makes a breakdown add up to
    the total, and the end-to-end evaluation asserts that it does. A tag is zero or many. Two tags
    on one movement is ordinary and would double it in any grouping that treated them alike, so
    tags have a table and deliberately no column on the read view.
    """
    account, transaction = await a_movement(connection, bank_says="LEISURE")
    for tag in ("regalo", "navidad"):
        await connection.execute(_TAG, account, transaction, tag)

    async with connection.transaction():
        await connection.execute(
            "SELECT set_config('app.account_id', $1::text, true)", str(account)
        )
        rows = await connection.fetch(
            "SELECT * FROM assistant_read.transactions_v1 WHERE account_id = $1", account
        )

    assert len(rows) == 1, "two tags did not become two movements"
    assert "tag" not in rows[0].keys(), "a tag on the view is a tag inside a GROUP BY"


async def test_the_same_label_twice_is_one_label(connection: asyncpg.Connection) -> None:
    account, transaction = await a_movement(connection, bank_says="LEISURE")
    await connection.execute(_TAG, account, transaction, "regalo")

    with pytest.raises(asyncpg.exceptions.UniqueViolationError):
        await connection.execute(_TAG, account, transaction, "regalo")


async def test_a_tag_is_stored_the_way_it_will_be_searched(
    connection: asyncpg.Connection,
) -> None:
    """Lower case in the column. "Regalo" and "regalo" are one label, and a directory that treats
    them as two cannot find the one it has — the same argument the IBAN's compact form makes."""
    account, transaction = await a_movement(connection, bank_says="LEISURE")

    with pytest.raises(asyncpg.exceptions.CheckViolationError):
        await connection.execute(_TAG, account, transaction, "Regalo")
