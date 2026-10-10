"""recover abandoned in-flight conversation turns after process crashes"""

from alembic import op

revision = "0005_turn_reservation_leases"
down_revision = "0004_provider_request_pacing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
      ALTER TABLE assistant.conversation_turn
      ADD COLUMN lease_until timestamptz
    """)
    op.execute("""
      ALTER TABLE assistant.conversation_turn
      ADD COLUMN lease_token uuid
    """)
    # The table is FORCE RLS. As the migration owner, temporarily restore the ordinary owner
    # bypass so existing NEW rows can be initialized before the lease consistency check is added.
    op.execute("ALTER TABLE assistant.conversation_turn NO FORCE ROW LEVEL SECURITY")
    # Give currently running turns the full lease on upgrade. A null lease is treated as expired
    # by the reservation query, so this avoids taking over an active request during deployment.
    op.execute("""
      UPDATE assistant.conversation_turn
      SET lease_until = now() + interval '5 minutes', lease_token = gen_random_uuid()
      WHERE status = 'NEW'
    """)
    op.execute("ALTER TABLE assistant.conversation_turn FORCE ROW LEVEL SECURITY")
    op.execute("""
      ALTER TABLE assistant.conversation_turn
      ADD CONSTRAINT conversation_turn_new_has_lease
      CHECK (
        (status = 'NEW' AND lease_until IS NOT NULL AND lease_token IS NOT NULL)
        OR (status <> 'NEW' AND lease_until IS NULL AND lease_token IS NULL)
      )
    """)
    op.execute("""
      CREATE INDEX conversation_turn_expired_lease
      ON assistant.conversation_turn (lease_until)
      WHERE status = 'NEW'
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS assistant.conversation_turn_expired_lease")
    op.execute(
        "ALTER TABLE assistant.conversation_turn "
        "DROP CONSTRAINT IF EXISTS conversation_turn_new_has_lease"
    )
    op.execute("ALTER TABLE assistant.conversation_turn DROP COLUMN IF EXISTS lease_token")
    op.execute("ALTER TABLE assistant.conversation_turn DROP COLUMN IF EXISTS lease_until")
