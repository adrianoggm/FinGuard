"""an append-only record of what was attempted

Step 13 of the demo, and the half of the story the ledger does not tell. The ledger records money
that moved; this records what somebody tried to do. Those differ exactly where it matters: a payment
refused for insufficient funds leaves no ledger row at all, and "did somebody try to empty this
account" is the question an audit trail exists to answer.

So a refusal is recorded as carefully as a success, with the domain error's code as its outcome.
`outcome` is that code and not a boolean for the same reason: "it failed" and "it failed because the
balance did not cover it" are different facts, and only one of them is useful six months later.

Append-only, enforced by two triggers rather than by nobody writing the UPDATE — a per-row one for
UPDATE and DELETE, and a statement-level one for TRUNCATE, which the first never sees. The service
is the thing the trail is evidence about, so the service must not be able to edit it. Unlike the
ledger there is no compensating row to offer: a wrong audit row is history, and the correction is
another row.

`detail` is the command's own fields as JSON. It is deliberately generic — whatever a command
carries is what gets recorded, so a command added later is audited without anybody remembering to
extend this. **The other side of that: a command with a secret in a field would write the secret
here.** Nothing in this service has one today; the day one does, the field has to be excluded where
the payload is built, in `CommandBus`.

`account_id` has no foreign key, on purpose. A trail that can be broken by deleting the account it
concerns is not a trail, and this is one of the few places where pointing at a row that may no
longer exist is the correct shape.

Revision ID: 0008_audit_events
Revises: 0007_fix_rls_write_policies
Create Date: 2026-08-03
"""

from pathlib import Path

from alembic import op

revision = "0008_audit_events"
down_revision = "0007_fix_rls_write_policies"
branch_labels = None
depends_on = None

_ACCOUNT = "current_setting('app.account_id', true)::uuid"


def _sql(name: str) -> str:
    return (Path(__file__).resolve().parents[1] / "sql" / name).read_text(encoding="utf-8")


def upgrade() -> None:
    op.execute("""
        CREATE TABLE audit_events (
            -- Sequential, because an audit trail without an order is a bag of rows. Two events in
            -- the same millisecond still have a before and an after.
            id BIGSERIAL PRIMARY KEY,
            occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            -- No foreign key: see the module docstring. The trail outlives what it describes.
            account_id UUID,
            command VARCHAR(80) NOT NULL,
            -- 'OK', or the domain error's code. Never a boolean.
            outcome VARCHAR(60) NOT NULL,
            detail JSONB NOT NULL DEFAULT '{}'::jsonb
        )
    """)

    op.execute("""
        CREATE INDEX idx_audit_events_account ON audit_events (account_id, id DESC)
    """)
    # Reading the trail across accounts — "what was refused today" — is a supervision question,
    # not an account's question, and it should not have to scan.
    op.execute("""
        CREATE INDEX idx_audit_events_outcome ON audit_events (outcome, occurred_at DESC)
            WHERE outcome <> 'OK'
    """)

    op.execute(_sql("0008_reject_audit_mutation.sql"))
    op.execute("""
        CREATE TRIGGER audit_events_append_only
            BEFORE UPDATE OR DELETE ON audit_events
            FOR EACH ROW EXECUTE FUNCTION reject_audit_mutation()
    """)
    # TRUNCATE is not DELETE and a per-row trigger never sees it. Found by trying it: the trigger
    # above refused a DELETE and let one TRUNCATE empty the whole trail. A statement-level trigger
    # is the other half of the claim, because a log that can be emptied is not append-only.
    op.execute("""
        CREATE TRIGGER audit_events_untruncatable
            BEFORE TRUNCATE ON audit_events
            FOR EACH STATEMENT EXECUTE FUNCTION reject_audit_mutation()
    """)

    # Same shape as the other per-account tables (0007): reads restricted to the caller's account,
    # writes per command so a permissive FOR ALL cannot OR the restriction away. No FORCE, because
    # the owner writes the trail and must be able to write rows it will not be able to select.
    op.execute("ALTER TABLE audit_events ENABLE ROW LEVEL SECURITY")
    op.execute(f"""
        CREATE POLICY audit_events_own_account ON audit_events
            FOR SELECT USING (account_id = {_ACCOUNT})
    """)
    op.execute("CREATE POLICY audit_events_inserts ON audit_events FOR INSERT WITH CHECK (true)")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS audit_events_inserts ON audit_events")
    op.execute("DROP POLICY IF EXISTS audit_events_own_account ON audit_events")
    op.execute("DROP TRIGGER IF EXISTS audit_events_untruncatable ON audit_events")
    op.execute("DROP TRIGGER IF EXISTS audit_events_append_only ON audit_events")
    op.execute("DROP FUNCTION IF EXISTS reject_audit_mutation()")
    op.execute("DROP TABLE IF EXISTS audit_events")
