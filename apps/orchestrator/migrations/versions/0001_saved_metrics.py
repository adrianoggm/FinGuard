"""assistant saved metrics with holder isolation"""

from alembic import op

revision = "0001_saved_metrics"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS assistant AUTHORIZATION CURRENT_USER")
    op.execute("""
      CREATE TABLE assistant.saved_metric (
        id uuid PRIMARY KEY,
        holder_id text NOT NULL,
        title varchar(80) NOT NULL CHECK (length(trim(title)) BETWEEN 1 AND 80),
        plan_json jsonb NOT NULL CHECK (octet_length(plan_json::text) <= 32768),
        encoding_json jsonb NOT NULL CHECK (octet_length(encoding_json::text) <= 8192),
        position integer NOT NULL DEFAULT 0 CHECK (position >= 0),
        version integer NOT NULL DEFAULT 1 CHECK (version > 0),
        idempotency_key varchar(200) NOT NULL,
        request_hash char(64) NOT NULL CHECK (request_hash ~ '^[0-9a-f]{64}$'),
        created_at timestamptz NOT NULL DEFAULT now(),
        updated_at timestamptz NOT NULL DEFAULT now(),
        UNIQUE (holder_id, idempotency_key)
      )
    """)
    op.execute(
        "CREATE INDEX saved_metric_holder_position_idx "
        "ON assistant.saved_metric(holder_id, position, created_at, id)"
    )
    op.execute("ALTER TABLE assistant.saved_metric ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE assistant.saved_metric FORCE ROW LEVEL SECURITY")
    op.execute("""
      CREATE POLICY saved_metric_holder_isolation ON assistant.saved_metric
      USING (holder_id = NULLIF(current_setting('app.holder_id', true), ''))
      WITH CHECK (holder_id = NULLIF(current_setting('app.holder_id', true), ''))
    """)


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS saved_metric_holder_isolation ON assistant.saved_metric")
    op.execute("DROP TABLE IF EXISTS assistant.saved_metric")
    op.execute("DROP SCHEMA IF EXISTS assistant")
