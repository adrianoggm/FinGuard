"""a movement between the holder's own accounts is not money leaving

Moving 200 € to the piggy bank is a debit on one account and a credit on another. Counted naively
it is an expense of 200 and an income of 200, and the monthly summary reports twice the movement
that happened. Today it cannot happen — one account per holder — which is exactly why it has to be
in place before the second account exists rather than after.

`is_internal` is read off the transaction type, set by the writer that created both legs. Deriving
it at read time from "is the counterparty another of this holder's accounts" would need the account
graph on every query, and the writer already knows.

**The balance still counts it, and that is not an inconsistency.** Money moved to the piggy bank
really did leave the current account, so `account_balances_v1` sums every entry as it always has.
What changes is the *question* "how much did I spend", which means money that left the household.
Two different questions over the same rows, which is what a view is for.

Adding a column means rebuilding the view, and `account_balances_v1` is built on it — so that one
is dropped and restated here too. Explicitly, never with CASCADE: a cascade drops whatever happens
to depend on the view, which on a database that has drifted is a list nobody enumerated. The body
restated below is 0011's, not 0002's: 0003 added the account filter, and copying the wrong version
is how a containment regression reads as a working query. 0011 has that warning in it because it
happened.

Revision ID: 0013_internal_movements
Revises: 0012_transfers
Create Date: 2026-08-04
"""

from alembic import op

revision = "0013_internal_movements"
down_revision = "0012_transfers"
branch_labels = None
depends_on = None

_ACCOUNT = "current_setting('app.account_id', true)::uuid"
ROLE = "finguard_query_reader"

# The transaction types that move money between accounts the same person owns. A list rather than a
# flag column: the type already exists on every transaction, and a second field saying the same
# thing is a second field to get wrong.
INTERNAL = ("INTERNAL_TRANSFER",)

_INTERNAL_SQL = ", ".join(f"'{name}'" for name in INTERNAL)

_TRANSACTIONS_AFTER = f"""
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
        -- The holder's category wins over the bank's. Everything downstream reads this column, so
        -- the override reaches the chat and the screens without either of them knowing it exists.
        COALESCE(mc.category, lt.metadata ->> 'category') AS category,
        mc.category IS NOT NULL AS category_is_custom,
        -- Money that stayed inside the household. Excluded from spending and income by default and
        -- never from the balance: it did leave this account, it just did not leave the person.
        lt.transaction_type IN ({_INTERNAL_SQL}) AS is_internal
    FROM ledger_transactions lt
    JOIN ledger_entries le ON le.transaction_id = lt.id
    LEFT JOIN movement_categories mc
           ON mc.transaction_id = lt.id
          AND mc.account_id = le.account_id
    WHERE lt.status = 'BOOKED'
      AND le.account_id = {{account}}
"""

_TRANSACTIONS_BEFORE = """
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
        COALESCE(mc.category, lt.metadata ->> 'category') AS category,
        mc.category IS NOT NULL AS category_is_custom
    FROM ledger_transactions lt
    JOIN ledger_entries le ON le.transaction_id = lt.id
    LEFT JOIN movement_categories mc
           ON mc.transaction_id = lt.id
          AND mc.account_id = le.account_id
    WHERE lt.status = 'BOOKED'
      AND le.account_id = {account}
"""

_BALANCES = """
    SELECT
        a.id AS account_id,
        a.currency,
        COALESCE(SUM(t.signed_amount_minor), 0) AS booked_minor
    FROM accounts a
    LEFT JOIN assistant_read.transactions_v1 t ON t.account_id = a.id
    WHERE a.id = {account}
    GROUP BY a.id, a.currency
"""


def _replace_transactions(body: str) -> None:
    """Swaps `transactions_v1` and puts the balance view back on top of it."""
    op.execute("DROP VIEW IF EXISTS assistant_read.account_balances_v1")
    op.execute("DROP VIEW IF EXISTS assistant_read.transactions_v1")
    op.execute(f"CREATE VIEW assistant_read.transactions_v1 AS {body.format(account=_ACCOUNT)}")
    op.execute(
        "CREATE VIEW assistant_read.account_balances_v1 AS " + _BALANCES.format(account=_ACCOUNT)
    )
    op.execute(f"GRANT SELECT ON assistant_read.transactions_v1 TO {ROLE}")
    op.execute(f"GRANT SELECT ON assistant_read.account_balances_v1 TO {ROLE}")


def upgrade() -> None:
    _replace_transactions(_TRANSACTIONS_AFTER)


def downgrade() -> None:
    _replace_transactions(_TRANSACTIONS_BEFORE)
