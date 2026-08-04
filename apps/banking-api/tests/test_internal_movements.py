"""Money between the holder's own accounts is not money that left.

Moving 200 € to the piggy bank is a debit on one account and a credit on another. Counted naively
it is an expense of 200 and an income of 200, and the monthly summary reports twice the movement
that happened.

It cannot happen today — one account per holder — which is exactly why it is here before the second
account exists. After it, the same change is a migration plus every figure anybody has already seen.
"""

import uuid
from datetime import date

import asyncpg
import pytest
from support import as_pool, create_account, record_movement

from finguard_banking.domain.enums import Direction, TransactionType
from finguard_banking.domain.models.financial_query import FinancialQuery
from finguard_banking.domain.value_objects.date_range import DateRange
from finguard_banking.infrastructure.persistence.financial_query_repository import (
    PostgresFinancialQueryRepository,
)
from finguard_banking.infrastructure.persistence.ledger_repository import PostgresLedgerRepository
from finguard_banking.infrastructure.persistence.query_compiler import compile_aggregate

pytestmark = pytest.mark.integration

WHOLE_YEAR = DateRange(start=date(2026, 1, 1), end=date(2026, 12, 31))


def plan(account: uuid.UUID, **over: object) -> FinancialQuery:
    fields: dict[str, object] = {"account_id": account, "date_range": WHOLE_YEAR}
    return FinancialQuery(**(fields | over))  # type: ignore[arg-type]


def queries(connection: asyncpg.Connection) -> PostgresFinancialQueryRepository:
    return PostgresFinancialQueryRepository(as_pool(connection), "Europe/Madrid")


async def a_current_and_a_piggy_bank(
    connection: asyncpg.Connection,
) -> tuple[uuid.UUID, uuid.UUID]:
    """One holder, two accounts, and 200 € moved from one to the other.

    Plus a real expense of 50 €, so the tests can tell "excluded correctly" from "found nothing".
    """
    current = await create_account(connection)
    piggy = await create_account(connection)
    shop = await create_account(connection)

    await record_movement(connection, account=current, counterparty=shop, amount_minor=100_000)
    await record_movement(
        connection,
        account=current,
        counterparty=shop,
        amount_minor=-5_000,
        occurred_at=None,
        category="GROCERIES",
    )
    await record_movement(
        connection,
        account=current,
        counterparty=piggy,
        amount_minor=-20_000,
        transaction_type=TransactionType.INTERNAL_TRANSFER.value,
    )
    return current, piggy


async def spent(connection: asyncpg.Connection, account: uuid.UUID, **over: object) -> int:
    result = await queries(connection).run(plan(account, direction=Direction.EXPENSE, **over))
    return result.total.amount_minor


async def test_moving_money_to_your_own_account_is_not_spending_it(
    connection: asyncpg.Connection,
) -> None:
    """The defect this exists to prevent. Without the exclusion the answer is 250 € — the 50 that
    left and the 200 that did not — and it is a real figure about a different question."""
    current, _ = await a_current_and_a_piggy_bank(connection)

    assert await spent(connection, current) == 5_000


async def test_receiving_it_in_the_other_account_is_not_income(
    connection: asyncpg.Connection,
) -> None:
    """The other leg, and the reason a naive count reports twice the movement that happened."""
    _, piggy = await a_current_and_a_piggy_bank(connection)
    result = await queries(connection).run(plan(piggy, direction=Direction.INCOME))

    assert result.total.amount_minor == 0


async def test_asking_about_them_on_purpose_is_a_real_question(
    connection: asyncpg.Connection,
) -> None:
    """ "¿Cuánto he movido a la hucha este año?" is worth asking and is not a question about
    spending, which is why this is a flag on the plan rather than a filter nobody can turn off."""
    current, _ = await a_current_and_a_piggy_bank(connection)

    assert await spent(connection, current, include_internal=True) == 25_000


async def test_the_balance_still_counts_it(connection: asyncpg.Connection) -> None:
    """Not an inconsistency: the money really did leave the current account. What changes is the
    question "how much did I spend", not the arithmetic of what is in the account."""
    current, piggy = await a_current_and_a_piggy_bank(connection)

    async def booked(account: uuid.UUID) -> int:
        async with connection.transaction():
            await connection.execute(
                "SELECT set_config('app.account_id', $1::text, true)", str(account)
            )
            balance = await PostgresLedgerRepository(as_pool(connection)).balance_of(account)
            return balance.booked.amount_minor

    assert await booked(current) == 75_000, "100.000 in, 5.000 spent, 20.000 to the piggy bank"
    assert await booked(piggy) == 20_000


def test_the_exclusion_is_in_the_sql_and_not_in_a_caller() -> None:
    """Every statement shares `_conditions`, so a query path that forgot this would be a path that
    also forgot the account. Asserted on the compiled text because that is the guarantee."""
    excluded = compile_aggregate(plan(uuid.uuid4()), "Europe/Madrid")
    included = compile_aggregate(plan(uuid.uuid4(), include_internal=True), "Europe/Madrid")

    assert "NOT t.is_internal" in excluded.sql
    assert "is_internal" not in included.sql


def test_the_dataset_has_a_holder_with_more_than_one_account() -> None:
    """The seed is what makes any of this reachable outside a fixture.

    One holder with a current account, a piggy bank and a fund — same customer, which is what
    makes them one person's accounts rather than three people's, and the property the RLS work
    will have to honour.
    """
    import re
    from pathlib import Path

    seed = (Path(__file__).resolve().parents[1] / "seeds" / "0001_demo.sql").read_text(
        encoding="utf-8"
    )
    rows = re.findall(
        r"'([0-9a-f-]{36})', '([0-9a-f-]{36})', 'ES-[^']*', 'EUR', 'ACTIVE', '(\w+)'", seed
    )

    by_customer: dict[str, list[str]] = {}
    for _, customer, kind in rows:
        by_customer.setdefault(customer, []).append(kind)

    several = [kinds for kinds in by_customer.values() if len(kinds) > 1]
    assert several, "no holder has a second account"
    assert {"CURRENT", "SAVINGS", "ASSET"} <= set(several[0])
    # And not everybody, deliberately: one account is still the ordinary case, and a dataset where
    # every holder had three would let the single-account path rot unnoticed.
    assert any(len(kinds) == 1 for kinds in by_customer.values())


def test_the_seeded_transfers_are_marked_internal() -> None:
    """A standing order to the piggy bank is one transaction with two of the holder's own entries.
    Emitted against the clearing account instead, it would be two unrelated movements that happen
    to cancel out — and both would count as spending."""
    from pathlib import Path

    seed = (Path(__file__).resolve().parents[1] / "seeds" / "0001_demo.sql").read_text(
        encoding="utf-8"
    )

    assert "'INTERNAL_TRANSFER'" in seed, "the dataset has no internal movements to exclude"
