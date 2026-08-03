"""per-account isolation in the database, not only in the application

The orchestrator resolves an identity and only ever sends that caller's account. That is a
promise the application keeps and the database did not: anything holding a connection could
read any row, so a bug in the SQL compiler or in the HTTP adapter would have returned
another holder's movements and looked completely normal doing it.

Two mechanisms, because they defend different failures and neither covers the other.

**The views filter on a session variable.** The reader role's only door is the two views —
it has no privilege on the base tables — so a filter there is what constrains it today. It
is independent of the WHERE clause the compiler builds, which is the point: the account is
enforced twice, from two different places, and a compiler that forgot it still sees nothing.

**Row-level security on the base tables.** Dormant for the reader, which cannot reach them,
and that is exactly why it is worth having: the day a new view, a new role or a migration
grants table access, the guarantee is already underneath rather than waiting to be
remembered. A view is safe because somebody wrote a WHERE; a table is safe because the
database will not serve the rows.

**Corrected by 0007, twice.** The write policy here is `FOR ALL USING (true)`, and PostgreSQL
combines permissive policies with OR — so it cancelled the SELECT policy above it for every role
subject to them. And FORCE, added here so the owner would be subject too, made the ledger's own
integrity triggers read an empty table once the policies started restricting: one refused to book
anything, and the balance check summed zero and passed. Read 0007 before trusting this file.

`current_setting('app.account_id', true)` returns NULL when nothing set it, and NULL fails
every comparison, so a session that forgets to declare its account sees zero rows. The
fail-safe direction: forgetting to set it costs an empty answer, never somebody else's.

What this does **not** do, stated so nobody reads it as more than it is: banking still does
not authenticate its callers. `app.account_id` is set from what the request says, so this
stops a bug in our own code, not a hostile caller on banking's network. That needs identity
in this service, which is §12.2 and not this migration.

Revision ID: 0003_row_level_security
Revises: 0002_read_views
Create Date: 2026-08-03
"""

from alembic import op

revision = "0003_row_level_security"
down_revision = "0002_read_views"
branch_labels = None
depends_on = None

ROLE = "finguard_query_reader"

# The session variable the account travels in. `SET LOCAL` scopes it to a transaction, so
# it cannot leak to the next caller borrowing the same pooled connection.
SETTING = "app.account_id"

_ACCOUNT = f"current_setting('{SETTING}', true)::uuid"


def upgrade() -> None:
    # --- the reader's door -----------------------------------------------------------
    # Rebuilt rather than altered: a view's definition cannot gain a WHERE with CREATE OR
    # REPLACE if the column list is unchanged only by luck, and dropping states the intent.
    op.execute("DROP VIEW assistant_read.account_balances_v1")
    op.execute("DROP VIEW assistant_read.transactions_v1")

    op.execute(f"""
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
          AND le.account_id = {_ACCOUNT}
    """)

    op.execute(f"""
        CREATE VIEW assistant_read.account_balances_v1 AS
        SELECT
            a.id AS account_id,
            a.currency,
            COALESCE(SUM(t.signed_amount_minor), 0) AS booked_minor
        FROM accounts a
        LEFT JOIN assistant_read.transactions_v1 t ON t.account_id = a.id
        WHERE a.id = {_ACCOUNT}
        GROUP BY a.id, a.currency
    """)

    op.execute(f"GRANT SELECT ON assistant_read.transactions_v1 TO {ROLE}")
    op.execute(f"GRANT SELECT ON assistant_read.account_balances_v1 TO {ROLE}")

    # --- underneath, for whoever comes next ------------------------------------------
    for table, column in (("accounts", "id"), ("ledger_entries", "account_id")):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        # FORCE so the owner is subject to it too. Without this the owner bypasses every
        # policy, which makes the protection depend on which role happens to connect.
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"""
            CREATE POLICY {table}_own_account ON {table}
                FOR SELECT
                USING ({column} = {_ACCOUNT})
        """)
        # Writing is the ledger's own business and has nothing to do with a caller's
        # account, so the write paths get a policy that does not consult the setting.
        # Without it, FORCE would make every INSERT fail once RLS is on.
        op.execute(f"""
            CREATE POLICY {table}_writes ON {table}
                FOR ALL
                USING (true)
                WITH CHECK (true)
        """)


def downgrade() -> None:
    for table in ("accounts", "ledger_entries"):
        op.execute(f"DROP POLICY IF EXISTS {table}_writes ON {table}")
        op.execute(f"DROP POLICY IF EXISTS {table}_own_account ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.execute("DROP VIEW assistant_read.account_balances_v1")
    op.execute("DROP VIEW assistant_read.transactions_v1")
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
    op.execute(f"GRANT SELECT ON assistant_read.transactions_v1 TO {ROLE}")
    op.execute(f"GRANT SELECT ON assistant_read.account_balances_v1 TO {ROLE}")
