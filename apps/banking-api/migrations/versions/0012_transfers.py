"""transfers: a contact can have an account, and an intent remembers where it is going

Bizum names a person and the bank finds the account. A transfer names the account, so two things
have to exist that did not: somewhere to keep a favourite's IBAN, and somewhere for an intent to
record the destination it was approved against.

**The intent snapshots the destination, and that is the substantive decision here.** A transfer to
a favourite could read the IBAN from `contacts` at confirmation time, and that would be wrong for
the same reason `amount_minor` is not read from anywhere: the preview showed a specific account and
the user approved *that*. If the contact is edited between preparing and confirming — a slip, a
second device, or somebody who should not have the session — the money would go where the row says
rather than where the screen said. A confirmation step whose subject can change underneath it is
not a confirmation step.

So `recipient_iban` is written when the intent is created, always, for every transfer. `contact_id`
stays when there was one, because knowing a transfer went to a favourite is worth keeping and the
audit trail is cheaper than reconstructing it.

`contact_id` becomes nullable, which is the other half: a transfer to an account the holder typed
has no contact and never will. It is still `NOT NULL` in practice for BIZUM, enforced by the
destination check rather than by the column, because the rule is about the pair.

Shape only in SQL. The mod-97 check digits are arithmetic and live in `value_objects/iban.py`,
where they can be tested over every single-character error — the same split the categories use.

Revision ID: 0012_transfers
Revises: 0011_movement_categories
Create Date: 2026-08-04
"""

from alembic import op

revision = "0012_transfers"
down_revision = "0011_movement_categories"
branch_labels = None
depends_on = None

ROLE = "finguard_query_reader"
_ACCOUNT = "current_setting('app.account_id', true)::uuid"

# Two letters, two check digits, then the country's own account number. Up to thirty-four in
# total: Spain uses twenty-four, and a contact list is exactly where a foreign account turns up.
_IBAN_SHAPE = r"^[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}$"


def upgrade() -> None:
    op.execute("ALTER TABLE contacts ADD COLUMN iban VARCHAR(34)")
    op.execute(f"""
        ALTER TABLE contacts ADD CONSTRAINT contacts_iban_shape
            CHECK (iban IS NULL OR iban ~ '{_IBAN_SHAPE}')
    """)
    # The same account twice in one holder's list is a data entry mistake, and the quiet outcome is
    # the dangerous one — resolving a name would become ambiguous between two rows that are the
    # same destination. The phone already has this constraint for the same reason.
    op.execute("""
        ALTER TABLE contacts ADD CONSTRAINT contacts_iban_once_per_account
            UNIQUE (account_id, iban)
    """)

    # The view is versioned by name, so a reader pinned to v1 keeps the shape it was written
    # against. A column added to a view a consumer selects `*` from is a breaking change to
    # somebody; naming the columns here is what makes this one additive.
    op.execute("DROP VIEW IF EXISTS assistant_read.contacts_v1")
    op.execute(f"""
        CREATE VIEW assistant_read.contacts_v1 AS
        SELECT id AS contact_id, account_id, display_name, phone, iban
        FROM contacts
        WHERE account_id = {_ACCOUNT}
    """)
    op.execute(f"GRANT SELECT ON assistant_read.contacts_v1 TO {ROLE}")

    op.execute("""
        ALTER TABLE payment_intents
            ADD COLUMN rail VARCHAR(20) NOT NULL DEFAULT 'BIZUM'
    """)
    op.execute("""
        ALTER TABLE payment_intents ADD CONSTRAINT payment_intents_rail
            CHECK (rail IN ('BIZUM', 'TRANSFER'))
    """)
    # Every existing row is a Bizum, so the default is right for the backfill and wrong as a
    # standing default: a rail nobody chose is a rail somebody will forget to send. Dropped once
    # the column is populated.
    op.execute("ALTER TABLE payment_intents ALTER COLUMN rail DROP DEFAULT")

    op.execute("ALTER TABLE payment_intents ALTER COLUMN contact_id DROP NOT NULL")
    op.execute("ALTER TABLE payment_intents ADD COLUMN recipient_name VARCHAR(140)")
    op.execute("ALTER TABLE payment_intents ADD COLUMN recipient_iban VARCHAR(34)")
    op.execute(f"""
        ALTER TABLE payment_intents ADD CONSTRAINT payment_intents_iban_shape
            CHECK (recipient_iban IS NULL OR recipient_iban ~ '{_IBAN_SHAPE}')
    """)
    # What each rail means, as one constraint rather than three nullable columns and a convention.
    # A Bizum goes to a contact and carries no account; a transfer carries the account it was
    # approved against, whether or not a contact was behind it.
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


def downgrade() -> None:
    op.execute("ALTER TABLE payment_intents DROP CONSTRAINT IF EXISTS payment_intents_destination")
    op.execute("ALTER TABLE payment_intents DROP CONSTRAINT IF EXISTS payment_intents_iban_shape")
    op.execute("DELETE FROM payment_intents WHERE rail <> 'BIZUM'")
    op.execute("ALTER TABLE payment_intents DROP COLUMN IF EXISTS recipient_iban")
    op.execute("ALTER TABLE payment_intents DROP COLUMN IF EXISTS recipient_name")
    op.execute("ALTER TABLE payment_intents DROP CONSTRAINT IF EXISTS payment_intents_rail")
    op.execute("ALTER TABLE payment_intents DROP COLUMN IF EXISTS rail")
    op.execute("ALTER TABLE payment_intents ALTER COLUMN contact_id SET NOT NULL")

    op.execute("DROP VIEW IF EXISTS assistant_read.contacts_v1")
    op.execute(f"""
        CREATE VIEW assistant_read.contacts_v1 AS
        SELECT id AS contact_id, account_id, display_name, phone
        FROM contacts
        WHERE account_id = {_ACCOUNT}
    """)
    op.execute(f"GRANT SELECT ON assistant_read.contacts_v1 TO {ROLE}")

    op.execute("ALTER TABLE contacts DROP CONSTRAINT IF EXISTS contacts_iban_once_per_account")
    op.execute("ALTER TABLE contacts DROP CONSTRAINT IF EXISTS contacts_iban_shape")
    op.execute("ALTER TABLE contacts DROP COLUMN IF EXISTS iban")
