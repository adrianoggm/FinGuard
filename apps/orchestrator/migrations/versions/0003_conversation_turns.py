"""durable idempotency and turn ownership for LangGraph conversations"""

from alembic import op

revision = "0003_conversation_turns"
down_revision = "0002_conversation_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
      CREATE TABLE assistant.conversation_turn (
        account_id uuid NOT NULL,
        conversation_id uuid NOT NULL,
        expected_version integer NOT NULL CHECK (expected_version >= 0),
        request_id varchar(200) NOT NULL,
        request_fingerprint char(64) NOT NULL CHECK (request_fingerprint ~ '^[0-9a-f]{64}$'),
        status text NOT NULL CHECK (status IN ('NEW', 'RESUME', 'DONE')),
        answer_schema_version smallint,
        answer_payload jsonb CHECK (octet_length(answer_payload::text) <= 524288),
        updated_at timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (account_id, conversation_id, expected_version),
        CHECK ((status = 'DONE') = (answer_payload IS NOT NULL)),
        CHECK ((answer_payload IS NULL) = (answer_schema_version IS NULL))
      )
    """)
    op.execute("ALTER TABLE assistant.conversation_turn ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE assistant.conversation_turn FORCE ROW LEVEL SECURITY")
    op.execute("""
      CREATE POLICY conversation_turn_account_isolation
      ON assistant.conversation_turn
      USING (account_id = NULLIF(current_setting('app.account_id', true), '')::uuid)
      WITH CHECK (account_id = NULLIF(current_setting('app.account_id', true), '')::uuid)
    """)


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS conversation_turn_account_isolation ON assistant.conversation_turn"
    )
    op.execute("DROP TABLE IF EXISTS assistant.conversation_turn")
