"""The audit trail: what was attempted, by whom, and how it ended.

An audit that only records what succeeded cannot answer the question anybody actually asks it —
"did somebody try to empty this account?" — so a refusal is recorded exactly as carefully as a
payment. And it cannot be rewritten: a log the service can edit is a log that proves nothing about
the service.

The trail records *attempts*. The record of the money itself is the ledger, which is immutable for
the same reason and already tested in `test_ledger_integrity.py`.
"""

import uuid

import asyncpg
import pytest
from support import as_pool, create_account, create_contact, record_movement

from finguard_banking.application import (
    CommandBus,
    ConfirmPaymentCommand,
    ConfirmPaymentHandler,
    PreparePaymentCommand,
    PreparePaymentHandler,
)
from finguard_banking.domain.errors import InsufficientFundsError
from finguard_banking.domain.models.audit_event import AuditEvent
from finguard_banking.domain.value_objects.money import Money
from finguard_banking.infrastructure.persistence.audit_log import PostgresAuditLog
from finguard_banking.infrastructure.persistence.contact_repository import PostgresContactRepository
from finguard_banking.infrastructure.persistence.payment_repository import (
    CLEARING_REFERENCE,
    PostgresPaymentRepository,
)

pytestmark = pytest.mark.integration

EUR = "EUR"


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


async def audited_bus(connection: asyncpg.Connection, cents: int) -> tuple[CommandBus, uuid.UUID]:
    """A bus that audits, wired the way the composition root wires it."""
    account = await create_account(connection)
    await record_movement(
        connection, account=account, counterparty=await clearing(connection), amount_minor=cents
    )
    await create_contact(connection, account, "Marta Ruiz Gomez", "+34600111222")

    pool = as_pool(connection)
    payments = PostgresPaymentRepository(pool)
    bus = CommandBus(audit=PostgresAuditLog(pool))
    bus.register(
        PreparePaymentCommand, PreparePaymentHandler(PostgresContactRepository(pool), payments)
    )
    bus.register(ConfirmPaymentCommand, ConfirmPaymentHandler(payments))
    return bus, account


async def events(connection: asyncpg.Connection, account: uuid.UUID) -> list[asyncpg.Record]:
    return await connection.fetch(
        "SELECT command, outcome, detail FROM audit_events WHERE account_id = $1 ORDER BY id",
        account,
    )


async def test_a_payment_leaves_a_trail_of_both_steps(connection: asyncpg.Connection) -> None:
    bus, account = await audited_bus(connection, 100_00)

    intent = await bus.dispatch(
        PreparePaymentCommand(
            account_id=account,
            recipient_name="Marta",
            amount=Money(30_00, EUR),
            idempotency_key="k1",
        )
    )
    await bus.dispatch(ConfirmPaymentCommand(account_id=account, intent_id=intent.intent_id))

    recorded = await events(connection, account)
    assert [(row["command"], row["outcome"]) for row in recorded] == [
        ("PreparePaymentCommand", "OK"),
        ("ConfirmPaymentCommand", "OK"),
    ]


async def test_a_refusal_is_recorded_as_carefully_as_a_payment(
    connection: asyncpg.Connection,
) -> None:
    """The reason the trail exists. A log of successes cannot answer "did somebody try to empty
    this account", which is the question it will be asked."""
    bus, account = await audited_bus(connection, 20_00)
    intent = await bus.dispatch(
        PreparePaymentCommand(
            account_id=account,
            recipient_name="Marta",
            amount=Money(30_00, EUR),
            idempotency_key="k1",
        )
    )

    with pytest.raises(InsufficientFundsError):
        await bus.dispatch(ConfirmPaymentCommand(account_id=account, intent_id=intent.intent_id))

    recorded = await events(connection, account)
    assert recorded[-1]["command"] == "ConfirmPaymentCommand"
    assert recorded[-1]["outcome"] == "insufficient_funds"


async def test_the_trail_says_what_was_attempted(connection: asyncpg.Connection) -> None:
    """The amount and the recipient, not just the verb. "A payment was attempted" is not an audit
    trail: the figure is the thing anybody reading it needs."""
    bus, account = await audited_bus(connection, 100_00)

    await bus.dispatch(
        PreparePaymentCommand(
            account_id=account,
            recipient_name="Marta",
            amount=Money(30_00, EUR),
            idempotency_key="k1",
        )
    )

    detail = (await events(connection, account))[0]["detail"]
    assert '"amount_minor": 3000' in detail
    assert "Marta" in detail


async def test_the_trail_cannot_be_rewritten(connection: asyncpg.Connection) -> None:
    """A log the service can edit proves nothing about the service. Enforced by the database, not
    by nobody writing the UPDATE."""
    account = await create_account(connection)
    await PostgresAuditLog(as_pool(connection)).record(
        AuditEvent(command="ConfirmPaymentCommand", account_id=account, outcome="OK", detail={})
    )

    with pytest.raises(asyncpg.PostgresError):
        await connection.execute("UPDATE audit_events SET outcome = 'OK' WHERE outcome = 'OK'")
    with pytest.raises(asyncpg.PostgresError):
        await connection.execute("DELETE FROM audit_events WHERE account_id = $1", account)


async def test_the_trail_cannot_be_emptied_either(connection: asyncpg.Connection) -> None:
    """TRUNCATE is not DELETE, and a per-row trigger never sees it.

    Found by trying it: the append-only trigger refused DELETE and let TRUNCATE through, which
    empties the whole trail in one statement. A log that can be emptied is not append-only, so the
    statement-level trigger is the other half of the claim.
    """
    account = await create_account(connection)
    await PostgresAuditLog(as_pool(connection)).record(
        AuditEvent(command="ConfirmPaymentCommand", account_id=account, outcome="OK", detail={})
    )

    with pytest.raises(asyncpg.PostgresError):
        await connection.execute("TRUNCATE audit_events")
