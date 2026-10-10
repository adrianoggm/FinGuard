"""retain financial-read accounting with the exact committed turn snapshot"""

from alembic import op

revision = "0007_replay_financial_read_count"
down_revision = "0006_atomic_turn_completion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE assistant.conversation_turn
        ADD COLUMN committed_financial_reads integer,
        ADD CONSTRAINT conversation_turn_committed_financial_reads_nonnegative
          CHECK (committed_financial_reads IS NULL OR committed_financial_reads >= 0),
        ADD CONSTRAINT conversation_turn_financial_reads_need_result
          CHECK (committed_result_payload IS NOT NULL OR committed_financial_reads IS NULL)
        """
    )


def downgrade() -> None:
    op.execute(
        """
        ALTER TABLE assistant.conversation_turn
        DROP CONSTRAINT IF EXISTS conversation_turn_financial_reads_need_result,
        DROP CONSTRAINT IF EXISTS conversation_turn_committed_financial_reads_nonnegative,
        DROP COLUMN IF EXISTS committed_financial_reads
        """
    )
