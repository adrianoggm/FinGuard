"""${message}"""
from alembic import op
import sqlalchemy as sa
${upgrades if upgrades else ""}

def upgrade() -> None:
    ${upgrades if upgrades else "pass"}

def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
