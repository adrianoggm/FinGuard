"""The nth largest movement of a period.

    "¿Cuál fue el gasto más caro del año en gasolina?"
    "Y el segundo."

Two questions any bank app is asked, and until now neither had an answer: the contract could
sum movements and count them, so the assistant refused rather than serving a total in place of a
maximum. Refusing was right — a total is a real number about a different question — and it is not
the same as being able to answer.

The capability is a different use case rather than a wider `Metric`. A maximum is one row, not an
aggregate: it has an identity, a date and a shop, and the answer names them. Nothing about
`FinancialQuery` changes — the filters are the same ones, and only the ordering and the offset are
new.

**The ordering is total.** `amount_minor DESC` alone leaves ties in whatever order the plan happens
to produce, so "el segundo" could be either of two equal charges and could differ between two runs
of the same question. The date breaks the tie and the identifier breaks the rest, which makes the
ranking a function of the data.
"""

import uuid
from datetime import UTC, date, datetime

import asyncpg
import pytest
from support import as_pool, create_account, record_movement

from finguard_banking.domain.enums import Direction
from finguard_banking.domain.models.financial_query import FinancialQuery
from finguard_banking.domain.value_objects.category import Category
from finguard_banking.domain.value_objects.date_range import DateRange
from finguard_banking.infrastructure.persistence.financial_query_repository import (
    PostgresFinancialQueryRepository,
)
from finguard_banking.infrastructure.persistence.query_compiler import compile_ranked

JULY = DateRange(start=date(2026, 7, 1), end=date(2026, 7, 31))


def plan(account: uuid.UUID, **kwargs: object) -> FinancialQuery:
    return FinancialQuery(
        account_id=account,
        date_range=JULY,
        direction=Direction.EXPENSE,
        **kwargs,  # type: ignore[arg-type]
    )


def test_the_ranking_is_ordered_by_amount_and_then_broken_deterministically() -> None:
    """A ranking whose ties are arbitrary is not a ranking: "el segundo" would be either of two
    equal charges, and could differ between two runs of the same question."""
    compiled = compile_ranked(plan(uuid.uuid4()), "Europe/Madrid", rank=1)

    ordering = compiled.sql.split("ORDER BY")[1]
    assert "amount_minor DESC" in ordering
    assert "occurred_at DESC" in ordering
    assert "transaction_id" in ordering


def test_the_rank_becomes_an_offset_and_never_reaches_the_sql_as_text() -> None:
    """Same rule as every other value here: it travels as a parameter. A rank is a number a model
    chose, and §9.3 does not care how harmless a number looks."""
    compiled = compile_ranked(plan(uuid.uuid4()), "Europe/Madrid", rank=3)

    assert "OFFSET" in compiled.sql
    assert 2 in compiled.parameters, "rank 3 is the third row, so it skips two"
    assert "3" not in compiled.sql.replace("$3", "").replace("$13", "")


@pytest.mark.integration
async def test_the_largest_expense_of_the_period(connection: asyncpg.Connection) -> None:
    account, bank = await create_account(connection), await create_account(connection)
    for merchant, amount, day in (
        ("Cepsa", -8240, 14),
        ("Repsol", -7610, 22),
        ("Cepsa", -2010, 3),
    ):
        await record_movement(
            connection,
            account,
            bank,
            amount,
            merchant=merchant,
            category="TRANSPORT_FUEL",
            occurred_at=datetime(2026, 7, day, 10, 0, tzinfo=UTC),
        )

    repository = PostgresFinancialQueryRepository(as_pool(connection), "Europe/Madrid")
    top = await repository.ranked(plan(account, categories=(Category("TRANSPORT_FUEL"),)), rank=1)

    assert top is not None
    assert top.amount.amount_minor == 8240
    assert top.merchant_name == "Cepsa"
    assert top.occurred_at.date() == date(2026, 7, 14)


@pytest.mark.integration
async def test_the_second_largest_is_the_next_one_down(connection: asyncpg.Connection) -> None:
    """The follow-up, which is the whole reason the rank is a parameter: "y el segundo" changes one
    number and nothing else about the question."""
    account, bank = await create_account(connection), await create_account(connection)
    for merchant, amount, day in (("Cepsa", -8240, 14), ("Repsol", -7610, 22)):
        await record_movement(
            connection,
            account,
            bank,
            amount,
            merchant=merchant,
            category="TRANSPORT_FUEL",
            occurred_at=datetime(2026, 7, day, 10, 0, tzinfo=UTC),
        )

    repository = PostgresFinancialQueryRepository(as_pool(connection), "Europe/Madrid")
    second = await repository.ranked(
        plan(account, categories=(Category("TRANSPORT_FUEL"),)), rank=2
    )

    assert second is not None
    assert second.amount.amount_minor == 7610
    assert second.merchant_name == "Repsol"


@pytest.mark.integration
async def test_a_rank_past_the_end_is_nothing_rather_than_the_last_one(
    connection: asyncpg.Connection,
) -> None:
    """ "El quinto" of three movements has no answer, and the honest one is nothing. Returning the
    smallest would be a real figure presented as the fifth largest."""
    account, bank = await create_account(connection), await create_account(connection)
    await record_movement(
        connection,
        account,
        bank,
        -8240,
        merchant="Cepsa",
        occurred_at=datetime(2026, 7, 14, 10, 0, tzinfo=UTC),
    )

    repository = PostgresFinancialQueryRepository(as_pool(connection), "Europe/Madrid")

    assert await repository.ranked(plan(account), rank=5) is None


@pytest.mark.integration
async def test_it_ranks_expenses_and_income_separately(connection: asyncpg.Connection) -> None:
    """A salary is larger than every expense in the month. Ranking without the direction filter
    would answer "your biggest expense" with the money coming in."""
    account, bank = await create_account(connection), await create_account(connection)
    await record_movement(
        connection, account, bank, 244287, occurred_at=datetime(2026, 7, 28, 9, 0, tzinfo=UTC)
    )
    await record_movement(
        connection,
        account,
        bank,
        -8240,
        merchant="Cepsa",
        occurred_at=datetime(2026, 7, 14, 10, 0, tzinfo=UTC),
    )

    repository = PostgresFinancialQueryRepository(as_pool(connection), "Europe/Madrid")
    biggest_expense = await repository.ranked(plan(account), rank=1)

    assert biggest_expense is not None
    assert biggest_expense.amount.amount_minor == 8240


@pytest.mark.integration
async def test_another_account_is_never_ranked(connection: asyncpg.Connection) -> None:
    """The same containment every read here has. It is asserted per query rather than once,
    because a new SQL statement is a new chance to forget the account."""
    mine, theirs, bank = (
        await create_account(connection),
        await create_account(connection),
        await create_account(connection),
    )
    await record_movement(
        connection,
        theirs,
        bank,
        -99999,
        merchant="Ajeno",
        occurred_at=datetime(2026, 7, 14, 10, 0, tzinfo=UTC),
    )

    repository = PostgresFinancialQueryRepository(as_pool(connection), "Europe/Madrid")

    assert await repository.ranked(plan(mine), rank=1) is None
