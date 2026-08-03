"""Prepares and confirms a payment. The two-step flow of §4.1, and where the locking lives.

Preparing writes an intent and moves nothing. Confirming turns it into a booked double-entry
transaction. Everything that makes "confirm" safe to retry and safe to race is here or in
migration 0006, and each piece answers a specific way of paying twice:

**A resent prepare** finds the intent it already made, through `UNIQUE (account_id,
idempotency_key)`. A client whose request timed out and resends is the normal case.

**A repeated confirm** finds a terminal status. The intent row is taken with `FOR UPDATE` before
anything is decided, so of two concurrent confirmations one updates the row and the other reads
the result rather than the stale value.

**Two payments racing for one balance** are serialised on the account row, also with `FOR UPDATE`.
Without it both read the same balance, both find it sufficient, and the account goes overdrawn
from two individually reasonable requests.

The balance is recomputed at confirmation. The preview's figure was true when it was shown and the
account can be spent from by another route in between, so trusting it would be trusting a
statement about the past.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import asyncpg

from finguard_banking.domain.enums import EntryType, TransactionStatus, TransactionType
from finguard_banking.domain.errors import (
    AccountNotFoundError,
    ContactNotFoundError,
    InsufficientFundsError,
    IntentExpiredError,
    IntentNotConfirmableError,
    IntentNotFoundError,
)
from finguard_banking.domain.models.payment import PaymentPreview, PaymentReceipt
from finguard_banking.domain.value_objects.money import Money
from finguard_banking.infrastructure.persistence.financial_query_repository import (
    _DECLARE_ACCOUNT,
)

# How long a preview stands. Long enough for somebody to read it and answer, short enough that the
# balance it quoted is still roughly true.
DEFAULT_VALIDITY_SECONDS = 300

# The counterparty for money leaving the bank, as the seeded ledger models it: a payment to a phone
# number has no account here, so the other side of the double entry is the clearing account.
CLEARING_REFERENCE = "ES-COMPENSACION"

_AWAITING = "AWAITING_CONFIRMATION"

_BALANCE_SQL = """
    SELECT booked_minor, currency FROM assistant_read.account_balances_v1 WHERE account_id = $1
"""


class PostgresPaymentRepository:
    """Implements the payment flow over PostgreSQL. Runs as the owner: it writes the ledger."""

    def __init__(self, pool: asyncpg.Pool[Any]) -> None:
        self._pool = pool

    async def prepare(
        self,
        *,
        account_id: UUID,
        contact_id: UUID,
        amount: Money,
        key: str,
        valid_for_seconds: int = DEFAULT_VALIDITY_SECONDS,
    ) -> PaymentPreview:
        """Writes the intent and returns what the user is asked to confirm. Moves nothing."""
        async with self._pool.acquire() as connection, connection.transaction():
            await connection.execute(_DECLARE_ACCOUNT, str(account_id))

            contact = await connection.fetchrow(
                "SELECT display_name, phone FROM assistant_read.contacts_v1 WHERE contact_id = $1",
                contact_id,
            )
            if contact is None:
                # Not "not authorised": a contact of another account does not exist here, and a
                # caller who could tell those apart could enumerate other people's contacts.
                raise ContactNotFoundError(str(contact_id))

            balance = await self._balance(connection, account_id)

            existing = await connection.fetchrow(
                "SELECT id, expires_at FROM payment_intents "
                "WHERE account_id = $1 AND idempotency_key = $2",
                account_id,
                key,
            )
            if existing is not None:
                # The same intention, not a second one. Returned as it stands rather than
                # refreshed: the user is being shown the thing they already have.
                intent_id, expires_at = existing["id"], existing["expires_at"]
            else:
                intent_id = uuid.uuid4()
                expires_at = datetime.now(UTC) + timedelta(seconds=valid_for_seconds)
                await connection.execute(
                    "INSERT INTO payment_intents "
                    "(id, account_id, contact_id, amount_minor, currency, status, "
                    " idempotency_key, expires_at) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
                    intent_id,
                    account_id,
                    contact_id,
                    amount.amount_minor,
                    amount.currency,
                    _AWAITING,
                    key,
                    expires_at,
                )

        return PaymentPreview(
            intent_id=intent_id,
            recipient=contact["display_name"],
            recipient_phone=contact["phone"],
            amount=amount,
            balance_before=balance,
            balance_after=Money(balance.amount_minor - amount.amount_minor, balance.currency),
            expires_at=expires_at,
        )

    async def confirm(self, *, account_id: UUID, intent_id: UUID) -> PaymentReceipt:
        """Executes the intent, exactly once, and returns the receipt."""
        async with self._pool.acquire() as connection, connection.transaction():
            await connection.execute(_DECLARE_ACCOUNT, str(account_id))

            # The account first, and always in this order: two confirmations that take the rows in
            # opposite orders deadlock instead of serialising.
            locked = await connection.fetchrow(
                "SELECT id FROM accounts WHERE id = $1 FOR UPDATE", account_id
            )
            if locked is None:
                raise AccountNotFoundError(account_id)

            intent = await connection.fetchrow(
                "SELECT i.id, i.amount_minor, i.currency, i.status, i.expires_at, "
                "       c.display_name, c.phone "
                "FROM payment_intents i JOIN contacts c ON c.id = i.contact_id "
                "WHERE i.id = $1 AND i.account_id = $2 FOR UPDATE OF i",
                intent_id,
                account_id,
            )
            if intent is None:
                raise IntentNotFoundError(intent_id)
            if intent["status"] != _AWAITING:
                # The idempotent path. A client retrying a request whose response it never saw
                # lands here, and lands here having paid once.
                raise IntentNotConfirmableError(intent_id, intent["status"])
            if intent["expires_at"] <= datetime.now(UTC):
                raise IntentExpiredError(intent_id)

            amount = Money(intent["amount_minor"], intent["currency"])
            balance = await self._balance(connection, account_id)
            if balance.amount_minor < amount.amount_minor:
                # Recomputed here rather than trusted from the preview: the account can be spent
                # from by another route between the two steps.
                raise InsufficientFundsError(balance.amount_minor, amount.amount_minor)

            clearing = await connection.fetchval(
                "SELECT id FROM accounts WHERE account_reference = $1", CLEARING_REFERENCE
            )
            transaction_id = await self._book(
                connection,
                payer=account_id,
                clearing=clearing,
                amount=amount,
                recipient=intent["display_name"],
                phone=intent["phone"],
            )

            confirmed_at = datetime.now(UTC)
            await connection.execute(
                "UPDATE payment_intents SET status = 'CONFIRMED', transaction_id = $2, "
                "confirmed_at = $3 WHERE id = $1",
                intent_id,
                transaction_id,
                confirmed_at,
            )
            after = Money(balance.amount_minor - amount.amount_minor, balance.currency)

        return PaymentReceipt(
            intent_id=intent_id,
            transaction_id=transaction_id,
            recipient=intent["display_name"],
            recipient_phone=intent["phone"],
            amount=amount,
            balance_after=after,
            confirmed_at=confirmed_at,
        )

    async def cancel(self, *, account_id: UUID, intent_id: UUID) -> None:
        """Terminal, like confirming. A cancelled intent cannot come back: preparing again is
        cheap, and an intent that can be revived is an agreement with no expiry."""
        async with self._pool.acquire() as connection, connection.transaction():
            status = await connection.fetchval(
                "SELECT status FROM payment_intents WHERE id = $1 AND account_id = $2 FOR UPDATE",
                intent_id,
                account_id,
            )
            if status is None:
                raise IntentNotFoundError(intent_id)
            if status != _AWAITING:
                raise IntentNotConfirmableError(intent_id, status)
            await connection.execute(
                "UPDATE payment_intents SET status = 'CANCELLED' WHERE id = $1", intent_id
            )

    async def _balance(self, connection: Any, account_id: UUID) -> Money:
        row = await connection.fetchrow(_BALANCE_SQL, account_id)
        if row is None:
            raise AccountNotFoundError(account_id)
        # int(), because SUM over bigint is numeric in PostgreSQL and asyncpg hands that back
        # as Decimal. Money refuses anything that is not an integer number of minor units, which
        # is how this was caught rather than silently rounding somewhere later.
        return Money(int(row["booked_minor"]), row["currency"])

    async def _book(
        self,
        connection: Any,
        *,
        payer: UUID,
        clearing: UUID,
        amount: Money,
        recipient: str,
        phone: str,
    ) -> UUID:
        """One transaction through the lifecycle: PENDING, both entries, BOOKED.

        In that order because migration 0004 accepts entries only while pending, and booking
        re-asserts the balance. A payment therefore cannot be recorded half-written.
        """
        transaction_id = uuid.uuid4()
        metadata = json.dumps(
            {"merchant_name": recipient, "category": "TRANSFER_BIZUM", "phone": phone},
            ensure_ascii=False,
        )
        await connection.execute(
            "INSERT INTO ledger_transactions "
            "(id, transaction_type, status, reference, description, metadata, occurred_at) "
            "VALUES ($1, $2, $3, $4, $5, $6::jsonb, NOW())",
            transaction_id,
            TransactionType.BIZUM.value,
            TransactionStatus.PENDING.value,
            f"bizum-{transaction_id.hex[:16]}",
            f"Bizum a {recipient}",
            metadata,
        )
        for account, entry_type in (
            (payer, EntryType.DEBIT.value),
            (clearing, EntryType.CREDIT.value),
        ):
            await connection.execute(
                "INSERT INTO ledger_entries "
                "(id, transaction_id, account_id, entry_type, amount_minor, currency) "
                "VALUES ($1, $2, $3, $4, $5, $6)",
                uuid.uuid4(),
                transaction_id,
                account,
                entry_type,
                amount.amount_minor,
                amount.currency,
            )
        await connection.execute(
            "UPDATE ledger_transactions SET status = $2 WHERE id = $1",
            transaction_id,
            TransactionStatus.BOOKED.value,
        )
        return transaction_id
