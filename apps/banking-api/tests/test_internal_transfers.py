"""Moving money between a holder's own accounts.

The ledger has recognised an `INTERNAL_TRANSFER` since 0013 and nothing could create one: the only
writer was the dataset generator, in raw SQL. So a holder could be shown a piggy bank and had no
way to put anything in it.

Three properties, and two of them are about what this must *not* become.

    it is a real double entry     the credit lands in the other account, not in clearing
    it is not spending            "¿cuánto gasté?" must not move by a euro
    it stays inside one holder    the destination is checked, and a foreign key does not check it
"""

import uuid
from datetime import date

import asyncpg
import pytest
from support import as_pool, create_account, record_movement

from finguard_banking.application.commands.payments import (
    ConfirmPaymentCommand,
    ConfirmPaymentHandler,
    PreparePaymentCommand,
    PreparePaymentHandler,
)
from finguard_banking.domain.enums import Direction, Rail
from finguard_banking.domain.errors import AccountNotFoundError, InvalidValueError
from finguard_banking.domain.models.financial_query import FinancialQuery
from finguard_banking.domain.value_objects.date_range import DateRange
from finguard_banking.domain.value_objects.destination import ToOwnAccount
from finguard_banking.domain.value_objects.money import Money
from finguard_banking.infrastructure.persistence.account_repository import (
    PostgresAccountRepository,
)
from finguard_banking.infrastructure.persistence.financial_query_repository import (
    PostgresFinancialQueryRepository,
)
from finguard_banking.infrastructure.persistence.payment_repository import (
    PostgresPaymentRepository,
)

pytestmark = pytest.mark.integration

WHOLE_YEAR = DateRange(start=date(2026, 1, 1), end=date(2026, 12, 31))
CLEARING = "ES-CLEARING"


async def a_holder_with_a_piggy_bank(
    connection: asyncpg.Connection,
) -> tuple[uuid.UUID, uuid.UUID]:
    """A current account with money in it and an empty savings account, both one person's."""
    customer = uuid.uuid4()
    await connection.execute(
        "INSERT INTO customers (id, full_name) VALUES ($1, $2)", customer, "Laura"
    )
    current = await create_account(connection, customer_id=customer)
    piggy = await create_account(connection, customer_id=customer, kind="SAVINGS")
    employer = await create_account(connection)
    await record_movement(connection, account=current, counterparty=employer, amount_minor=1_000_00)
    # The clearing account the payment repository balances outgoing money against. Present so a
    # Bizum in the same test file has somewhere to land, and deliberately not used by a transfer.
    await connection.execute(
        "INSERT INTO accounts (id, customer_id, account_reference, currency, status, kind) "
        "VALUES ($1, $2, $3, 'EUR', 'ACTIVE', 'CURRENT')",
        uuid.uuid4(),
        customer,
        CLEARING,
    )
    return current, piggy


def machinery(
    connection: asyncpg.Connection,
) -> tuple[PreparePaymentHandler, ConfirmPaymentHandler]:
    pool = as_pool(connection)
    payments = PostgresPaymentRepository(pool, clearing_reference=CLEARING, validity_seconds=300)
    return (
        PreparePaymentHandler(_NoContacts(), payments, PostgresAccountRepository(pool)),
        ConfirmPaymentHandler(payments),
    )


class _NoContacts:
    """A transfer between own accounts resolves nobody, so the directory must never be reached."""

    async def resolve(self, account_id: uuid.UUID, name: str) -> None:  # pragma: no cover
        raise AssertionError("an internal transfer looked somebody up")

    async def one(self, account_id: uuid.UUID, contact_id: uuid.UUID) -> None:  # pragma: no cover
        raise AssertionError("an internal transfer looked somebody up")

    async def all_of(self, account_id: uuid.UUID) -> tuple[()]:  # pragma: no cover
        return ()


async def move(
    connection: asyncpg.Connection, *, source: uuid.UUID, target: uuid.UUID, minor: int
) -> None:
    prepare, confirm = machinery(connection)
    preview = await prepare.handle(
        PreparePaymentCommand(
            account_id=source,
            amount=Money(minor, "EUR"),
            idempotency_key=str(uuid.uuid4()),
            rail=Rail.INTERNAL,
            to_account_id=target,
        )
    )
    await confirm.handle(ConfirmPaymentCommand(account_id=source, intent_id=preview.intent_id))


async def balance_of(connection: asyncpg.Connection, account: uuid.UUID) -> int:
    async with connection.transaction():
        await connection.execute(
            "SELECT set_config('app.customer_id', (SELECT customer_id::text FROM accounts "
            "WHERE id = $1), true)",
            account,
        )
        return int(
            await connection.fetchval(
                "SELECT booked_minor FROM assistant_read.account_balances_v1 WHERE account_id = $1",
                account,
            )
        )


# --- what it does -----------------------------------------------------------------------


async def test_the_money_arrives_in_the_other_account(connection: asyncpg.Connection) -> None:
    """The credit leg lands in the piggy bank rather than in clearing, which is what makes this a
    real double entry and not a payment out with a matching payment in."""
    current, piggy = await a_holder_with_a_piggy_bank(connection)

    await move(connection, source=current, target=piggy, minor=200_00)

    assert await balance_of(connection, piggy) == 200_00
    assert await balance_of(connection, current) == 800_00


async def test_the_preview_names_the_account_it_is_going_to(
    connection: asyncpg.Connection,
) -> None:
    """What somebody is asked to approve. The holder's own label for the account, snapshotted onto
    the intent — a name edited between preparing and confirming must not change what was agreed."""
    current, piggy = await a_holder_with_a_piggy_bank(connection)
    prepare, _ = machinery(connection)

    preview = await prepare.handle(
        PreparePaymentCommand(
            account_id=current,
            amount=Money(200_00, "EUR"),
            idempotency_key=str(uuid.uuid4()),
            rail=Rail.INTERNAL,
            to_account_id=piggy,
        )
    )

    assert isinstance(preview.to, ToOwnAccount)
    assert preview.to.account_id == piggy
    assert preview.balance_after.amount_minor == 800_00


# --- what it must not become ------------------------------------------------------------


async def test_it_is_not_spending(connection: asyncpg.Connection) -> None:
    """The property this whole rail exists to protect. 200 € into the piggy bank is money that
    moved, not money that left, and a holder who asks what they spent must get the same figure
    before and after — ADR-009 §2."""
    current, piggy = await a_holder_with_a_piggy_bank(connection)
    shop = await create_account(connection)
    await record_movement(connection, account=current, counterparty=shop, amount_minor=-50_00)
    queries = PostgresFinancialQueryRepository(as_pool(connection), "Europe/Madrid")
    plan = FinancialQuery(account_id=current, date_range=WHOLE_YEAR, direction=Direction.EXPENSE)
    before = (await queries.run(plan)).total.amount_minor

    await move(connection, source=current, target=piggy, minor=200_00)

    assert (await queries.run(plan)).total.amount_minor == before == 50_00


async def test_it_is_visible_when_asked_for(connection: asyncpg.Connection) -> None:
    """Excluded from spending is not hidden. A list that cannot show a 200 € transfer does not
    reconcile with the balance, and somebody hunting for where it went has nowhere to look."""
    current, piggy = await a_holder_with_a_piggy_bank(connection)
    await move(connection, source=current, target=piggy, minor=200_00)
    queries = PostgresFinancialQueryRepository(as_pool(connection), "Europe/Madrid")

    answer = await queries.run(
        FinancialQuery(
            account_id=current,
            date_range=WHOLE_YEAR,
            direction=Direction.ALL,
            include_internal=True,
            supporting_limit=20,
        )
    )

    moved = [m for m in answer.supporting_movements if m.is_internal]
    # Negative, because `Direction.ALL` returns the signed amount and this leg is the debit. The
    # sign is the whole point of an ALL query: it is what tells a charge from a payment in.
    assert [m.amount.amount_minor for m in moved] == [-200_00]
    assert moved[0].category is None, "a transfer filed under a category joins a spending breakdown"


async def test_another_holders_account_is_not_a_destination(
    connection: asyncpg.Connection,
) -> None:
    """The one that matters. A foreign key says the account exists; it does not say it is yours,
    and an internal transfer to a stranger is a payment whose preview names nobody."""
    current, _ = await a_holder_with_a_piggy_bank(connection)
    stranger = await create_account(connection)
    prepare, _ = machinery(connection)

    with pytest.raises(AccountNotFoundError):
        await prepare.handle(
            PreparePaymentCommand(
                account_id=current,
                amount=Money(200_00, "EUR"),
                idempotency_key=str(uuid.uuid4()),
                rail=Rail.INTERNAL,
                to_account_id=stranger,
            )
        )


def test_an_account_cannot_transfer_to_itself() -> None:
    """A transaction debiting and crediting one account balances perfectly, so every integrity
    check in this ledger would accept it and the balance would not move. Refused at construction,
    and again by a CHECK constraint, because a form with two identical dropdowns defaults to it."""
    account = uuid.uuid4()

    with pytest.raises(InvalidValueError):
        PreparePaymentCommand(
            account_id=account,
            amount=Money(200_00, "EUR"),
            idempotency_key="k",
            rail=Rail.INTERNAL,
            to_account_id=account,
        )


def test_an_own_account_is_not_a_destination_for_the_other_rails() -> None:
    with pytest.raises(InvalidValueError):
        PreparePaymentCommand(
            account_id=uuid.uuid4(),
            amount=Money(200_00, "EUR"),
            idempotency_key="k",
            rail=Rail.TRANSFER,
            to_account_id=uuid.uuid4(),
        )


def test_the_internal_rail_needs_an_own_account() -> None:
    """Otherwise it would fall through to the contact lookup and become a Bizum with a rail label
    on it, which is a payment leaving the household under the name of one that does not."""
    with pytest.raises(InvalidValueError):
        PreparePaymentCommand(
            account_id=uuid.uuid4(),
            amount=Money(200_00, "EUR"),
            idempotency_key="k",
            rail=Rail.INTERNAL,
            recipient_name="Marta",
        )
