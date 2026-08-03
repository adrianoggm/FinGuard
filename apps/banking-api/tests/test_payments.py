"""Preparing and confirming a payment, and the ways it could pay twice.

§4.1 puts human confirmation between an intent and its execution. These test the gap: that
preparing moves nothing, that confirming moves it exactly once, and that the three ways of paying
twice all fail — a resent prepare, a repeated confirm, and two confirmations racing for a balance
that covers only one.

The last needs a real database and two connections. It cannot be tested with a double, because
what is under test *is* the database's locking.
"""

import asyncio
import uuid

import asyncpg
import pytest
from support import as_pool, create_account, create_contact, record_movement

from finguard_banking.bootstrap.config import get_settings
from finguard_banking.domain.errors import (
    InsufficientFundsError,
    IntentExpiredError,
    IntentNotConfirmableError,
)
from finguard_banking.domain.value_objects.money import Money
from finguard_banking.infrastructure.persistence.ledger_repository import PostgresLedgerRepository
from finguard_banking.infrastructure.persistence.payment_repository import (
    CLEARING_REFERENCE,
    PostgresPaymentRepository,
)

pytestmark = pytest.mark.integration

EUR = "EUR"


async def clearing(connection: asyncpg.Connection) -> uuid.UUID:
    """The counterparty every movement needs. The seed has one; a fixture's transaction does not,
    so it is created on demand and named the same way."""
    existing = await connection.fetchval(
        "SELECT id FROM accounts WHERE account_reference = $1", CLEARING_REFERENCE
    )
    if existing is not None:
        return existing
    account = await create_account(connection)
    await connection.execute(
        "UPDATE accounts SET account_reference = $2 WHERE id = $1", account, CLEARING_REFERENCE
    )
    return account


async def funded(connection: asyncpg.Connection, cents: int) -> tuple[uuid.UUID, uuid.UUID]:
    """An account with a balance, and somebody in its contact list to pay."""
    account = await create_account(connection)
    bank = await clearing(connection)
    await record_movement(connection, account=account, counterparty=bank, amount_minor=cents)
    contact = await create_contact(connection, account, "Marta Ruiz Gomez", "+34600111222")
    return account, contact


def payments(connection: asyncpg.Connection) -> PostgresPaymentRepository:
    return PostgresPaymentRepository(as_pool(connection))


async def balance_of(connection: asyncpg.Connection, account: uuid.UUID) -> Money:
    async with connection.transaction():
        await connection.execute(
            "SELECT set_config('app.account_id', $1::text, true)", str(account)
        )
        return (await PostgresLedgerRepository(as_pool(connection)).balance_of(account)).booked


async def test_preparing_moves_no_money(connection: asyncpg.Connection) -> None:
    """The whole point of two steps. If preparing moved anything, the confirmation would be
    theatre."""
    account, contact = await funded(connection, 100_00)

    preview = await payments(connection).prepare(
        account_id=account, contact_id=contact, amount=Money(30_00, EUR), key="k1"
    )

    assert await balance_of(connection, account) == Money(100_00, EUR)
    assert preview.balance_before == Money(100_00, EUR)
    assert preview.balance_after == Money(70_00, EUR), "the preview has to say what it leaves"
    assert preview.recipient == "Marta Ruiz Gomez"
    assert preview.recipient_phone == "+34600111222"


async def test_confirming_moves_it_once(connection: asyncpg.Connection) -> None:
    account, contact = await funded(connection, 100_00)
    intent = await payments(connection).prepare(
        account_id=account, contact_id=contact, amount=Money(30_00, EUR), key="k1"
    )

    receipt = await payments(connection).confirm(account_id=account, intent_id=intent.intent_id)

    assert await balance_of(connection, account) == Money(70_00, EUR)
    assert receipt.amount == Money(30_00, EUR)
    assert receipt.recipient == "Marta Ruiz Gomez"


async def test_a_resent_prepare_is_the_same_intent(connection: asyncpg.Connection) -> None:
    """A client whose request timed out and resends is the normal case. Two intents from one
    intention is how somebody confirms a payment they thought they had cancelled."""
    account, contact = await funded(connection, 100_00)
    first = await payments(connection).prepare(
        account_id=account, contact_id=contact, amount=Money(30_00, EUR), key="same-key"
    )

    second = await payments(connection).prepare(
        account_id=account, contact_id=contact, amount=Money(30_00, EUR), key="same-key"
    )

    assert second.intent_id == first.intent_id


async def test_confirming_twice_pays_once(connection: asyncpg.Connection) -> None:
    account, contact = await funded(connection, 100_00)
    intent = await payments(connection).prepare(
        account_id=account, contact_id=contact, amount=Money(30_00, EUR), key="k1"
    )
    await payments(connection).confirm(account_id=account, intent_id=intent.intent_id)

    with pytest.raises(IntentNotConfirmableError):
        await payments(connection).confirm(account_id=account, intent_id=intent.intent_id)

    assert await balance_of(connection, account) == Money(70_00, EUR)


async def test_a_payment_beyond_the_balance_is_refused(connection: asyncpg.Connection) -> None:
    account, contact = await funded(connection, 20_00)
    intent = await payments(connection).prepare(
        account_id=account, contact_id=contact, amount=Money(30_00, EUR), key="k1"
    )

    with pytest.raises(InsufficientFundsError):
        await payments(connection).confirm(account_id=account, intent_id=intent.intent_id)

    assert await balance_of(connection, account) == Money(20_00, EUR)


async def test_the_balance_is_checked_at_confirmation_not_at_preparation(
    connection: asyncpg.Connection,
) -> None:
    """The preview stated a balance, and it can be spent before the confirmation arrives.
    Trusting the figure the preview computed is how an account goes overdrawn from two
    individually reasonable requests."""
    account, contact = await funded(connection, 100_00)
    bank = await clearing(connection)
    intent = await payments(connection).prepare(
        account_id=account, contact_id=contact, amount=Money(80_00, EUR), key="k1"
    )
    # The money leaves by another route between preparing and confirming.
    await record_movement(connection, account=account, counterparty=bank, amount_minor=-90_00)

    with pytest.raises(InsufficientFundsError):
        await payments(connection).confirm(account_id=account, intent_id=intent.intent_id)


async def test_an_expired_intent_cannot_be_confirmed(connection: asyncpg.Connection) -> None:
    account, contact = await funded(connection, 100_00)
    intent = await payments(connection).prepare(
        account_id=account, contact_id=contact, amount=Money(30_00, EUR), key="k1"
    )
    # The whole row is aged, both timestamps. `CHECK (expires_at > created_at)` refuses an intent
    # that is dead on arrival — correct, and it applies to updates too, so moving only the expiry
    # is refused as well. Ageing both is what waiting looks like without waiting.
    await connection.execute(
        "UPDATE payment_intents "
        "SET created_at = NOW() - INTERVAL '2 hours', expires_at = NOW() - INTERVAL '1 hour' "
        "WHERE id = $1",
        intent.intent_id,
    )

    with pytest.raises(IntentExpiredError):
        await payments(connection).confirm(account_id=account, intent_id=intent.intent_id)


async def test_a_cancelled_intent_cannot_be_confirmed(connection: asyncpg.Connection) -> None:
    account, contact = await funded(connection, 100_00)
    intent = await payments(connection).prepare(
        account_id=account, contact_id=contact, amount=Money(30_00, EUR), key="k1"
    )
    await payments(connection).cancel(account_id=account, intent_id=intent.intent_id)

    with pytest.raises(IntentNotConfirmableError):
        await payments(connection).confirm(account_id=account, intent_id=intent.intent_id)


async def test_the_payment_is_double_entry_and_booked(connection: asyncpg.Connection) -> None:
    """Two entries that cancel out, in one booked transaction. The balanced trigger already
    refuses anything else, so this asserts the shape rather than the invariant."""
    account, contact = await funded(connection, 100_00)
    intent = await payments(connection).prepare(
        account_id=account, contact_id=contact, amount=Money(30_00, EUR), key="k1"
    )

    receipt = await payments(connection).confirm(account_id=account, intent_id=intent.intent_id)

    rows = await connection.fetch(
        "SELECT entry_type, amount_minor FROM ledger_entries WHERE transaction_id = $1",
        receipt.transaction_id,
    )
    assert sorted((row["entry_type"], row["amount_minor"]) for row in rows) == [
        ("CREDIT", 30_00),
        ("DEBIT", 30_00),
    ]
    status = await connection.fetchval(
        "SELECT status FROM ledger_transactions WHERE id = $1", receipt.transaction_id
    )
    assert status == "BOOKED"


async def test_the_intent_and_the_ledger_cannot_disagree(connection: asyncpg.Connection) -> None:
    """A CHECK, not a convention: `transaction_id` is set if and only if the status is CONFIRMED.
    Without it a crash between booking and updating leaves a payment that happened and an intent
    that says it did not, and the retry pays again."""
    account, contact = await funded(connection, 100_00)
    intent = await payments(connection).prepare(
        account_id=account, contact_id=contact, amount=Money(30_00, EUR), key="k1"
    )

    with pytest.raises(asyncpg.PostgresError):
        await connection.execute(
            "UPDATE payment_intents SET status = 'CONFIRMED' WHERE id = $1", intent.intent_id
        )


async def test_two_confirmations_racing_for_one_balance_pay_once() -> None:
    """The one that needs two connections, because what is under test is the database's locking.

    Two intents, each affordable alone, together more than the balance. Without `FOR UPDATE` on
    the account both read the same figure, both find it sufficient, and the account goes overdrawn
    from two requests that were each perfectly reasonable.

    Committed data, so it cannot use the rollback fixture — it cleans up after itself.
    """
    settings = get_settings()
    setup: asyncpg.Connection = await asyncpg.connect(settings.database_url)
    account = bank = None
    try:
        async with setup.transaction():
            bank = await clearing(setup)
            account = await create_account(setup)
            await record_movement(setup, account=account, counterparty=bank, amount_minor=100_00)
            contact = await create_contact(setup, account, "Marta Ruiz Gomez", "+34600111222")

            first = await PostgresPaymentRepository(as_pool(setup)).prepare(
                account_id=account, contact_id=contact, amount=Money(80_00, EUR), key="a"
            )
            second = await PostgresPaymentRepository(as_pool(setup)).prepare(
                account_id=account, contact_id=contact, amount=Money(80_00, EUR), key="b"
            )

        left: asyncpg.Pool[object] = await asyncpg.create_pool(
            settings.database_url, min_size=1, max_size=1
        )
        right: asyncpg.Pool[object] = await asyncpg.create_pool(
            settings.database_url, min_size=1, max_size=1
        )
        try:
            outcomes = await asyncio.gather(
                PostgresPaymentRepository(left).confirm(
                    account_id=account, intent_id=first.intent_id
                ),
                PostgresPaymentRepository(right).confirm(
                    account_id=account, intent_id=second.intent_id
                ),
                return_exceptions=True,
            )
        finally:
            await left.close()
            await right.close()

        refused = [o for o in outcomes if isinstance(o, InsufficientFundsError)]
        paid = [o for o in outcomes if not isinstance(o, BaseException)]

        assert len(paid) == 1, f"both payments went through: {outcomes}"
        assert len(refused) == 1, f"the loser failed for the wrong reason: {outcomes}"

        remaining = await setup.fetchval(
            "SELECT COALESCE(SUM(CASE WHEN entry_type = 'CREDIT' THEN amount_minor "
            "ELSE -amount_minor END), 0) FROM ledger_entries WHERE account_id = $1",
            account,
        )
        assert remaining == 20_00, "the account went overdrawn"
    finally:
        # Committed rows, so they are removed by hand. The ledger refuses DELETE on its own
        # tables, which is why the payment's transaction is cancelled rather than erased.
        if account is not None:
            await setup.execute("DELETE FROM payment_intents WHERE account_id = $1", account)
            await setup.execute("DELETE FROM contacts WHERE account_id = $1", account)
        await setup.close()
