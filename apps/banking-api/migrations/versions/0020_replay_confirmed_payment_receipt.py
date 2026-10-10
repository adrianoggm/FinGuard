"""persist exact receipts so confirmed intents can be safely replayed

Revision ID: 0020_replay_confirmed_payment_receipt
Revises: 0019_intent_immutability
Create Date: 2026-10-10
"""

from pathlib import Path

from alembic import op

revision = "0020_replay_confirmed_payment_receipt"
down_revision = "0019_intent_immutability"
branch_labels = None
depends_on = None


def _sql(name: str) -> str:
    return (Path(__file__).resolve().parents[1] / "sql" / name).read_text(encoding="utf-8")


def upgrade() -> None:
    op.execute("ALTER TABLE payment_intents ADD COLUMN receipt_payload jsonb")
    # Existing confirmed intents predate receipt snapshots. Keep them valid, but require every
    # newly written snapshot to belong to a confirmed intent and stay bounded. The replacement
    # transition trigger also requires every new AWAITING_CONFIRMATION -> CONFIRMED transition to
    # write its receipt in that same transaction; historical confirmed rows remain untouched.
    op.execute("""
      ALTER TABLE payment_intents
      ADD CONSTRAINT payment_intents_receipt_size
        CHECK (receipt_payload IS NULL OR octet_length(receipt_payload::text) <= 8192),
      ADD CONSTRAINT payment_intents_receipt_confirmed
        CHECK (receipt_payload IS NULL OR status = 'CONFIRMED')
    """)
    op.execute(_sql("0020_assert_intent_transition.sql"))


def downgrade() -> None:
    op.execute(_sql("0019_assert_intent_transition.sql"))
    op.execute("""
      ALTER TABLE payment_intents
      DROP CONSTRAINT IF EXISTS payment_intents_receipt_confirmed,
      DROP CONSTRAINT IF EXISTS payment_intents_receipt_size,
      DROP COLUMN IF EXISTS receipt_payload
    """)
