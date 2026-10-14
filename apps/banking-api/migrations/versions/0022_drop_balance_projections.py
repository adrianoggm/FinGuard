"""drop balance_projections, which nothing reads or writes

Revision ID: 0022_drop_balance_projections
Revises: 0021_bizum_recipient_snapshot
Create Date: 2026-10-14
"""

from alembic import op

revision = "0022_drop_balance_projections"
down_revision = "0021_bizum_recipient_snapshot"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Balances are computed from the ledger by the views; this projection was never filled.
    op.execute("DROP TABLE balance_projections")


def downgrade() -> None:
    op.execute("""
        CREATE TABLE balance_projections (
            account_id UUID PRIMARY KEY
                REFERENCES accounts(id),
            available_minor BIGINT NOT NULL,
            booked_minor BIGINT NOT NULL,
            currency CHAR(3) NOT NULL,
            version BIGINT NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL
        )
    """)
