"""isolation by holder rather than by account, stated once instead of five times

A holder with a current account, a piggy bank and a fund could see one of them. Every read view
carried its own `WHERE account_id = current_setting('app.account_id')`, so the rule was written
five times and widening it meant editing five places and hoping.

**What actually enforces isolation here, and it is not the policies.** `relforcerowsecurity` is
false on every protected table and deliberately: 0007 took it off because row-level security
applies inside trigger functions, and `assert_transaction_is_balanced` summed a transaction's
entries, saw none, and would have accepted an unbalanced transaction in silence. The owner is
exempt so those triggers can work. What enforces isolation is the privilege barrier — the reader
role cannot reach the base tables — plus these views' WHERE clause. The policies below move with
them to stay correct for the day a migration grants table access, and not because they are
guarding anything today.

`my_accounts_v1` is the one place the rule lives now. Every other view filters through it, so
widening or narrowing what a session can see is one edit rather than five.

**Two settings, and the disjunction is the point.** `app.customer_id` scopes to a holder;
`app.account_id` remains and still admits exactly the account it names. That keeps this additive:
a caller that declares only an account sees only that account, precisely as before, so no read path
silently returns nothing while it is being migrated. And it is safe in the widening direction too —
a session can reach an account it proved, or accounts belonging to the same holder as one it
proved, and nothing else.

The holder is declared by the caller rather than derived inside SQL. A policy resolving the holder
from the account would read `accounts` from inside a policy on `accounts`, which recurses.

Revision ID: 0016_holder_scope
Revises: 0015_classification_layers
Create Date: 2026-08-04
"""

from alembic import op

revision = "0016_holder_scope"
down_revision = "0015_classification_layers"
branch_labels = None
depends_on = None

ROLE = "finguard_query_reader"

# `NULLIF` as well as the `true` flag, and both are needed for different reasons. The flag makes an
# *unset* variable NULL instead of an error; the NULLIF makes an *empty* one NULL too — which is
# what a session gets when it declares an account that does not exist, and "" is not a uuid.
# Without it, asking about a stranger's account id raised a type error instead of finding nothing.
_ACCOUNT = "NULLIF(current_setting('app.account_id', true), '')::uuid"
_HOLDER = "NULLIF(current_setting('app.customer_id', true), '')::uuid"

# What a session may see, in one place. Every view below filters through it, and so does every
# policy — so the answer to "whose rows are these" is written once.
_VISIBLE = "(SELECT account_id FROM assistant_read.my_accounts_v1)"

# Tables whose rows belong to an account. `accounts` is not here: it is the one that decides.
SCOPED = (
    "ledger_entries",
    "contacts",
    "payment_intents",
    "cards",
    "movement_categories",
    "movement_classifications",
    "movement_tags",
    "audit_events",
)

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
        COALESCE(mc.category, sc.category, lt.metadata ->> 'category') AS category,
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
      AND le.account_id IN {visible}
"""

_BALANCES = """
    SELECT
        a.account_id,
        a.currency,
        COALESCE(SUM(t.signed_amount_minor), 0) AS booked_minor
    FROM assistant_read.my_accounts_v1 a
    LEFT JOIN assistant_read.transactions_v1 t ON t.account_id = a.account_id
    GROUP BY a.account_id, a.currency
"""

_CONTACTS = """
    SELECT id AS contact_id, account_id, display_name, phone, iban
    FROM contacts
    WHERE account_id IN {visible}
"""

_CARDS = """
    SELECT id AS card_id, account_id, display_name, brand, kind, last_four,
           expires_month, expires_year, status, spending_limit_minor
    FROM cards
    WHERE account_id IN {visible}
"""

_CHARGES = """
    SELECT
        c.id AS card_id,
        c.account_id,
        t.id AS transaction_id,
        t.occurred_at,
        t.description,
        t.metadata ->> 'merchant_name' AS merchant_name,
        COALESCE(mc.category, t.metadata ->> 'category') AS category,
        e.amount_minor
    FROM cards c
    JOIN ledger_transactions t ON t.card_id = c.id
    JOIN ledger_entries e ON e.transaction_id = t.id AND e.account_id = c.account_id
    LEFT JOIN movement_categories mc
           ON mc.transaction_id = t.id AND mc.account_id = c.account_id
    WHERE c.account_id IN {visible}
      AND t.status = 'BOOKED'
      AND e.entry_type = 'DEBIT'
"""

VIEWS = ("transactions_v1", "account_balances_v1", "contacts_v1", "cards_v1", "card_charges_v1")


def _views(visible: str, balances: str) -> None:
    """Rebuild all five, in dependency order.

    One statement per `op.execute`: asyncpg prepares what it is given, and a string with semicolons
    in it is not one statement. Dropped explicitly and never with CASCADE — a cascade takes whatever
    happens to depend on a view, which on a database that has drifted is a list nobody enumerated.
    """
    for view in reversed(VIEWS):
        op.execute(f"DROP VIEW IF EXISTS assistant_read.{view}")
    bodies = {
        "transactions_v1": _TRANSACTIONS.format(visible=visible),
        "account_balances_v1": balances,
        "contacts_v1": _CONTACTS.format(visible=visible),
        "cards_v1": _CARDS.format(visible=visible),
        "card_charges_v1": _CHARGES.format(visible=visible),
    }
    for view in VIEWS:
        op.execute(f"CREATE VIEW assistant_read.{view} AS {bodies[view]}")
        op.execute(f"GRANT SELECT ON assistant_read.{view} TO {ROLE}")


def upgrade() -> None:
    # Who owns an account, answerable by a role that cannot read the accounts table.
    #
    # The reader deliberately has no privilege on any base table — that barrier is what actually
    # enforces isolation here — so it cannot look a holder up, and the benchmark found this the
    # first time it ran: `relation "accounts" does not exist`. SECURITY DEFINER lets it ask the
    # question without being able to ask any other, which is the narrowest hole that works.
    #
    # `search_path` is pinned. A SECURITY DEFINER function that resolves names against the caller's
    # path is the classic way one becomes a privilege escalation.
    op.execute("""
        CREATE FUNCTION assistant_read.holder_of(account uuid) RETURNS uuid
        LANGUAGE sql STABLE SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS 'SELECT customer_id FROM accounts WHERE id = account'
    """)
    op.execute(f"GRANT EXECUTE ON FUNCTION assistant_read.holder_of(uuid) TO {ROLE}")

    op.execute(f"""
        CREATE VIEW assistant_read.my_accounts_v1 AS
        SELECT id AS account_id, customer_id, currency, status, kind, display_name
        FROM accounts
        WHERE customer_id = {_HOLDER}
           OR id = {_ACCOUNT}
    """)
    op.execute(f"GRANT SELECT ON assistant_read.my_accounts_v1 TO {ROLE}")

    _views(_VISIBLE, _BALANCES)

    # Dormant, and correct anyway. `accounts` decides and every other table asks it, so the rule
    # is in one place here too.
    op.execute("DROP POLICY IF EXISTS accounts_own_account ON accounts")
    op.execute(f"""
        CREATE POLICY accounts_own_account ON accounts
            FOR SELECT USING (customer_id = {_HOLDER} OR id = {_ACCOUNT})
    """)
    for table in SCOPED:
        op.execute(f"DROP POLICY IF EXISTS {table}_own_account ON {table}")
        op.execute(f"""
            CREATE POLICY {table}_own_account ON {table}
                FOR SELECT USING (account_id IN (SELECT id FROM accounts))
        """)


# What a session could see before: exactly the account it declared.
_ONE_ACCOUNT = f"(SELECT {_ACCOUNT})"

_BALANCES_BEFORE = f"""
    SELECT a.id AS account_id, a.currency,
           COALESCE(SUM(t.signed_amount_minor), 0) AS booked_minor
    FROM accounts a
    LEFT JOIN assistant_read.transactions_v1 t ON t.account_id = a.id
    WHERE a.id = {_ACCOUNT}
    GROUP BY a.id, a.currency
"""


def downgrade() -> None:
    for table in SCOPED:
        op.execute(f"DROP POLICY IF EXISTS {table}_own_account ON {table}")
        op.execute(f"""
            CREATE POLICY {table}_own_account ON {table}
                FOR SELECT USING (account_id = {_ACCOUNT})
        """)
    op.execute("DROP POLICY IF EXISTS accounts_own_account ON accounts")
    op.execute(f"CREATE POLICY accounts_own_account ON accounts FOR SELECT USING (id = {_ACCOUNT})")

    _views(_ONE_ACCOUNT, _BALANCES_BEFORE)
    op.execute("DROP VIEW IF EXISTS assistant_read.my_accounts_v1")
    op.execute("DROP FUNCTION IF EXISTS assistant_read.holder_of(uuid)")
