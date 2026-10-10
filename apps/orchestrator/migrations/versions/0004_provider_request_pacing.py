"""shared outbound request pacing slots for hosted model providers"""

from alembic import op

revision = "0004_provider_request_pacing"
down_revision = "0003_conversation_turns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS assistant AUTHORIZATION CURRENT_USER")
    op.execute("""
      CREATE TABLE assistant.provider_request_pacer (
        provider text PRIMARY KEY CHECK (length(provider) BETWEEN 1 AND 64),
        next_slot_at timestamptz NOT NULL
      )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS assistant.provider_request_pacer")
