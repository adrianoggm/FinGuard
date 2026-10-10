"""fence and expire abandoned turn-local checkpoints

Revision ID: 0008_abandoned_checkpoint_retention
Revises: 0007_replay_financial_read_count
Create Date: 2026-10-11
"""

from alembic import op

revision = "0008_abandoned_checkpoint_retention"
down_revision = "0007_replay_financial_read_count"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ABANDONED means the same idempotency key may no longer resume from a checkpoint that is
    # eligible for deletion. A new request at the unchanged canonical version may take over.
    op.execute(
        "ALTER TABLE assistant.conversation_turn DROP CONSTRAINT conversation_turn_status_check"
    )
    op.execute("""
        ALTER TABLE assistant.conversation_turn
        ADD CONSTRAINT conversation_turn_status_check
        CHECK (status IN ('NEW', 'RESUME', 'DONE', 'ABANDONED'))
    """)
    op.execute("""
        ALTER TABLE assistant.conversation_turn
        ADD COLUMN checkpoint_deleted_at timestamptz
    """)
    op.execute("""
        CREATE INDEX conversation_turn_abandoned_checkpoint
        ON assistant.conversation_turn (account_id, conversation_id, updated_at)
        WHERE status = 'ABANDONED' AND checkpoint_deleted_at IS NULL
    """)


def downgrade() -> None:
    # A downgraded runtime cannot understand ABANDONED rows. Refuse to downgrade while one remains
    # instead of making an expired checkpoint resumable after its data has been deleted.
    op.execute("""
        DO $$ BEGIN
            IF EXISTS (
                SELECT 1 FROM assistant.conversation_turn WHERE status = 'ABANDONED'
            ) THEN
                RAISE EXCEPTION 'cannot downgrade while abandoned conversation checkpoints exist';
            END IF;
        END $$
    """)
    op.execute("DROP INDEX IF EXISTS assistant.conversation_turn_abandoned_checkpoint")
    op.execute("ALTER TABLE assistant.conversation_turn DROP COLUMN checkpoint_deleted_at")
    op.execute(
        "ALTER TABLE assistant.conversation_turn DROP CONSTRAINT conversation_turn_status_check"
    )
    op.execute("""
        ALTER TABLE assistant.conversation_turn
        ADD CONSTRAINT conversation_turn_status_check
        CHECK (status IN ('NEW', 'RESUME', 'DONE'))
    """)
