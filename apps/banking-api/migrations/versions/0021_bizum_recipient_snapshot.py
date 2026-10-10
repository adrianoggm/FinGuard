"""snapshot the exact Bizum recipient approved in the preview

Revision ID: 0021_bizum_recipient_snapshot
Revises: 0020_replay_confirmed_payment_receipt
Create Date: 2026-10-11
"""

from alembic import op

revision = "0021_bizum_recipient_snapshot"
down_revision = "0020_replay_confirmed_payment_receipt"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE payment_intents ADD COLUMN recipient_phone VARCHAR(32)")
    op.execute("ALTER TABLE payment_intents DROP CONSTRAINT payment_intents_destination")
    # Old Bizum intents remain representable, but the application refuses to confirm one without
    # a snapshot: the current contact row cannot prove which phone number was actually previewed.
    # New Bizum intents store both values. The whole-row immutability trigger in 0019/0020
    # automatically protects the new column too.
    op.execute("""
        ALTER TABLE payment_intents ADD CONSTRAINT payment_intents_destination CHECK (
            (rail = 'BIZUM'
                AND contact_id IS NOT NULL
                AND recipient_iban IS NULL
                AND to_account_id IS NULL
                AND (
                    (recipient_name IS NULL AND recipient_phone IS NULL)
                    OR (recipient_name IS NOT NULL AND recipient_phone IS NOT NULL
                        AND btrim(recipient_name) <> '' AND btrim(recipient_phone) <> '')
                ))
            OR
            (rail = 'TRANSFER'
                AND recipient_iban IS NOT NULL
                AND recipient_name IS NOT NULL
                AND btrim(recipient_name) <> ''
                AND recipient_phone IS NULL
                AND to_account_id IS NULL)
            OR
            (rail = 'INTERNAL'
                AND to_account_id IS NOT NULL
                AND contact_id IS NULL
                AND recipient_iban IS NULL
                AND recipient_phone IS NULL)
        )
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE payment_intents DROP CONSTRAINT payment_intents_destination")
    # The prior constraint cannot represent the snapshot fields. Preserve payment receipts while
    # removing only the newly introduced redundant destination snapshot columns.
    # This deliberate schema rewrite is the one operation allowed to clear the immutable snapshot.
    op.execute("DROP TRIGGER payment_intents_lifecycle ON payment_intents")
    op.execute("""
        UPDATE payment_intents
        SET recipient_name = NULL, recipient_phone = NULL
        WHERE rail = 'BIZUM'
    """)
    op.execute("ALTER TABLE payment_intents DROP COLUMN recipient_phone")
    op.execute("""
        ALTER TABLE payment_intents ADD CONSTRAINT payment_intents_destination CHECK (
            (rail = 'BIZUM'
                AND contact_id IS NOT NULL
                AND recipient_iban IS NULL
                AND recipient_name IS NULL
                AND to_account_id IS NULL)
            OR
            (rail = 'TRANSFER'
                AND recipient_iban IS NOT NULL
                AND btrim(recipient_name) <> ''
                AND to_account_id IS NULL)
            OR
            (rail = 'INTERNAL'
                AND to_account_id IS NOT NULL
                AND contact_id IS NULL
                AND recipient_iban IS NULL)
        )
    """)
    op.execute("""
        CREATE TRIGGER payment_intents_lifecycle
            BEFORE UPDATE ON payment_intents
            FOR EACH ROW EXECUTE FUNCTION assert_intent_transition()
    """)
