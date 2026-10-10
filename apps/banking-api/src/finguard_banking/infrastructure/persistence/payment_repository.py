"""Prepares and confirms a payment. The two-step flow of §4.1, and where the locking lives.

Preparing writes an intent and moves nothing. Confirming turns it into a booked double-entry
transaction. Everything that makes "confirm" safe to retry and safe to race is here or in
migration 0006, and each piece answers a specific way of paying twice:

**A resent prepare** finds the intent it already made, through `UNIQUE (account_id,
idempotency_key)`. A client whose request timed out and resends is the normal case.

**A repeated confirm** for an already confirmed intent replays its saved receipt. The intent row is
taken with `FOR UPDATE` before anything is decided, so concurrent confirmations serialize: one
books the ledger and stores the receipt; the other returns that same receipt without booking again.
Cancelled and expired intents remain terminal refusals.

**A repeated cancel** for an already cancelled intent is a no-op. A conversation worker may have
closed the bank intent and crashed before committing its canonical state; replaying the same cancel
must let that turn continue. Confirmed and expired intents are still refused.

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

from finguard_banking.domain.enums import EntryType, Rail, TransactionStatus, TransactionType
from finguard_banking.domain.errors import (
    AccountNotFoundError,
    ContactNotFoundError,
    InsufficientFundsError,
    IntentExpiredError,
    IntentNotConfirmableError,
    IntentNotFoundError,
    IntentRecipientSnapshotMissingError,
    KeyReusedForAnotherPaymentError,
)
from finguard_banking.domain.models.payment import PaymentPreview, PaymentReceipt
from finguard_banking.domain.value_objects.destination import (
    Destination,
    ToAccount,
    ToOwnAccount,
    ToPhone,
    rail_of,
)
from finguard_banking.domain.value_objects.iban import Iban
from finguard_banking.domain.value_objects.money import Money
from finguard_banking.infrastructure.persistence.financial_query_repository import (
    declare,
)

_AWAITING = "AWAITING_CONFIRMATION"
_CANCELLED = "CANCELLED"


def _must_be_the_same_payment(
    stored: asyncpg.Record, *, to: Destination, amount: Money, rail: Rail, key: str
) -> None:
    """Refuses a key that has already been used for a different payment.

    Without this, replaying a key answers with the stored intent while the preview is built from
    the *current* request — so a caller who corrected 200 € to 300 € and kept the key was shown
    300 € above an identifier that pays 200 €, and confirming it moved 200 €. An approval binding
    something other than what was shown defeats the entire two-step flow, and neither side would
    report anything wrong.

    Compared over what the caller chose, which is what the key is a promise about. `expires_at`
    and the status are this service's own and change on their own.
    """
    asked = (
        rail.value,
        to.contact_id if isinstance(to, ToPhone | ToAccount) else None,
        str(to.iban) if isinstance(to, ToAccount) else None,
        to.account_id if isinstance(to, ToOwnAccount) else None,
        amount.amount_minor,
        amount.currency,
    )
    if asked != (
        stored["rail"],
        stored["contact_id"],
        stored["recipient_iban"],
        stored["to_account_id"],
        stored["amount_minor"],
        stored["currency"],
    ):
        raise KeyReusedForAnotherPaymentError(key)


_BALANCE_SQL = """
    SELECT booked_minor, currency FROM assistant_read.account_balances_v1 WHERE account_id = $1
"""


class PostgresPaymentRepository:
    """Implements the payment flow over PostgreSQL. Runs as the owner: it writes the ledger."""

    def __init__(
        self, pool: asyncpg.Pool[Any], *, clearing_reference: str, validity_seconds: int
    ) -> None:
        """No defaults, and the absence is the design. How long a preview stands is a product
        decision and the clearing account is a fact about this ledger; both belong to a deployment,
        beside the reason each one is what it is, and not here where changing one is a commit.

        Public because configuration that reaches an adapter without being observable is
        configuration nobody can prove arrived.
        """
        self._pool = pool
        self.clearing_reference = clearing_reference
        self.validity_seconds = validity_seconds

    async def prepare(
        self,
        *,
        account_id: UUID,
        to: Destination,
        amount: Money,
        key: str,
    ) -> PaymentPreview:
        """Writes the intent and returns what the user is asked to confirm. Moves nothing.

        The destination arrives resolved. A Bizum contact is re-read through the caller-scoped
        view and its name and phone are snapshotted onto the intent, so confirmation pays exactly
        the destination shown in the preview even if the contact is edited later.
        """
        rail = rail_of(to)
        async with self._pool.acquire() as connection, connection.transaction():
            await declare(connection, str(account_id))

            if isinstance(to, ToPhone):
                known = await connection.fetchrow(
                    "SELECT contact_id, display_name, phone "
                    "FROM assistant_read.contacts_v1 WHERE contact_id = $1",
                    to.contact_id,
                )
                if known is None:
                    # Not "not authorised": a contact of another account does not exist here,
                    # and a caller who could tell those apart could enumerate other people's.
                    raise ContactNotFoundError(str(to.contact_id))
                to = ToPhone(
                    contact_id=known["contact_id"],
                    name=known["display_name"],
                    phone=known["phone"],
                )

            balance = await self._balance(connection, account_id)

            existing = await connection.fetchrow(
                "SELECT id, expires_at, status, rail, contact_id, recipient_name, recipient_phone, "
                "       recipient_iban, to_account_id, "
                "       amount_minor, currency "
                "FROM payment_intents WHERE account_id = $1 AND idempotency_key = $2",
                account_id,
                key,
            )
            if existing is not None:
                _must_be_the_same_payment(existing, to=to, amount=amount, rail=rail, key=key)
                if existing["rail"] == Rail.BIZUM.value and (
                    not existing["recipient_name"] or not existing["recipient_phone"]
                ):
                    if existing["status"] == _AWAITING:
                        raise IntentRecipientSnapshotMissingError(existing["id"])
                    raise IntentNotConfirmableError(existing["id"], existing["status"])
                # The same intention, not a second one. Returned as it stands rather than
                # refreshed: the user is being shown the thing they already have.
                intent_id, expires_at = existing["id"], existing["expires_at"]
                to = _destination_of(existing)
            else:
                intent_id = uuid.uuid4()
                expires_at = datetime.now(UTC) + timedelta(seconds=self.validity_seconds)
                await connection.execute(
                    "INSERT INTO payment_intents "
                    "(id, account_id, contact_id, rail, recipient_name, recipient_phone, "
                    " recipient_iban, to_account_id, amount_minor, currency, status, "
                    " idempotency_key, expires_at) "
                    "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)",
                    intent_id,
                    account_id,
                    to.contact_id if isinstance(to, ToPhone | ToAccount) else None,
                    # Named rather than defaulted. The column carried `DEFAULT 'BIZUM'` only long
                    # enough to backfill the rows that existed; a rail nobody chose is a rail
                    # somebody forgets to send, and this is the one statement that decides where
                    # money goes.
                    rail.value,
                    # Snapshot contact destinations so later edits cannot redirect approval.
                    to.name if isinstance(to, ToPhone | ToAccount | ToOwnAccount) else None,
                    to.phone if isinstance(to, ToPhone) else None,
                    str(to.iban) if isinstance(to, ToAccount) else None,
                    to.account_id if isinstance(to, ToOwnAccount) else None,
                    amount.amount_minor,
                    amount.currency,
                    _AWAITING,
                    key,
                    expires_at,
                )

        return PaymentPreview(
            intent_id=intent_id,
            to=to,
            amount=amount,
            balance_before=balance,
            balance_after=Money(balance.amount_minor - amount.amount_minor, balance.currency),
            expires_at=expires_at,
        )

    async def confirm(self, *, account_id: UUID, intent_id: UUID) -> PaymentReceipt:
        """Executes the intent, exactly once, and returns the receipt."""
        async with self._pool.acquire() as connection, connection.transaction():
            await declare(connection, str(account_id))

            # The account first, and always in this order: two confirmations that take the rows in
            # opposite orders deadlock instead of serialising.
            locked = await connection.fetchrow(
                "SELECT id FROM accounts WHERE id = $1 FOR UPDATE", account_id
            )
            if locked is None:
                raise AccountNotFoundError(account_id)

            intent = await connection.fetchrow(
                "SELECT i.id, i.amount_minor, i.currency, i.status, i.expires_at, i.rail, "
                "       i.transaction_id, i.confirmed_at, "
                "       i.contact_id, i.recipient_name, i.recipient_phone, i.recipient_iban, "
                "       i.to_account_id, i.receipt_payload "
                "FROM payment_intents i "
                "WHERE i.id = $1 AND i.account_id = $2 FOR UPDATE OF i",
                intent_id,
                account_id,
            )
            if intent is None:
                raise IntentNotFoundError(intent_id)
            if intent["status"] == "CONFIRMED":
                # A graph worker may have received the bank's success and crashed before its
                # canonical conversation CAS. Replaying the same intent must recover the exact
                # receipt, not turn a successful payment into an ambiguous terminal refusal.
                if intent["receipt_payload"] is not None:
                    receipt = _receipt_from_payload(intent["receipt_payload"])
                    if (
                        receipt.intent_id != intent_id
                        or receipt.transaction_id != intent["transaction_id"]
                        or receipt.amount.amount_minor != intent["amount_minor"]
                        or receipt.amount.currency != intent["currency"].strip()
                        or receipt.confirmed_at != intent["confirmed_at"]
                        or not _receipt_matches_intent(receipt, intent)
                    ):
                        raise ValueError(
                            "stored payment receipt does not match its confirmed intent"
                        )
                    return receipt
                # Intents confirmed before receipt snapshots were introduced have no replay data.
                raise IntentNotConfirmableError(intent_id, intent["status"])
            if intent["status"] != _AWAITING:
                raise IntentNotConfirmableError(intent_id, intent["status"])
            if intent["expires_at"] <= datetime.now(UTC):
                raise IntentExpiredError(intent_id)
            if intent["rail"] == Rail.BIZUM.value and (
                not intent["recipient_name"] or not intent["recipient_phone"]
            ):
                # Older pending intents cannot prove which phone number the user saw. Require a
                # fresh preview instead of resolving the mutable contact at confirmation time.
                raise IntentRecipientSnapshotMissingError(intent_id)

            # Off the intent, never off the contact. A favourite edited between preparing and
            # confirming must not move money approved against the old account, and the inner
            # join this replaced would have read exactly that.
            destination = _destination_of(intent)
            amount = Money(intent["amount_minor"], intent["currency"])
            balance = await self._balance(connection, account_id)
            if balance.amount_minor < amount.amount_minor:
                # Recomputed here rather than trusted from the preview: the account can be spent
                # from by another route between the two steps.
                raise InsufficientFundsError(balance.amount_minor, amount.amount_minor)

            # Where the credit leg lands, and it is the whole difference between the rails at this
            # layer. Money that leaves the household is balanced against the clearing account;
            # money that stays lands in the holder's other account, which is what makes the pair a
            # real double entry rather than a payment out with a matching payment in.
            if isinstance(destination, ToOwnAccount):
                counterparty = destination.account_id
            else:
                counterparty = await connection.fetchval(
                    "SELECT id FROM accounts WHERE account_reference = $1", self.clearing_reference
                )
            transaction_id = await self._book(
                connection,
                payer=account_id,
                clearing=counterparty,
                amount=amount,
                recipient=destination.name,
                phone=destination.phone if isinstance(destination, ToPhone) else None,
                internal=isinstance(destination, ToOwnAccount),
            )

            confirmed_at = datetime.now(UTC)
            after = Money(balance.amount_minor - amount.amount_minor, balance.currency)
            receipt = PaymentReceipt(
                intent_id=intent_id,
                transaction_id=transaction_id,
                to=destination,
                amount=amount,
                balance_after=after,
                confirmed_at=confirmed_at,
            )
            await connection.execute(
                "UPDATE payment_intents SET status = 'CONFIRMED', transaction_id = $2, "
                "confirmed_at = $3, receipt_payload = $4::jsonb WHERE id = $1",
                intent_id,
                transaction_id,
                confirmed_at,
                json.dumps(_receipt_payload(receipt), ensure_ascii=False, sort_keys=True),
            )
            return receipt

    async def cancel(self, *, account_id: UUID, intent_id: UUID) -> None:
        """Close a pending intent; replay an already completed cancellation as a no-op.

        A cancelled intent cannot come back: preparing again is cheap, and an intent that can be
        revived is an agreement with no expiry. Other terminal states remain refusals.
        """
        async with self._pool.acquire() as connection, connection.transaction():
            # The SELECT is protected by payment_intents_own_account RLS, just like confirm's
            # intent lookup. Declare the caller's account inside this transaction before reading.
            await declare(connection, str(account_id))
            status = await connection.fetchval(
                "SELECT status FROM payment_intents WHERE id = $1 AND account_id = $2 FOR UPDATE",
                intent_id,
                account_id,
            )
            if status is None:
                raise IntentNotFoundError(intent_id)
            # A graph turn can cancel the bank intent and crash before committing its new
            # canonical dialogue state. Replaying that same cancellation must be harmless so the
            # turn can continue with its idempotent replacement prepare. Confirmed and expired
            # intents remain refusals; only the already-cancelled terminal state is replay-safe.
            if status == _CANCELLED:
                return
            if status != _AWAITING:
                raise IntentNotConfirmableError(intent_id, status)
            await connection.execute(
                "UPDATE payment_intents SET status = $2 WHERE id = $1",
                intent_id,
                _CANCELLED,
            )

    async def expire_stale(self) -> int:
        """Closes the intents nobody answered, and returns how many.

        Not what makes expiry safe — `confirm` already refuses an intent past its expiry, and it
        does so without trusting this to have run. What this makes true is the *data*: `EXPIRED`
        exists in the state machine and until this nothing ever wrote it, so an intent nobody
        confirmed sat AWAITING_CONFIRMATION for ever with a timestamp beside it saying otherwise.

        `status = AWAITING_CONFIRMATION` in the WHERE, not just the expiry: a confirmed intent is
        past its expiry too, and expiring it would rewrite a payment that happened into one that
        never did.

        ponytail: nothing schedules this yet. Run it by hand or from cron —
        `python -m finguard_banking.interface.expire_intents`. A sweep every minute buys nothing
        while the confirm path refuses expired intents on its own.
        """
        async with self._pool.acquire() as connection, connection.transaction():
            swept = await connection.fetch(
                f"UPDATE payment_intents SET status = 'EXPIRED' "
                f"WHERE status = '{_AWAITING}' AND expires_at <= NOW() "
                f"RETURNING id"
            )
        return len(swept)

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
        phone: str | None,
        internal: bool = False,
    ) -> UUID:
        """One transaction through the lifecycle: PENDING, both entries, BOOKED.

        In that order because migration 0004 accepts entries only while pending, and booking
        re-asserts the balance. A payment therefore cannot be recorded half-written.
        """
        transaction_id = uuid.uuid4()
        # An internal transfer gets no category, and that is deliberate rather than an omission.
        # A category partitions spending, and this is not spending — filing it under one would put
        # it in a breakdown that is supposed to add up to what left the household.
        metadata = json.dumps(
            {
                "merchant_name": recipient,
                "category": None if internal else "TRANSFER_BIZUM",
                "phone": phone,
            },
            ensure_ascii=False,
        )
        kind = TransactionType.INTERNAL_TRANSFER if internal else TransactionType.BIZUM
        label = f"Traspaso a {recipient}" if internal else f"Bizum a {recipient}"
        prefix = "traspaso" if internal else "bizum"
        await connection.execute(
            "INSERT INTO ledger_transactions "
            "(id, transaction_type, status, reference, description, metadata, occurred_at) "
            "VALUES ($1, $2, $3, $4, $5, $6::jsonb, NOW())",
            transaction_id,
            kind.value,
            TransactionStatus.PENDING.value,
            f"{prefix}-{transaction_id.hex[:16]}",
            label,
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


def _destination_of(intent: Any) -> Destination:
    """The destination an intent was approved against, rebuilt from its own row.

    The schema guarantees the pairing — a BIZUM has a contact and no account, a TRANSFER has an
    account and a name — so the branches here restate an invariant rather than guessing at one, and
    a row that satisfied neither could not have been written.
    """
    if intent["rail"] == Rail.INTERNAL.value:
        return ToOwnAccount(account_id=intent["to_account_id"], name=intent["recipient_name"])
    if intent["rail"] == Rail.BIZUM.value:
        return ToPhone(
            contact_id=intent["contact_id"],
            name=intent["recipient_name"],
            phone=intent["recipient_phone"],
        )
    return ToAccount(
        name=intent["recipient_name"],
        iban=Iban(intent["recipient_iban"]),
        contact_id=intent["contact_id"],
    )


def _receipt_payload(receipt: PaymentReceipt) -> dict[str, Any]:
    """JSON snapshot written atomically with the ledger booking and intent transition."""
    match receipt.to:
        case ToPhone(contact_id=contact_id, name=name, phone=phone):
            destination = {
                "kind": "phone",
                "contact_id": str(contact_id),
                "name": name,
                "phone": phone,
            }
        case ToAccount(name=name, iban=iban, contact_id=contact_id):
            destination = {
                "kind": "account",
                "name": name,
                "iban": str(iban),
                "contact_id": str(contact_id) if contact_id is not None else None,
            }
        case ToOwnAccount(account_id=account_id, name=name):
            destination = {
                "kind": "own_account",
                "account_id": str(account_id),
                "name": name,
            }
    return {
        "intent_id": str(receipt.intent_id),
        "transaction_id": str(receipt.transaction_id),
        "destination": destination,
        "amount_minor": receipt.amount.amount_minor,
        "currency": receipt.amount.currency,
        "balance_after_minor": receipt.balance_after.amount_minor,
        "confirmed_at": receipt.confirmed_at.isoformat(),
    }


def _receipt_from_payload(payload: Any) -> PaymentReceipt:
    """Decode a stored receipt without consulting mutable contacts or current balances."""
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, dict):
        raise ValueError("stored payment receipt is not an object")
    destination = payload["destination"]
    match destination.get("kind"):
        case "phone":
            to: Destination = ToPhone(
                contact_id=UUID(destination["contact_id"]),
                name=destination["name"],
                phone=destination["phone"],
            )
        case "account":
            to = ToAccount(
                name=destination["name"],
                iban=Iban(destination["iban"]),
                contact_id=(
                    UUID(destination["contact_id"]) if destination.get("contact_id") else None
                ),
            )
        case "own_account":
            to = ToOwnAccount(
                account_id=UUID(destination["account_id"]),
                name=destination["name"],
            )
        case kind:
            raise ValueError(f"unknown stored payment destination: {kind}")
    return PaymentReceipt(
        intent_id=UUID(payload["intent_id"]),
        transaction_id=UUID(payload["transaction_id"]),
        to=to,
        amount=Money(int(payload["amount_minor"]), payload["currency"]),
        balance_after=Money(int(payload["balance_after_minor"]), payload["currency"]),
        confirmed_at=datetime.fromisoformat(payload["confirmed_at"]),
    )


def _receipt_matches_intent(receipt: PaymentReceipt, intent: Any) -> bool:
    """Check the replayed receipt's destination against the immutable approved intent fields."""
    match intent["rail"]:
        case "BIZUM":
            return (
                isinstance(receipt.to, ToPhone)
                and receipt.to.contact_id == intent["contact_id"]
                and (
                    intent["recipient_name"] is None or receipt.to.name == intent["recipient_name"]
                )
                and (
                    intent["recipient_phone"] is None
                    or receipt.to.phone == intent["recipient_phone"]
                )
            )
        case "TRANSFER":
            return (
                isinstance(receipt.to, ToAccount)
                and str(receipt.to.iban) == intent["recipient_iban"]
                and receipt.to.name == intent["recipient_name"]
                and receipt.to.contact_id == intent["contact_id"]
            )
        case "INTERNAL":
            return (
                isinstance(receipt.to, ToOwnAccount)
                and receipt.to.account_id == intent["to_account_id"]
                and receipt.to.name == intent["recipient_name"]
            )
        case _:
            return False
