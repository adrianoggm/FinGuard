"""`assert_intent_transition` guards four columns, and the destination is not among them.

Migration 0006's trigger says, in its own comment:

    The amount, the recipient and the account are what the user agreed to. Confirming has no
    business changing any of them, and a bug that did would move a different payment than the
    one that was previewed.

It then compares `account_id`, `contact_id`, `amount_minor` and `currency`. Those were every
column a destination had in 0006, when the only rail was Bizum and a recipient *was* a contact.
Migration 0012 added `rail`, `recipient_iban` and `recipient_name`; 0017 added `to_account_id`.
Neither redefined the function, so "the recipient" is no longer any of the columns it checks.

The gap is not about a hostile caller — banking does not authenticate its callers, and 0003 says
so plainly. It is the failure 0006 was written to stop: an intent is previewed and approved for
one IBAN, and the UPDATE that confirms it carries a different one. Every check constraint still
holds — `payment_intents_destination` requires a TRANSFER to *have* an IBAN and
`payment_intents_iban_shape` requires it to look like one, but neither pins its value — so the row
moves the money somewhere the holder never saw, and nothing in the database objects.

These tests assert the behaviour the comment promises. They fail. Left failing deliberately: the
fix is to redefine the trigger, which is a migration and not a test change.
"""

import uuid

import asyncpg
import pytest
from support import as_pool, create_account, create_contact, create_transaction, record_movement

from finguard_banking.bootstrap.config import get_settings
from finguard_banking.domain.value_objects.destination import ToAccount
from finguard_banking.domain.value_objects.iban import Iban
from finguard_banking.domain.value_objects.money import Money
from finguard_banking.infrastructure.persistence.payment_repository import (
    PostgresPaymentRepository,
)

pytestmark = pytest.mark.integration

EUR = "EUR"

# The IBAN the holder saw on the preview, and the one somebody else would rather be paid.
APPROVED_IBAN = "ES9121000418450200051332"
ATTACKER_IBAN = "ES6000491500051234567892"

CLEARING_REFERENCE = get_settings().clearing_account_reference


async def clearing(connection: asyncpg.Connection) -> uuid.UUID:
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


def payments(connection: asyncpg.Connection) -> PostgresPaymentRepository:
    settings = get_settings()
    return PostgresPaymentRepository(
        as_pool(connection),
        clearing_reference=settings.clearing_account_reference,
        validity_seconds=settings.payment_validity_seconds,
    )


async def a_transfer_awaiting_confirmation(connection: asyncpg.Connection) -> uuid.UUID:
    """A funded account and a TRANSFER intent it has previewed but not confirmed."""
    account = await create_account(connection)
    bank = await clearing(connection)
    await record_movement(connection, account=account, counterparty=bank, amount_minor=100_00)
    preview = await payments(connection).prepare(
        account_id=account,
        to=ToAccount(name="Elena Marin", iban=Iban(APPROVED_IBAN)),
        amount=Money(30_00, EUR),
        key="approved",
    )
    return preview.intent_id


async def test_confirming_cannot_change_the_iban_the_holder_approved(
    connection: asyncpg.Connection,
) -> None:
    """The headline. One UPDATE moves the intent to CONFIRMED *and* rewrites the destination.

    This is exactly the shape the trigger fires on — the status changes, so it does not take its
    early return — and exactly the shape its comment says it refuses. It does not refuse it.
    """
    intent = await a_transfer_awaiting_confirmation(connection)
    booked = await create_transaction(connection)

    with pytest.raises(asyncpg.PostgresError):
        await connection.execute(
            "UPDATE payment_intents "
            "SET status = 'CONFIRMED', transaction_id = $2, confirmed_at = NOW(), "
            "    recipient_iban = $3 "
            "WHERE id = $1",
            intent,
            booked,
            ATTACKER_IBAN,
        )

    assert (
        await connection.fetchval(
            "SELECT recipient_iban FROM payment_intents WHERE id = $1", intent
        )
        == APPROVED_IBAN
    ), "the confirmed intent points at an account the holder never approved"


async def test_a_pending_intent_cannot_have_its_iban_rewritten(
    connection: asyncpg.Connection,
) -> None:
    """The same hole one step earlier, and wider: with the status unchanged the trigger takes its
    `NEW.status = OLD.status` early return and inspects nothing at all. An intent can be
    redirected while it sits waiting, and then confirmed entirely legitimately."""
    intent = await a_transfer_awaiting_confirmation(connection)

    with pytest.raises(asyncpg.PostgresError):
        await connection.execute(
            "UPDATE payment_intents SET recipient_iban = $2 WHERE id = $1",
            intent,
            ATTACKER_IBAN,
        )

    assert (
        await connection.fetchval(
            "SELECT recipient_iban FROM payment_intents WHERE id = $1", intent
        )
        == APPROVED_IBAN
    ), "a payment awaiting confirmation was redirected before anyone confirmed it"


async def test_confirming_cannot_change_the_name_printed_beside_the_account(
    connection: asyncpg.Connection,
) -> None:
    """`recipient_name` is the other half of what the preview showed. The IBAN is what makes the
    transfer correct and the name is what makes it readable — a receipt naming somebody the holder
    never agreed to pay is a receipt that cannot be reconciled."""
    intent = await a_transfer_awaiting_confirmation(connection)
    booked = await create_transaction(connection)

    with pytest.raises(asyncpg.PostgresError):
        await connection.execute(
            "UPDATE payment_intents "
            "SET status = 'CONFIRMED', transaction_id = $2, confirmed_at = NOW(), "
            "    recipient_name = 'Somebody Else' "
            "WHERE id = $1",
            intent,
            booked,
        )

    assert (
        await connection.fetchval(
            "SELECT recipient_name FROM payment_intents WHERE id = $1", intent
        )
        == "Elena Marin"
    )


async def test_confirming_cannot_erase_the_contact_the_payment_came_from(
    connection: asyncpg.Connection,
) -> None:
    """`contact_id` *is* one of the four columns checked, and the check still misses this.

    `NEW.contact_id <> OLD.contact_id` is a three-valued comparison: against NULL it evaluates to
    NULL, not TRUE, so the whole OR chain is NULL and the `IF` is not taken. Setting a favourite's
    id to NULL therefore passes a guard written to catch it. Nothing else records which contact a
    transfer was aimed at, so this is the audit trail being erased at the moment of payment.
    """
    account = await create_account(connection)
    bank = await clearing(connection)
    await record_movement(connection, account=account, counterparty=bank, amount_minor=100_00)
    elena = await create_contact(connection, account, "Elena Marin", "+34600999888", APPROVED_IBAN)
    preview = await payments(connection).prepare(
        account_id=account,
        to=ToAccount(name="Elena Marin", iban=Iban(APPROVED_IBAN), contact_id=elena),
        amount=Money(30_00, EUR),
        key="from-a-favourite",
    )
    booked = await create_transaction(connection)

    with pytest.raises(asyncpg.PostgresError):
        await connection.execute(
            "UPDATE payment_intents "
            "SET status = 'CONFIRMED', transaction_id = $2, confirmed_at = NOW(), "
            "    contact_id = NULL "
            "WHERE id = $1",
            preview.intent_id,
            booked,
        )

    assert (
        await connection.fetchval(
            "SELECT contact_id FROM payment_intents WHERE id = $1", preview.intent_id
        )
        == elena
    )
