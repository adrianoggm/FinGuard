"""seal a booked transaction, and give status a state machine

ADR-002 says the ledger is immutable, and 0001 implemented that as "no UPDATE, no DELETE".
The hole this closes is the one the review found: **nothing stopped entries being appended to
a transaction that was already BOOKED.** A new debit and a new credit keep it balanced, so the
balance trigger passes, and the account's balance changes after the movement was recorded. A
booked transaction that can still grow is not a record of what happened.

Three rules, and each one is a thing that was possible before:

- `status` had no CHECK at all, so the column accepted any 30-character string. A typo was a
  new state, and the read views — which filter `status = 'BOOKED'` — would simply not see it.
- Entries could be inserted whenever. Now only while PENDING.
- Status could go anywhere, including backwards. Now PENDING may become BOOKED or CANCELLED,
  and both are terminal. A correction is a compensating transaction, which leaves the mistake
  and the fix both in the record — the reason a ledger is append-only in the first place.

This is a precondition for phase 5 rather than part of it. A payment writes a transaction, and
a payment whose record can be edited afterwards is not a payment, it is a suggestion.

**The seed had to change with it, and that is a finding.** It inserted every transaction as
BOOKED and then inserted the entries, so it could not have run under these rules — which means
the dataset never exercised the lifecycle it was supposed to represent. It now creates them
PENDING, adds the entries, and books them, so 1,621 transactions pass through the state machine
every time the database is built.

Revision ID: 0004_transaction_lifecycle
Revises: 0003_row_level_security
Create Date: 2026-08-03
"""

from pathlib import Path

from alembic import op

revision = "0004_transaction_lifecycle"
down_revision = "0003_row_level_security"
branch_labels = None
depends_on = None

STATUSES = ("PENDING", "BOOKED", "CANCELLED")


def _sql(name: str) -> str:
    return (Path(__file__).resolve().parents[1] / "sql" / name).read_text(encoding="utf-8")


def upgrade() -> None:
    # The column accepted any string. Adding the constraint after the fact needs the existing
    # rows to satisfy it, which they do — BOOKED and PENDING are both in the set.
    allowed = ", ".join(f"'{status}'" for status in STATUSES)
    op.execute(f"""
        ALTER TABLE ledger_transactions
            ADD CONSTRAINT ledger_transactions_status_known
            CHECK (status IN ({allowed}))
    """)

    # One function per file: asyncpg will not run two commands in one prepared statement,
    # which is why 0001 is split the same way.
    op.execute(_sql("0004_assert_transaction_accepts_entries.sql"))
    op.execute(_sql("0004_assert_lifecycle_transition.sql"))

    op.execute("""
        CREATE TRIGGER ledger_entries_only_while_pending
            BEFORE INSERT ON ledger_entries
            FOR EACH ROW EXECUTE FUNCTION assert_transaction_accepts_entries()
    """)

    # BEFORE UPDATE, and it coexists with 0001's reject_ledger_mutation: that one forbids
    # changing any column other than status, this one says which status changes are legal.
    op.execute("""
        CREATE TRIGGER ledger_transactions_lifecycle
            BEFORE UPDATE ON ledger_transactions
            FOR EACH ROW EXECUTE FUNCTION assert_lifecycle_transition()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS ledger_transactions_lifecycle ON ledger_transactions")
    op.execute("DROP TRIGGER IF EXISTS ledger_entries_only_while_pending ON ledger_entries")
    op.execute("DROP FUNCTION IF EXISTS assert_lifecycle_transition()")
    op.execute("DROP FUNCTION IF EXISTS assert_transaction_accepts_entries()")
    op.execute("""
        ALTER TABLE ledger_transactions
            DROP CONSTRAINT IF EXISTS ledger_transactions_status_known
    """)
