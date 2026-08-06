"""a payment intent can point at another account of the same holder

Money between a holder's own accounts existed in the ledger and could not be created. Migration
0013 taught the read views to recognise an `INTERNAL_TRANSFER` and 0014 gave accounts a kind, but
the only writer was the dataset generator, in raw SQL. So a holder could be shown a piggy bank and
had no way to put anything in it.

**A third rail rather than a flag on the transfer.** `INTERNAL` is not a setting of `TRANSFER`: the
destination is an account id rather than an IBAN, no clearing account is involved because the money
does not leave, and the resulting transaction is a type the read views already exclude from
spending. The union in `destination.py` makes the same argument — the three shapes cannot be
spelled as one row of nullable columns without admitting states nobody means.

**`to_account_id` and not an IBAN.** An account of this holder is identified by its id: routing it
through an IBAN would mean resolving the number back to a row, which is a lookup that can find
somebody else's account. The foreign key is the whole check.

What still holds it to the holder is the same barrier as everywhere else — the account has to be in
`assistant_read.my_accounts_v1` for the caller to have seen it, and the command re-checks it before
anything is written. A foreign key says the account exists; it does not say it is yours.

Revision ID: 0017_internal_transfers
Revises: 0016_holder_scope
Create Date: 2026-08-05
"""

from alembic import op

revision = "0017_internal_transfers"
down_revision = "0016_holder_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE payment_intents
            ADD COLUMN to_account_id UUID REFERENCES accounts (id)
    """)

    # An account cannot pay itself. Not a hypothetical: a form with two identical dropdowns
    # defaulted to the same account, and a transaction debiting and crediting one account balances
    # perfectly — so every integrity check in this ledger would have accepted it.
    op.execute("""
        ALTER TABLE payment_intents ADD CONSTRAINT payment_intents_not_to_self
            CHECK (to_account_id IS NULL OR to_account_id <> account_id)
    """)

    op.execute("ALTER TABLE payment_intents DROP CONSTRAINT IF EXISTS payment_intents_rail")
    op.execute("""
        ALTER TABLE payment_intents ADD CONSTRAINT payment_intents_rail
            CHECK (rail IN ('BIZUM', 'TRANSFER', 'INTERNAL'))
    """)

    # The destination check, restated with the third rail. Still one constraint about the pairing
    # rather than three nullable columns and a convention: an INTERNAL carries an account of this
    # holder and nothing else, so an intent that named both an IBAN and an own account — which is
    # two destinations — cannot be written.
    op.execute("ALTER TABLE payment_intents DROP CONSTRAINT IF EXISTS payment_intents_destination")
    op.execute("""
        ALTER TABLE payment_intents ADD CONSTRAINT payment_intents_destination CHECK (
            (rail = 'BIZUM'
                AND contact_id IS NOT NULL
                AND recipient_iban IS NULL
                AND recipient_name IS NULL
                AND to_account_id IS NULL)
            OR
            (rail = 'TRANSFER'
                AND recipient_iban IS NOT NULL
                AND btrim(recipient_name) <> ''
                AND to_account_id IS NULL)
            OR
            (rail = 'INTERNAL'
                AND to_account_id IS NOT NULL
                AND contact_id IS NULL
                AND recipient_iban IS NULL)
        )
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE payment_intents DROP CONSTRAINT IF EXISTS payment_intents_destination")
    # Rows that could not exist under the older constraint. Deleted rather than migrated, exactly as
    # 0012 does with transfers: an intent is a proposal that expires on its own, so the worst this
    # costs is somebody preparing again.
    op.execute("DELETE FROM payment_intents WHERE rail = 'INTERNAL'")
    op.execute("""
        ALTER TABLE payment_intents ADD CONSTRAINT payment_intents_destination CHECK (
            (rail = 'BIZUM'
                AND contact_id IS NOT NULL
                AND recipient_iban IS NULL
                AND recipient_name IS NULL)
            OR
            (rail = 'TRANSFER'
                AND recipient_iban IS NOT NULL
                AND btrim(recipient_name) <> '')
        )
    """)
    op.execute("ALTER TABLE payment_intents DROP CONSTRAINT IF EXISTS payment_intents_rail")
    op.execute("""
        ALTER TABLE payment_intents ADD CONSTRAINT payment_intents_rail
            CHECK (rail IN ('BIZUM', 'TRANSFER'))
    """)
    op.execute("ALTER TABLE payment_intents DROP CONSTRAINT IF EXISTS payment_intents_not_to_self")
    op.execute("ALTER TABLE payment_intents DROP COLUMN IF EXISTS to_account_id")
