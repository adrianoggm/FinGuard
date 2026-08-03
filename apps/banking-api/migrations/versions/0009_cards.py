"""the cards an account holds, and which one each charge was made with

A card screen cannot be built from what was here. The ledger had `transaction_type =
'CARD_PAYMENT'` and nothing to say *which* card, so "what have I spent on the black one" was not a
question the database could answer — and a screen listing cards with figures beside them would have
been inventing the association.

Two things, therefore. A `cards` table, and a nullable `card_id` on `ledger_transactions`.

**Nullable, and it stays nullable.** A salary is not a card charge, and a `NOT NULL` here would
be claiming every movement came from a card. The CHECK is the honest version of the rule: a
`card_id` implies the type is CARD_PAYMENT, and the absence of one implies nothing.

**`last_four` and not a card number.** A PAN belongs in a vault, not in a ledger, and nothing here
needs one: a screen shows the last four so a person recognises which card, and the payment goes
through the account. Storing what we do not need is how a demo database becomes a breach.

`spending_limit_minor` is on the card rather than derived, because a limit is an agreement rather
than an observation. It is nullable: a debit card is limited by the balance, which is not the same
thing as having a limit of zero.

Revision ID: 0009_cards
Revises: 0008_audit_events
Create Date: 2026-08-03
"""

from alembic import op

revision = "0009_cards"
down_revision = "0008_audit_events"
branch_labels = None
depends_on = None

_ACCOUNT = "current_setting('app.account_id', true)::uuid"
ROLE = "finguard_query_reader"

BRANDS = ("VISA", "MASTERCARD")
KINDS = ("DEBIT", "CREDIT")
STATUSES = ("ACTIVE", "BLOCKED", "EXPIRED")


def upgrade() -> None:
    brands = ", ".join(f"'{brand}'" for brand in BRANDS)
    kinds = ", ".join(f"'{kind}'" for kind in KINDS)
    statuses = ", ".join(f"'{status}'" for status in STATUSES)

    op.execute(f"""
        CREATE TABLE cards (
            id UUID PRIMARY KEY,
            account_id UUID NOT NULL REFERENCES accounts(id),
            -- The name embossed on it, which is not always the holder's legal name.
            display_name VARCHAR(80) NOT NULL,
            brand VARCHAR(20) NOT NULL CHECK (brand IN ({brands})),
            kind VARCHAR(20) NOT NULL CHECK (kind IN ({kinds})),
            -- Four digits, never a PAN: a card number belongs in a vault, and nothing needs one.
            last_four CHAR(4) NOT NULL CHECK (last_four ~ '^[0-9]{{4}}$'),
            expires_month SMALLINT NOT NULL CHECK (expires_month BETWEEN 1 AND 12),
            expires_year SMALLINT NOT NULL CHECK (expires_year BETWEEN 2020 AND 2100),
            status VARCHAR(20) NOT NULL CHECK (status IN ({statuses})),
            -- An agreement, not an observation, so it is stored. NULL means "the balance is the
            -- limit", which is a debit card and is not the same as a limit of zero.
            spending_limit_minor BIGINT CHECK (spending_limit_minor > 0),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),

            CONSTRAINT cards_last_four_once UNIQUE (account_id, brand, last_four)
        )
    """)
    op.execute("CREATE INDEX idx_cards_account ON cards (account_id, status)")

    op.execute("ALTER TABLE ledger_transactions ADD COLUMN card_id UUID REFERENCES cards(id)")
    # The rule, as a constraint rather than as a convention: a charge that names a card is a card
    # charge. The other direction is deliberately not asserted — a CARD_PAYMENT with no card is an
    # older row, or a charge from a card this bank does not issue.
    op.execute("""
        ALTER TABLE ledger_transactions
            ADD CONSTRAINT ledger_transactions_card_implies_card_payment
            CHECK (card_id IS NULL OR transaction_type = 'CARD_PAYMENT')
    """)
    op.execute("""
        CREATE INDEX idx_transactions_card ON ledger_transactions (card_id, occurred_at DESC)
            WHERE card_id IS NOT NULL
    """)

    # Same shape as every other per-account table (0007): reads restricted to the caller's account,
    # writes per command so a permissive FOR ALL cannot OR the restriction away.
    op.execute("ALTER TABLE cards ENABLE ROW LEVEL SECURITY")
    op.execute(f"""
        CREATE POLICY cards_own_account ON cards
            FOR SELECT USING (account_id = {_ACCOUNT})
    """)
    op.execute("CREATE POLICY cards_inserts ON cards FOR INSERT WITH CHECK (true)")
    op.execute("CREATE POLICY cards_updates ON cards FOR UPDATE USING (true) WITH CHECK (true)")
    op.execute("CREATE POLICY cards_deletes ON cards FOR DELETE USING (true)")

    # The read view, like the others: the restricted role reaches this and never the table.
    op.execute(f"""
        CREATE VIEW assistant_read.cards_v1 AS
        SELECT
            id AS card_id,
            account_id,
            display_name,
            brand,
            kind,
            last_four,
            expires_month,
            expires_year,
            status,
            spending_limit_minor
        FROM cards
        WHERE account_id = {_ACCOUNT}
    """)
    op.execute(f"GRANT SELECT ON assistant_read.cards_v1 TO {ROLE}")

    # Every booked charge, one row each, with no grouping and no window.
    #
    # The first version of this grouped by `date_trunc('month', occurred_at)` — in UTC, because a
    # migration does not know the deployment's reporting zone. That is the two-clocks bug this
    # project already paid for once: a charge at 00:30 on the 1st in Madrid falls in the previous
    # month in UTC, so "this month on the black card" would have been short by it. Aggregating is
    # therefore the reader's job, which is also where the zone is known.
    #
    # A view rather than a query built elsewhere because "spent on this card" is a fact about the
    # ledger and belongs beside it — and because the query plan has no notion of a card, which is
    # right: a card is not a filter a sentence names.
    op.execute(f"""
        CREATE VIEW assistant_read.card_charges_v1 AS
        SELECT
            c.id AS card_id,
            c.account_id,
            t.occurred_at,
            e.amount_minor
        FROM cards c
        JOIN ledger_transactions t ON t.card_id = c.id
        JOIN ledger_entries e ON e.transaction_id = t.id AND e.account_id = c.account_id
        WHERE c.account_id = {_ACCOUNT}
          AND t.status = 'BOOKED'
          -- A charge is a debit on the holder's side. Summing both entries of a double-entry
          -- transaction would give zero, which is the mistake this line exists to avoid.
          AND e.entry_type = 'DEBIT'
    """)
    op.execute(f"GRANT SELECT ON assistant_read.card_charges_v1 TO {ROLE}")


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS assistant_read.card_charges_v1")
    op.execute("DROP VIEW IF EXISTS assistant_read.cards_v1")
    op.execute("DROP INDEX IF EXISTS idx_transactions_card")
    op.execute(
        "ALTER TABLE ledger_transactions "
        "DROP CONSTRAINT IF EXISTS ledger_transactions_card_implies_card_payment"
    )
    op.execute("ALTER TABLE ledger_transactions DROP COLUMN IF EXISTS card_id")
    for policy in ("cards_deletes", "cards_updates", "cards_inserts", "cards_own_account"):
        op.execute(f"DROP POLICY IF EXISTS {policy} ON cards")
    op.execute("DROP TABLE IF EXISTS cards")
