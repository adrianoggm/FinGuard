"""a card charge is filed under the same category as the movement it is

0015 gave classification four layers and resolved them in `transactions_v1`. `card_charges_v1` was
not touched, and 0016 carried its body forward unchanged, so the card screen still read
`user ?? bank` while everything else read `user ?? system ?? bank`. One layer missing, in one view.

It is not theoretical and it is on screen in the demo. The dataset reclassifies Steam from the
bank's LEISURE to SUBSCRIPTIONS, and every one of those purchases is a card payment:

    movements screen    Steam · SUBSCRIPTIONS
    card screen         Steam · LEISURE

The same purchase, two categories, two taps apart. Neither figure is wrong — that is what makes it
worth a migration rather than a note. A person who sees both learns that one of the screens is
lying and has no way to tell which.

**No new column.** `category_source` would say *why* a row is filed where it is, and the client
deliberately shows a caption only for USER and NONE — SYSTEM is the ordinary case and gets none. So
carrying the source here would add a field to four layers that nothing renders. The value is the
whole fix; the source can come the day something displays it.

Revision ID: 0018_card_charge_layers
Revises: 0017_internal_transfers
Create Date: 2026-08-05
"""

from alembic import op

# `alembic_version.version_num` is varchar(32). A longer id fails on the very last statement of an
# otherwise correct migration, which is a confusing way to find out.
revision = "0018_card_charge_layers"
down_revision = "0017_internal_transfers"
branch_labels = None
depends_on = None

ROLE = "finguard_query_reader"

# The same one 0016 filters every view through. Repeated rather than imported: a migration is a
# snapshot of what was run, and one that changes meaning when another file is edited is not one.
_VISIBLE = "(SELECT account_id FROM assistant_read.my_accounts_v1)"

_CHARGES = """
    SELECT
        c.id AS card_id,
        c.account_id,
        t.id AS transaction_id,
        t.occurred_at,
        t.description,
        t.metadata ->> 'merchant_name' AS merchant_name,
        {category} AS category,
        e.amount_minor
    FROM cards c
    JOIN ledger_transactions t ON t.card_id = c.id
    JOIN ledger_entries e ON e.transaction_id = t.id AND e.account_id = c.account_id
    LEFT JOIN movement_categories mc
           ON mc.transaction_id = t.id AND mc.account_id = c.account_id
    {system}
    WHERE c.account_id IN {visible}
      AND t.status = 'BOOKED'
      AND e.entry_type = 'DEBIT'
"""

# The holder's opinion, then this service's, then the bank's — the order `transactions_v1` has
# resolved since 0015, spelled the same way so a reader comparing the two sees one rule.
_AFTER = {
    "category": "COALESCE(mc.category, sc.category, t.metadata ->> 'category')",
    "system": (
        "LEFT JOIN movement_classifications sc "
        "ON sc.transaction_id = t.id AND sc.account_id = c.account_id"
    ),
}
_BEFORE = {"category": "COALESCE(mc.category, t.metadata ->> 'category')", "system": ""}


def _rebuild(body: dict[str, str]) -> None:
    """Only this view. Nothing depends on it — `account_balances_v1` hangs off `transactions_v1`,
    not off the charges — so there is no cascade to reason about and none is used."""
    op.execute("DROP VIEW IF EXISTS assistant_read.card_charges_v1")
    op.execute(
        "CREATE VIEW assistant_read.card_charges_v1 AS " + _CHARGES.format(visible=_VISIBLE, **body)
    )
    op.execute(f"GRANT SELECT ON assistant_read.card_charges_v1 TO {ROLE}")


def upgrade() -> None:
    _rebuild(_AFTER)


def downgrade() -> None:
    _rebuild(_BEFORE)
