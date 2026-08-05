"""what was bought with each card, not only how much

`card_charges_v1` gave a card id, a timestamp and an amount, which is everything a total needs and
nothing a person can read. A card screen that shows "437,92 € en 10 cargos" and cannot then show the
ten is a screen that asks to be trusted — the same objection the whole product is built against.

So the view gains what the movements list already has: the description, the shop and the category.
The data was always there, in `ledger_transactions` and its metadata; it simply was not exposed on
this path.

`transaction_id` comes with it, because a charge somebody is looking at should be findable in the
ledger rather than only visible in a list.

Revision ID: 0010_card_charges_detail
Revises: 0009_cards
Create Date: 2026-08-03
"""

from alembic import op

revision = "0010_card_charges_detail"
down_revision = "0009_cards"
branch_labels = None
depends_on = None

_ACCOUNT = "current_setting('app.account_id', true)::uuid"
ROLE = "finguard_query_reader"

_CHARGES = """
    SELECT
        c.id AS card_id,
        c.account_id,
        t.id AS transaction_id,
        t.occurred_at,
        t.description,
        t.metadata ->> 'merchant_name' AS merchant_name,
        t.metadata ->> 'category' AS category,
        e.amount_minor
    FROM cards c
    JOIN ledger_transactions t ON t.card_id = c.id
    JOIN ledger_entries e ON e.transaction_id = t.id AND e.account_id = c.account_id
    WHERE c.account_id = {account}
      AND t.status = 'BOOKED'
      -- A charge is a debit on the holder's side. Summing both entries of a double-entry
      -- transaction would give zero, which is the mistake this line exists to avoid.
      AND e.entry_type = 'DEBIT'
"""

_PREVIOUS = """
    SELECT
        c.id AS card_id,
        c.account_id,
        t.occurred_at,
        e.amount_minor
    FROM cards c
    JOIN ledger_transactions t ON t.card_id = c.id
    JOIN ledger_entries e ON e.transaction_id = t.id AND e.account_id = c.account_id
    WHERE c.account_id = {account}
      AND t.status = 'BOOKED'
      AND e.entry_type = 'DEBIT'
"""


def _replace(body: str) -> None:
    # CREATE OR REPLACE cannot add columns to a view, so it is dropped and rebuilt. Nothing depends
    # on it but the reader's SELECT grant, which is re-issued below.
    op.execute("DROP VIEW IF EXISTS assistant_read.card_charges_v1")
    op.execute(f"CREATE VIEW assistant_read.card_charges_v1 AS {body.format(account=_ACCOUNT)}")
    op.execute(f"GRANT SELECT ON assistant_read.card_charges_v1 TO {ROLE}")


def upgrade() -> None:
    _replace(_CHARGES)


def downgrade() -> None:
    _replace(_PREVIOUS)
