"""The financial query against a real PostgreSQL, with its own data, undone at the end."""

import uuid
from datetime import UTC, date, datetime

import asyncpg
import pytest
from conftest import as_pool, create_account, record_movement

from finguard_banking.domain.enums import Direction, GroupBy
from finguard_banking.domain.errors import AccountNotFoundError
from finguard_banking.domain.financial_query import DateRange, FinancialQuery
from finguard_banking.infrastructure.persistence.financial_query_repository import (
    PostgresFinancialQueryRepository,
)

pytestmark = pytest.mark.integration

JULY = DateRange(start=date(2026, 7, 1), end=date(2026, 7, 31))


def on(day: int, hour: int = 12) -> datetime:
    return datetime(2026, 7, day, hour, tzinfo=UTC)


def plan(account: uuid.UUID, **kwargs: object) -> FinancialQuery:
    return FinancialQuery(account_id=account, date_range=JULY, **kwargs)  # type: ignore[arg-type]


def repository(connection: asyncpg.Connection) -> PostgresFinancialQueryRepository:
    return PostgresFinancialQueryRepository(as_pool(connection), "Europe/Madrid")


async def test_it_only_adds_up_the_requested_categories(connection: asyncpg.Connection) -> None:
    account = await create_account(connection)
    bank = await create_account(connection)
    await record_movement(
        connection, account, bank, -4_500, category="TRANSPORT_FUEL", occurred_at=on(3)
    )
    await record_movement(
        connection, account, bank, -3_200, category="TRANSPORT_FUEL", occurred_at=on(17)
    )
    await record_movement(
        connection, account, bank, -9_000, category="GROCERIES", occurred_at=on(18)
    )

    result = await repository(connection).run(
        plan(account, direction=Direction.EXPENSE, categories=("TRANSPORT_FUEL",))
    )

    # Spending comes back positive: "I spent 77.00", not "-77.00".
    assert result.total_minor == 7_700
    assert result.movement_count == 2
    assert result.currency == "EUR"


async def test_what_falls_outside_the_range_does_not_count(
    connection: asyncpg.Connection,
) -> None:
    account = await create_account(connection)
    bank = await create_account(connection)
    await record_movement(
        connection, account, bank, -5_000, category="GROCERIES", occurred_at=on(15)
    )
    await record_movement(
        connection,
        account,
        bank,
        -5_000,
        category="GROCERIES",
        occurred_at=datetime(2026, 6, 30, 12, tzinfo=UTC),
    )

    result = await repository(connection).run(plan(account, direction=Direction.EXPENSE))

    assert result.total_minor == 5_000


async def test_what_is_not_booked_does_not_count(connection: asyncpg.Connection) -> None:
    account = await create_account(connection)
    bank = await create_account(connection)
    await record_movement(
        connection, account, bank, -5_000, category="GROCERIES", occurred_at=on(10)
    )
    await record_movement(
        connection,
        account,
        bank,
        -70_000,
        category="GROCERIES",
        occurred_at=on(11),
        status="PENDING",
    )

    result = await repository(connection).run(plan(account, direction=Direction.EXPENSE))

    assert result.total_minor == 5_000


async def test_it_groups_by_week(connection: asyncpg.Connection) -> None:
    account = await create_account(connection)
    bank = await create_account(connection)
    for amount, day in ((-3_420, 7), (-6_200, 14), (-3_123, 21)):
        await record_movement(
            connection, account, bank, amount, category="TRANSPORT_FUEL", occurred_at=on(day)
        )

    result = await repository(connection).run(
        plan(account, direction=Direction.EXPENSE, group_by=GroupBy.WEEK)
    )

    assert [group.total_minor for group in result.groups] == [3_420, 6_200, 3_123]
    assert result.total_minor == 12_743


async def test_it_groups_by_category(connection: asyncpg.Connection) -> None:
    account = await create_account(connection)
    bank = await create_account(connection)
    await record_movement(
        connection, account, bank, -4_000, category="GROCERIES", occurred_at=on(5)
    )
    await record_movement(
        connection, account, bank, -1_000, category="GROCERIES", occurred_at=on(6)
    )
    await record_movement(
        connection, account, bank, -7_000, category="RESTAURANTS", occurred_at=on(8)
    )

    result = await repository(connection).run(
        plan(account, direction=Direction.EXPENSE, group_by=GroupBy.CATEGORY)
    )

    assert {(g.key, g.total_minor) for g in result.groups} == {
        ("GROCERIES", 5_000),
        ("RESTAURANTS", 7_000),
    }


async def test_income_is_kept_apart_from_spending(connection: asyncpg.Connection) -> None:
    account = await create_account(connection)
    bank = await create_account(connection)
    await record_movement(connection, account, bank, 245_000, category="SALARY", occurred_at=on(25))
    await record_movement(
        connection, account, bank, -9_000, category="GROCERIES", occurred_at=on(26)
    )

    income = await repository(connection).run(plan(account, direction=Direction.INCOME))
    spending = await repository(connection).run(plan(account, direction=Direction.EXPENSE))
    everything = await repository(connection).run(plan(account, direction=Direction.ALL))

    assert income.total_minor == 245_000
    assert spending.total_minor == 9_000
    # With both directions mixed the total is signed: it is the net for the period.
    assert everything.total_minor == 236_000


async def test_it_returns_the_movements_backing_the_figure(
    connection: asyncpg.Connection,
) -> None:
    account = await create_account(connection)
    bank = await create_account(connection)
    await record_movement(
        connection,
        account,
        bank,
        -4_500,
        category="TRANSPORT_FUEL",
        merchant="Repsol",
        description="Compra en Repsol",
        occurred_at=on(3),
    )

    result = await repository(connection).run(
        plan(account, direction=Direction.EXPENSE, categories=("TRANSPORT_FUEL",))
    )

    assert len(result.supporting_movements) == 1
    movement = result.supporting_movements[0]
    assert movement.merchant_name == "Repsol"
    assert movement.category == "TRANSPORT_FUEL"
    assert movement.amount_minor == 4_500
    assert movement.direction == "EXPENSE"
    assert movement.description == "Compra en Repsol"


async def test_the_figure_can_be_asked_for_without_evidence(
    connection: asyncpg.Connection,
) -> None:
    account = await create_account(connection)
    bank = await create_account(connection)
    await record_movement(
        connection, account, bank, -4_500, category="GROCERIES", occurred_at=on(3)
    )

    result = await repository(connection).run(
        plan(account, direction=Direction.EXPENSE, supporting_limit=0)
    )

    assert result.total_minor == 4_500
    assert result.supporting_movements == []


async def test_the_trace_only_travels_when_asked_for(connection: asyncpg.Connection) -> None:
    account = await create_account(connection)

    without_trace = await repository(connection).run(plan(account))
    with_trace = await repository(connection).run(plan(account, explain=True))

    assert without_trace.trace is None
    assert with_trace.trace is not None
    assert "SELECT" in with_trace.trace.sql
    assert with_trace.trace.elapsed_ms >= 0


async def test_an_account_that_does_not_exist_does_not_return_zero(
    connection: asyncpg.Connection,
) -> None:
    with pytest.raises(AccountNotFoundError):
        await repository(connection).run(plan(uuid.uuid4()))
