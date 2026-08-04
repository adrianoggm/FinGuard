"""four layers of classification, and only one of them immutable

A movement's category is currently two things pretending to be one: what the bank said, in
`ledger_transactions.metadata`, and what the holder overrode, in `movement_categories`. Two more
are needed and each answers a different question.

    bank_category     what the bank said.       Immutable. In the ledger's metadata, and that is
                                                where it stays — it is the only layer that can be
                                                audited against a statement, and the moment it is
                                                editable "cada cifra viene de tu banco" stops
                                                being true.
    system_category   what FinGuard infers.     Recomputable. Its own table with the classifier's
                                                version beside it, so a better classifier is a
                                                rewrite of this table and touches nothing else —
                                                rather than a migration over everybody's
                                                corrections.
    user_category     the holder's override.    Already here. Wins over both.
    tags              free labels.               Zero or many, and therefore **not** a category.

The effective category is `user ?? system ?? bank`, resolved in the view so nothing downstream has
to know the layers exist. `category_source` says which one won, because "¿en qué categoría has
clasificado esto?" has a better answer than "LEISURE" when it can say who decided.

**Tags are deliberately not part of the grouping mechanism.** A category partitions — exactly one
per movement — which is what makes a breakdown add up to the total, and `evaluate_e2e` asserts
that. A tag is zero or many, so totals over tags do not sum and cannot. Routing them through the
same `GROUP BY` would make a check that exists to catch wrong answers start failing on correct
ones, and a check that cries wolf is one somebody switches off. Grouping by tag needs its own
answer shape, which is why this migration gives tags a table and no view column.

Revision ID: 0015_classification_layers
Revises: 0014_account_kinds
Create Date: 2026-08-04
"""

from alembic import op

revision = "0015_classification_layers"
down_revision = "0014_account_kinds"
branch_labels = None
depends_on = None

_ACCOUNT = "current_setting('app.account_id', true)::uuid"
ROLE = "finguard_query_reader"

# Same shape as a category everywhere else. Checked here as well as in the domain: this column is
# written by a batch job rather than by a request, so the edge that validates everything else does
# not see it.
_CATEGORY_SHAPE = "^[A-Z][A-Z0-9_]*$"

_TRANSACTIONS_AFTER = """
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
        -- The holder's opinion, then this service's, then the bank's. Resolved here so nothing
        -- downstream has to know there are layers.
        COALESCE(mc.category, sc.category, lt.metadata ->> 'category') AS category,
        -- Which one won. `category_is_custom` stays because every reader already uses it and it
        -- means what it always meant; the source is what lets an answer say *who* decided.
        mc.category IS NOT NULL AS category_is_custom,
        CASE
            WHEN mc.category IS NOT NULL THEN 'USER'
            WHEN sc.category IS NOT NULL THEN 'SYSTEM'
            WHEN lt.metadata ->> 'category' IS NOT NULL THEN 'BANK'
            ELSE 'NONE'
        END AS category_source,
        lt.transaction_type IN ('INTERNAL_TRANSFER') AS is_internal
    FROM ledger_transactions lt
    JOIN ledger_entries le ON le.transaction_id = lt.id
    LEFT JOIN movement_categories mc
           ON mc.transaction_id = lt.id
          AND mc.account_id = le.account_id
    LEFT JOIN movement_classifications sc
           ON sc.transaction_id = lt.id
          AND sc.account_id = le.account_id
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
        COALESCE(mc.category, lt.metadata ->> 'category') AS category,
        mc.category IS NOT NULL AS category_is_custom,
        lt.transaction_type IN ('INTERNAL_TRANSFER') AS is_internal
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
    op.execute("DROP VIEW IF EXISTS assistant_read.account_balances_v1")
    op.execute("DROP VIEW IF EXISTS assistant_read.transactions_v1")
    op.execute(f"CREATE VIEW assistant_read.transactions_v1 AS {body.format(account=_ACCOUNT)}")
    op.execute(
        "CREATE VIEW assistant_read.account_balances_v1 AS " + _BALANCES.format(account=_ACCOUNT)
    )
    op.execute(f"GRANT SELECT ON assistant_read.transactions_v1 TO {ROLE}")
    op.execute(f"GRANT SELECT ON assistant_read.account_balances_v1 TO {ROLE}")


def upgrade() -> None:
    op.execute(f"""
        CREATE TABLE movement_classifications (
            account_id UUID NOT NULL REFERENCES accounts(id),
            transaction_id UUID NOT NULL REFERENCES ledger_transactions(id),
            category VARCHAR(40) NOT NULL CHECK (category ~ '{_CATEGORY_SHAPE}'),
            -- Which classifier produced this. It is what makes the table recomputable rather than
            -- merely overwritable: a row from an older version can be found and redone, and a
            -- change in behaviour can be attributed instead of guessed at.
            classifier_version VARCHAR(40) NOT NULL,
            classified_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            PRIMARY KEY (account_id, transaction_id)
        )
    """)
    op.execute("ALTER TABLE movement_classifications ENABLE ROW LEVEL SECURITY")
    op.execute(f"""
        CREATE POLICY movement_classifications_own_account ON movement_classifications
            FOR SELECT USING (account_id = {_ACCOUNT})
    """)
    op.execute(
        "CREATE POLICY movement_classifications_writes ON movement_classifications "
        "FOR ALL USING (true) WITH CHECK (true)"
    )

    op.execute("""
        CREATE TABLE movement_tags (
            account_id UUID NOT NULL REFERENCES accounts(id),
            transaction_id UUID NOT NULL REFERENCES ledger_transactions(id),
            -- Free text, lower case, and that is the whole difference from a category: a category
            -- is a closed vocabulary this service can name back, and a tag is whatever the holder
            -- wrote. Bounded so the field cannot carry a paragraph into a filter.
            tag VARCHAR(40) NOT NULL CHECK (btrim(tag) <> '' AND tag = lower(tag)),
            tagged_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            -- The same label twice on one movement is a duplicate, not two labels.
            PRIMARY KEY (account_id, transaction_id, tag)
        )
    """)
    op.execute("CREATE INDEX idx_movement_tags_label ON movement_tags (account_id, tag)")
    op.execute("ALTER TABLE movement_tags ENABLE ROW LEVEL SECURITY")
    op.execute(f"""
        CREATE POLICY movement_tags_own_account ON movement_tags
            FOR SELECT USING (account_id = {_ACCOUNT})
    """)
    op.execute(
        "CREATE POLICY movement_tags_writes ON movement_tags FOR ALL USING (true) WITH CHECK (true)"
    )

    _replace_transactions(_TRANSACTIONS_AFTER)


def downgrade() -> None:
    _replace_transactions(_TRANSACTIONS_BEFORE)
    op.execute("DROP TABLE IF EXISTS movement_tags")
    op.execute("DROP TABLE IF EXISTS movement_classifications")
