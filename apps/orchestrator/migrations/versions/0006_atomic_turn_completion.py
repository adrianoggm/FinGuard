"""persist a replayable result in the turn reservation alongside canonical state"""

from alembic import op

revision = "0006_atomic_turn_completion"
down_revision = "0005_turn_reservation_leases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
      ALTER TABLE assistant.conversation_turn
      ADD COLUMN committed_result_payload text,
      ADD COLUMN committed_state_payload text,
      ADD COLUMN committed_model_calls integer,
      ADD CONSTRAINT conversation_turn_committed_result_size
        CHECK (committed_result_payload IS NULL OR
               octet_length(committed_result_payload) <= 524288),
      ADD CONSTRAINT conversation_turn_committed_state_size
        CHECK (committed_state_payload IS NULL OR
               octet_length(committed_state_payload) <= 131072),
      ADD CONSTRAINT conversation_turn_committed_state_present
        CHECK ((committed_result_payload IS NULL) = (committed_state_payload IS NULL)),
      ADD CONSTRAINT conversation_turn_committed_result_fields
        CHECK ((committed_result_payload IS NULL) = (committed_model_calls IS NULL)),
      ADD CONSTRAINT conversation_turn_committed_model_calls_nonnegative
        CHECK (committed_model_calls IS NULL OR committed_model_calls >= 0)
    """)


def downgrade() -> None:
    op.execute("""
      ALTER TABLE assistant.conversation_turn
      DROP CONSTRAINT IF EXISTS conversation_turn_committed_model_calls_nonnegative,
      DROP CONSTRAINT IF EXISTS conversation_turn_committed_result_fields,
      DROP CONSTRAINT IF EXISTS conversation_turn_committed_state_present,
      DROP CONSTRAINT IF EXISTS conversation_turn_committed_state_size,
      DROP CONSTRAINT IF EXISTS conversation_turn_committed_result_size,
      DROP COLUMN IF EXISTS committed_model_calls,
      DROP COLUMN IF EXISTS committed_state_payload,
      DROP COLUMN IF EXISTS committed_result_payload
    """)
