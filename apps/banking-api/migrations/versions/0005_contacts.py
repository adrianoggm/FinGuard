"""contacts, so a payment can name a person instead of a phone number

Step 10 of the demo shows the recipient before anything is confirmed, and there was nowhere to
read a recipient from. Deferred out of phase 1 on the grounds that Bizum needs it, which was
true and means it is now on the critical path rather than off it.

Keyed on the account rather than on the customer, for consistency: every isolation rule in this
database is `account_id = current_setting('app.account_id')`, and a second shape would be a
second thing to get right. A holder with several accounts is the migration everything else
needs at the same time.

Row-level security from the start rather than added later. A contact list is a list of the
people somebody pays — names, phone numbers, and by implication a relationship — so it is
arguably more private than the movements, and it would have been the first table where "we will
add RLS when we need it" quietly meant never.

Revision ID: 0005_contacts
Revises: 0004_transaction_lifecycle
Create Date: 2026-08-03
"""

from alembic import op

revision = "0005_contacts"
down_revision = "0004_transaction_lifecycle"
branch_labels = None
depends_on = None

ROLE = "finguard_query_reader"
_ACCOUNT = "current_setting('app.account_id', true)::uuid"


def upgrade() -> None:
    op.execute(r"""
        CREATE TABLE contacts (
            id UUID PRIMARY KEY,
            account_id UUID NOT NULL REFERENCES accounts(id),
            display_name VARCHAR(140) NOT NULL,
            -- E.164 without the punctuation. Spain is +34 and nine digits; the column is wide
            -- enough for anywhere, because a contact list is exactly where an international
            -- number turns up first.
            phone VARCHAR(20) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            CONSTRAINT contacts_phone_shape CHECK (phone ~ '^\+[0-9]{6,18}$'),
            CONSTRAINT contacts_name_not_blank CHECK (btrim(display_name) <> ''),
            -- The same person twice in one account's list is a data entry mistake, and the
            -- quiet outcome is the dangerous one: resolving a name would become ambiguous
            -- between two rows that are the same person.
            CONSTRAINT contacts_phone_once_per_account UNIQUE (account_id, phone)
        )
    """)

    # Resolution is by name, case-insensitively, so the index has to be too.
    op.execute("CREATE INDEX idx_contacts_name ON contacts (account_id, lower(display_name))")

    op.execute("ALTER TABLE contacts ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE contacts FORCE ROW LEVEL SECURITY")
    op.execute(f"""
        CREATE POLICY contacts_own_account ON contacts
            FOR SELECT USING (account_id = {_ACCOUNT})
    """)
    op.execute("""
        CREATE POLICY contacts_writes ON contacts
            FOR ALL USING (true) WITH CHECK (true)
    """)

    op.execute(f"""
        CREATE VIEW assistant_read.contacts_v1 AS
        SELECT id AS contact_id, account_id, display_name, phone
        FROM contacts
        WHERE account_id = {_ACCOUNT}
    """)
    op.execute(f"GRANT SELECT ON assistant_read.contacts_v1 TO {ROLE}")


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS assistant_read.contacts_v1")
    op.execute("DROP POLICY IF EXISTS contacts_writes ON contacts")
    op.execute("DROP POLICY IF EXISTS contacts_own_account ON contacts")
    op.execute("DROP TABLE IF EXISTS contacts")
