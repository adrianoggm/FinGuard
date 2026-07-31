"""read views and permissions for the query role

Implements vision.md §8.6: the query engine does not read the tables, it reads two
views, and it does so with a role that cannot do anything else.

The views are also the only place where two rules live that used to be repeated in
the balance SQL, the aggregate SQL and the supporting-movements SQL: what counts as
booked, and which sign each entry carries.

Revision ID: 0002_read_views
Revises: 0001_ledger
Create Date: 2026-07-30
"""

from alembic import op

revision = "0002_read_views"
down_revision = "0001_ledger"
branch_labels = None
depends_on = None

ROLE = "finguard_query_reader"


def upgrade() -> None:
    op.execute("CREATE SCHEMA assistant_read")

    # One entry per row, already interpreted: no entry_type or status the consumer
    # has to remember to filter on.
    op.execute("""
        CREATE VIEW assistant_read.transactions_v1 AS
        SELECT
            lt.id AS transaction_id,
            le.id AS entry_id,
            le.account_id,
            lt.occurred_at,
            lt.description,
            lt.transaction_type,
            CASE WHEN le.entry_type = 'CREDIT' THEN 'INCOME' ELSE 'EXPENSE' END AS direction,
            le.amount_minor,
            CASE WHEN le.entry_type = 'CREDIT' THEN le.amount_minor
                 ELSE -le.amount_minor END AS signed_amount_minor,
            le.currency,
            lt.metadata ->> 'merchant_name' AS merchant_name,
            lt.metadata ->> 'category' AS category
        FROM ledger_transactions lt
        JOIN ledger_entries le ON le.transaction_id = lt.id
        WHERE lt.status = 'BOOKED'
    """)

    # LEFT JOIN on purpose: an account with no movements shows up at zero, and an
    # account that does not exist does not show up at all. Two different answers.
    op.execute("""
        CREATE VIEW assistant_read.account_balances_v1 AS
        SELECT
            a.id AS account_id,
            a.currency,
            COALESCE(SUM(t.signed_amount_minor), 0) AS booked_minor
        FROM accounts a
        LEFT JOIN assistant_read.transactions_v1 t ON t.account_id = a.id
        GROUP BY a.id, a.currency
    """)

    # The role is created in infrastructure/postgres/init/02-query-reader.sql. If it
    # is missing this fails on purpose: without the role, the views protect nothing.
    op.execute(f"GRANT USAGE ON SCHEMA assistant_read TO {ROLE}")
    op.execute(f"GRANT SELECT ON assistant_read.transactions_v1 TO {ROLE}")
    op.execute(f"GRANT SELECT ON assistant_read.account_balances_v1 TO {ROLE}")

    # Without USAGE on public it cannot even list the table names. The views still
    # work because they run with their owner's privileges.
    op.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")


def downgrade() -> None:
    op.execute("GRANT USAGE ON SCHEMA public TO PUBLIC")
    op.execute("DROP VIEW IF EXISTS assistant_read.account_balances_v1")
    op.execute("DROP VIEW IF EXISTS assistant_read.transactions_v1")
    op.execute("DROP SCHEMA IF EXISTS assistant_read")
