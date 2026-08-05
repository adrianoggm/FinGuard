"""two-step payments: an intention, then a confirmation

§4.1 of the vision puts human confirmation between an intent and its execution, and this is the
row that makes the gap real. Preparing writes an intent and moves nothing; confirming turns it
into a booked ledger transaction. Nothing between the two touches a balance.

The constraints here are the ones that make "confirm" safe to retry and safe to race, and each
is a specific way of paying twice:

`UNIQUE (account_id, idempotency_key)` — a retried *prepare* finds the intent it already made
instead of making a second one. A client whose request timed out and resends is the normal case,
not the exceptional one.

`transaction_id` set if and only if CONFIRMED — the link to the ledger and the status cannot
disagree. Without it, a crash between booking the transaction and updating the status leaves an
intent that looks unconfirmed and a payment that happened, and the retry pays again.

A state machine, like the ledger's: AWAITING_CONFIRMATION may become CONFIRMED, CANCELLED or
EXPIRED, and all three are terminal. Confirming twice is therefore not a race that needs winning
— the second attempt finds a status that cannot transition and stops.

`expires_at` because the preview showed a balance. An intent confirmed a day later was agreed
against figures that no longer hold, and the honest answer is to prepare it again.

Revision ID: 0006_payment_intents
Revises: 0005_contacts
Create Date: 2026-08-03
"""

from pathlib import Path

from alembic import op

revision = "0006_payment_intents"
down_revision = "0005_contacts"
branch_labels = None
depends_on = None

_ACCOUNT = "current_setting('app.account_id', true)::uuid"
STATUSES = ("AWAITING_CONFIRMATION", "CONFIRMED", "CANCELLED", "EXPIRED")


def _sql(name: str) -> str:
    return (Path(__file__).resolve().parents[1] / "sql" / name).read_text(encoding="utf-8")


def upgrade() -> None:
    allowed = ", ".join(f"'{status}'" for status in STATUSES)
    op.execute(f"""
        CREATE TABLE payment_intents (
            id UUID PRIMARY KEY,
            account_id UUID NOT NULL REFERENCES accounts(id),
            contact_id UUID NOT NULL REFERENCES contacts(id),
            amount_minor BIGINT NOT NULL CHECK (amount_minor > 0),
            currency CHAR(3) NOT NULL,
            concept VARCHAR(140),
            status VARCHAR(30) NOT NULL CHECK (status IN ({allowed})),
            -- Supplied by the caller. Scoped to the account so two holders cannot collide,
            -- and unique so a resent prepare is the same intent rather than a second one.
            idempotency_key VARCHAR(120) NOT NULL,
            transaction_id UUID REFERENCES ledger_transactions(id),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            expires_at TIMESTAMPTZ NOT NULL,
            confirmed_at TIMESTAMPTZ,

            CONSTRAINT payment_intents_key_once UNIQUE (account_id, idempotency_key),
            -- The link to the ledger and the status cannot disagree. A crash between booking
            -- and updating would otherwise leave a payment that happened and an intent that
            -- says it did not, and the retry pays again.
            CONSTRAINT payment_intents_booked_iff_confirmed CHECK (
                (status = 'CONFIRMED') = (transaction_id IS NOT NULL)
            ),
            CONSTRAINT payment_intents_confirmed_at_iff_confirmed CHECK (
                (status = 'CONFIRMED') = (confirmed_at IS NOT NULL)
            ),
            CONSTRAINT payment_intents_expiry_after_creation CHECK (expires_at > created_at)
        )
    """)

    op.execute("""
        CREATE INDEX idx_payment_intents_account
            ON payment_intents (account_id, status, created_at DESC)
    """)

    op.execute(_sql("0006_assert_intent_transition.sql"))
    op.execute("""
        CREATE TRIGGER payment_intents_lifecycle
            BEFORE UPDATE ON payment_intents
            FOR EACH ROW EXECUTE FUNCTION assert_intent_transition()
    """)

    op.execute("ALTER TABLE payment_intents ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE payment_intents FORCE ROW LEVEL SECURITY")
    op.execute(f"""
        CREATE POLICY payment_intents_own_account ON payment_intents
            FOR SELECT USING (account_id = {_ACCOUNT})
    """)
    op.execute("""
        CREATE POLICY payment_intents_writes ON payment_intents
            FOR ALL USING (true) WITH CHECK (true)
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS payment_intents_writes ON payment_intents")
    op.execute("DROP POLICY IF EXISTS payment_intents_own_account ON payment_intents")
    op.execute("DROP TRIGGER IF EXISTS payment_intents_lifecycle ON payment_intents")
    op.execute("DROP FUNCTION IF EXISTS assert_intent_transition()")
    op.execute("DROP TABLE IF EXISTS payment_intents")
