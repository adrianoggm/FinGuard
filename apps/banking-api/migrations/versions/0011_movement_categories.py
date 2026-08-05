"""the holder's own category for a movement, beside the bank's

A transfer is a *mechanism*, not a category. Every bizum in this ledger is `TRANSFER_BIZUM`, which
says how the money moved and nothing about what it was for — and it was somebody's rent, a share
of a dinner, or a present. The category the bank assigned is a guess from a merchant name, and the
person who made the payment knows better.

**The ledger is not edited.** ADR-002 says a movement is immutable and this does not argue with
it: what the bank assigned stays in `metadata`, and the holder's opinion is a row in a new table
beside it. Overwriting would destroy the only record of what was originally inferred, and "the bank
said Ocio and I said Salud" is two facts rather than one corrected fact.

**Keyed on (account, transaction), not on the transaction.** A movement has entries for two
accounts — the holder and the clearing account — and an opinion belongs to whoever holds the money.
Keying it on the transaction alone would let one side of a double entry relabel the other's.

**The coalesce goes in `transactions_v1`, and that is the whole reason this is cheap.** The query
compiler filters, groups and lists from `t.category` in that view, so every path picks the override
up at once: the movement list, the category chips, the finance breakdown, and any question the
assistant asks about a category. Putting it anywhere else would mean a screen and a sentence
disagreeing about the same movement, which is the failure this project keeps paying to avoid.

`category_is_custom` comes with it so a screen can say whose opinion it is showing. A label that
silently changed is a label nobody trusts twice.

Adding that column means rebuilding the view rather than replacing it, and `account_balances_v1`
is built on `transactions_v1` — so the balance view is dropped and restated here too. Explicitly,
never with CASCADE: a cascade drops whatever happens to depend on the view, which on a database
that has drifted is a list nobody enumerated.

Revision ID: 0011_movement_categories
Revises: 0010_card_charges_detail
Create Date: 2026-08-03
"""

from alembic import op

revision = "0011_movement_categories"
down_revision = "0010_card_charges_detail"
branch_labels = None
depends_on = None

_ACCOUNT = "current_setting('app.account_id', true)::uuid"
ROLE = "finguard_query_reader"

_TRANSACTIONS = """
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
        mc.category IS NOT NULL AS category_is_custom
    FROM ledger_transactions lt
    JOIN ledger_entries le ON le.transaction_id = lt.id
    LEFT JOIN movement_categories mc
           ON mc.transaction_id = lt.id
          AND mc.account_id = le.account_id
    WHERE lt.status = 'BOOKED'
      AND le.account_id = {account}
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
        lt.metadata ->> 'category' AS category
    FROM ledger_transactions lt
    JOIN ledger_entries le ON le.transaction_id = lt.id
    WHERE lt.status = 'BOOKED'
      AND le.account_id = {account}
"""


def _charges(assigned: str, joined: str) -> str:
    return f"""
    SELECT
        c.id AS card_id,
        c.account_id,
        t.id AS transaction_id,
        t.occurred_at,
        t.description,
        t.metadata ->> 'merchant_name' AS merchant_name,
        {assigned} AS category,
        e.amount_minor
    FROM cards c
    JOIN ledger_transactions t ON t.card_id = c.id
    JOIN ledger_entries e ON e.transaction_id = t.id AND e.account_id = c.account_id
    {joined}
    WHERE c.account_id = {_ACCOUNT}
      AND t.status = 'BOOKED'
      AND e.entry_type = 'DEBIT'
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
    """Swaps `transactions_v1` and puts the balance view back on top of it.

    `account_balances_v1` reads `signed_amount_minor` from this view, so it depends on it and has
    to come down first. Restating it here is what a migration is: a record of the shape at this
    point in history, not a reference to the shape somewhere else.

    **And the shape to restate is the current one, not the one in the migration that created it.**
    The first version of this restated the bodies from 0002 — and 0003 is what added
    `AND le.account_id = current_setting('app.account_id')`, so copying 0002 silently removed the
    account filter from both views. Three isolation tests failed with 3254 rows where they expect 1,
    which is the entire reason those tests exist: a containment regression that reads as a working
    query.
    """
    op.execute("DROP VIEW IF EXISTS assistant_read.account_balances_v1")
    op.execute("DROP VIEW IF EXISTS assistant_read.transactions_v1")
    op.execute(f"CREATE VIEW assistant_read.transactions_v1 AS {body.format(account=_ACCOUNT)}")
    op.execute(
        "CREATE VIEW assistant_read.account_balances_v1 AS " + _BALANCES.format(account=_ACCOUNT)
    )
    op.execute(f"GRANT SELECT ON assistant_read.transactions_v1 TO {ROLE}")
    op.execute(f"GRANT SELECT ON assistant_read.account_balances_v1 TO {ROLE}")


def upgrade() -> None:
    op.execute("""
        CREATE TABLE movement_categories (
            account_id UUID NOT NULL REFERENCES accounts(id),
            transaction_id UUID NOT NULL REFERENCES ledger_transactions(id),
            category VARCHAR(40) NOT NULL CHECK (category ~ '^[A-Z][A-Z0-9_]*$'),
            assigned_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            -- One opinion per movement per holder. Changing it is an UPDATE, and undoing it is a
            -- DELETE that falls back to what the bank assigned.
            PRIMARY KEY (account_id, transaction_id)
        )
    """)

    op.execute("ALTER TABLE movement_categories ENABLE ROW LEVEL SECURITY")
    op.execute(f"""
        CREATE POLICY movement_categories_own_account ON movement_categories
            FOR SELECT USING (account_id = {_ACCOUNT})
    """)
    op.execute(
        "CREATE POLICY movement_categories_inserts ON movement_categories "
        "FOR INSERT WITH CHECK (true)"
    )
    op.execute(
        "CREATE POLICY movement_categories_updates ON movement_categories "
        "FOR UPDATE USING (true) WITH CHECK (true)"
    )
    op.execute(
        "CREATE POLICY movement_categories_deletes ON movement_categories FOR DELETE USING (true)"
    )

    # Rebuilt rather than replaced: CREATE OR REPLACE cannot add a column to a view.
    _replace_transactions(_TRANSACTIONS)

    op.execute("DROP VIEW IF EXISTS assistant_read.card_charges_v1")
    op.execute(
        "CREATE VIEW assistant_read.card_charges_v1 AS "
        + _charges(
            "COALESCE(mc.category, t.metadata ->> 'category')",
            "LEFT JOIN movement_categories mc "
            "ON mc.transaction_id = t.id AND mc.account_id = c.account_id",
        )
    )
    op.execute(f"GRANT SELECT ON assistant_read.card_charges_v1 TO {ROLE}")


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS assistant_read.card_charges_v1")
    op.execute(
        "CREATE VIEW assistant_read.card_charges_v1 AS " + _charges("t.metadata ->> 'category'", "")
    )
    op.execute(f"GRANT SELECT ON assistant_read.card_charges_v1 TO {ROLE}")

    _replace_transactions(_TRANSACTIONS_BEFORE)

    for policy in (
        "movement_categories_deletes",
        "movement_categories_updates",
        "movement_categories_inserts",
        "movement_categories_own_account",
    ):
        op.execute(f"DROP POLICY IF EXISTS {policy} ON movement_categories")
    op.execute("DROP TABLE IF EXISTS movement_categories")
