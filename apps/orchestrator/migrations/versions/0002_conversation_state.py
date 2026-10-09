"""versioned, account-scoped canonical conversation state"""

from alembic import op

revision = "0002_conversation_state"
down_revision = "0001_saved_metrics"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS assistant AUTHORIZATION CURRENT_USER")
    op.execute("""
      CREATE TABLE assistant.conversation_state (
        account_id uuid NOT NULL,
        conversation_id uuid NOT NULL,
        state_version integer NOT NULL CHECK (state_version > 0),
        schema_version smallint NOT NULL CHECK (schema_version > 0),
        payload jsonb NOT NULL CHECK (octet_length(payload::text) <= 131072),
        updated_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (account_id, conversation_id)
      )
    """)
    op.execute("ALTER TABLE assistant.conversation_state ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE assistant.conversation_state FORCE ROW LEVEL SECURITY")
    op.execute("""
      CREATE POLICY conversation_state_account_isolation
      ON assistant.conversation_state
      USING (account_id = NULLIF(current_setting('app.account_id', true), '')::uuid)
      WITH CHECK (account_id = NULLIF(current_setting('app.account_id', true), '')::uuid)
    """)


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS conversation_state_account_isolation ON assistant.conversation_state"
    )
    op.execute("DROP TABLE IF EXISTS assistant.conversation_state")
