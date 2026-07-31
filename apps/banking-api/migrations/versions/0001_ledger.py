"""double-entry ledger: customers, accounts, entries and balance projection

The DDL follows docs/vision.md §8.5. It adds the foreign keys the spec argues for in
§8.1 but does not write, and the two invariants that ADR-002 and ADR-003 take for
granted: that every transaction balances, and that entries are never touched.

The trigger bodies live in migrations/sql/ rather than inside Python strings: they are
the most delicate logic in the repository and deserve syntax highlighting and a linter.

Revision ID: 0001_ledger
Create Date: 2026-07-30
"""

from pathlib import Path

from alembic import op

SQL = Path(__file__).resolve().parent.parent / "sql"

revision = "0001_ledger"
down_revision = None
branch_labels = None
depends_on = None


def _sql(name: str) -> str:
    return (SQL / name).read_text(encoding="utf-8")


def upgrade() -> None:
    op.execute("""
        CREATE TABLE customers (
            id UUID PRIMARY KEY,
            full_name VARCHAR(140) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    op.execute("""
        CREATE TABLE accounts (
            id UUID PRIMARY KEY,
            customer_id UUID NOT NULL REFERENCES customers(id),
            account_reference VARCHAR(64) NOT NULL UNIQUE,
            currency CHAR(3) NOT NULL,
            status VARCHAR(30) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            version BIGINT NOT NULL DEFAULT 0
        )
    """)

    op.execute("""
        CREATE TABLE ledger_transactions (
            id UUID PRIMARY KEY,
            transaction_type VARCHAR(40) NOT NULL,
            status VARCHAR(30) NOT NULL,
            reference VARCHAR(120) NOT NULL UNIQUE,
            description TEXT,
            metadata JSONB NOT NULL DEFAULT '{}',
            occurred_at TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    op.execute("""
        CREATE TABLE ledger_entries (
            id UUID PRIMARY KEY,
            transaction_id UUID NOT NULL
                REFERENCES ledger_transactions(id),
            account_id UUID NOT NULL
                REFERENCES accounts(id),
            entry_type VARCHAR(10) NOT NULL
                CHECK (entry_type IN ('DEBIT', 'CREDIT')),
            amount_minor BIGINT NOT NULL
                CHECK (amount_minor > 0),
            currency CHAR(3) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    op.execute("""
        CREATE TABLE balance_projections (
            account_id UUID PRIMARY KEY
                REFERENCES accounts(id),
            available_minor BIGINT NOT NULL,
            booked_minor BIGINT NOT NULL,
            currency CHAR(3) NOT NULL,
            version BIGINT NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL
        )
    """)

    # Indexes from docs/vision.md §8.7 that apply to these tables.
    op.execute("CREATE INDEX idx_ledger_entries_account ON ledger_entries(account_id)")
    op.execute("CREATE INDEX idx_ledger_entries_transaction ON ledger_entries(transaction_id)")
    op.execute("CREATE INDEX idx_transactions_occurred_at ON ledger_transactions(occurred_at)")
    op.execute(
        "CREATE INDEX idx_transactions_type_date "
        "ON ledger_transactions(transaction_type, occurred_at)"
    )
    op.execute("CREATE INDEX idx_transactions_metadata ON ledger_transactions USING GIN(metadata)")

    # ADR-003, checked per currency. See migrations/sql/ for the body.
    op.execute(_sql("0001_assert_transaction_is_balanced.sql"))

    # INSERT only: UPDATE and DELETE are blocked by the immutability trigger.
    op.execute("""
        CREATE CONSTRAINT TRIGGER ledger_entries_balanced
            AFTER INSERT ON ledger_entries
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION assert_transaction_is_balanced()
    """)

    # ADR-002. The status of ledger_transactions does change (to BOOKED, for instance),
    # which is why only DELETE is blocked there.
    op.execute(_sql("0001_reject_ledger_mutation.sql"))

    op.execute("""
        CREATE TRIGGER ledger_entries_immutable
            BEFORE UPDATE OR DELETE ON ledger_entries
            FOR EACH ROW EXECUTE FUNCTION reject_ledger_mutation()
    """)

    op.execute("""
        CREATE TRIGGER ledger_transactions_undeletable
            BEFORE DELETE ON ledger_transactions
            FOR EACH ROW EXECUTE FUNCTION reject_ledger_mutation()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS ledger_transactions_undeletable ON ledger_transactions")
    op.execute("DROP TRIGGER IF EXISTS ledger_entries_immutable ON ledger_entries")
    op.execute("DROP TRIGGER IF EXISTS ledger_entries_balanced ON ledger_entries")
    op.execute("DROP FUNCTION IF EXISTS reject_ledger_mutation()")
    op.execute("DROP FUNCTION IF EXISTS assert_transaction_is_balanced()")
    op.execute("DROP TABLE IF EXISTS balance_projections")
    op.execute("DROP TABLE IF EXISTS ledger_entries")
    op.execute("DROP TABLE IF EXISTS ledger_transactions")
    op.execute("DROP TABLE IF EXISTS accounts")
    op.execute("DROP TABLE IF EXISTS customers")
