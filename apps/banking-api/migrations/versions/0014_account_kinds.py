"""an account has a kind, because a piggy bank is not a current account

The schema has allowed several accounts per customer since 0001 — `customer_id` is right there —
and nothing ever said what an account is *for*. With one per holder that was fine. With three it
decides which one a question is about, which one a salary lands in, and which one has a value
rather than a balance.

    CURRENT   money to spend. What every existing account is, and the default for that reason.
    SAVINGS   money set aside, possibly earning interest.
    ASSET     a holding whose worth is not its cost — crypto, funds, shares.

`ASSET` carries a problem this column does not solve and must not pretend to: a holding is worth
what the market says, and this ledger only knows what was paid. Until there is a price source with
a date, an asset account reports its **cost basis**. A balance that looks like a valuation and is
not is worse than no balance at all, so the kind is what lets a screen say which one it is showing.

No `DEFAULT` left behind, for the same reason `payment_intents.rail` has none: it exists long enough
to backfill the rows that are there, and then every writer has to say. An account kind nobody chose
is one somebody forgets to send.

Revision ID: 0014_account_kinds
Revises: 0013_internal_movements
Create Date: 2026-08-04
"""

from alembic import op

revision = "0014_account_kinds"
down_revision = "0013_internal_movements"
branch_labels = None
depends_on = None

KINDS = ("CURRENT", "SAVINGS", "ASSET")


def upgrade() -> None:
    allowed = ", ".join(f"'{kind}'" for kind in KINDS)
    op.execute("ALTER TABLE accounts ADD COLUMN kind VARCHAR(20) NOT NULL DEFAULT 'CURRENT'")
    op.execute(f"ALTER TABLE accounts ADD CONSTRAINT accounts_kind CHECK (kind IN ({allowed}))")
    op.execute("ALTER TABLE accounts ALTER COLUMN kind DROP DEFAULT")

    # A holder needs to tell two of their accounts apart on a screen, and a reference like
    # ES-0001-2 is not something anybody recognises. Optional: the clearing account has no holder
    # to name it for, and an account nobody named is not a bug.
    op.execute("ALTER TABLE accounts ADD COLUMN display_name VARCHAR(60)")


def downgrade() -> None:
    op.execute("ALTER TABLE accounts DROP COLUMN IF EXISTS display_name")
    op.execute("ALTER TABLE accounts DROP CONSTRAINT IF EXISTS accounts_kind")
    op.execute("ALTER TABLE accounts DROP COLUMN IF EXISTS kind")
