"""vistas de lectura y permisos del rol de consulta

Implementa vision.md §8.6: el motor de consultas no lee las tablas, lee dos vistas,
y lo hace con un rol que no puede hacer nada más.

Las vistas también son el único sitio donde viven dos reglas que antes estaban
repetidas en el SQL del saldo, en el del agregado y en el de los justificantes:
qué cuenta como contabilizado y qué signo tiene cada asiento.

Revision ID: 0002_read_views
Revises: 0001_ledger
Create Date: 2026-07-30
"""

from alembic import op

revision = "0002_read_views"
down_revision = "0001_ledger"
branch_labels = None
depends_on = None

ROL = "finguard_query_reader"


def upgrade() -> None:
    op.execute("CREATE SCHEMA assistant_read")

    # Un asiento por fila, ya interpretado: sin entry_type ni status que el
    # consumidor tenga que recordar filtrar.
    op.execute("""
        CREATE VIEW assistant_read.transactions_v1 AS
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
            lt.metadata ->> 'category' AS category
        FROM ledger_transactions lt
        JOIN ledger_entries le ON le.transaction_id = lt.id
        WHERE lt.status = 'BOOKED'
    """)

    # LEFT JOIN a propósito: una cuenta sin movimientos aparece a cero, y una cuenta
    # que no existe no aparece. Son dos respuestas distintas.
    op.execute("""
        CREATE VIEW assistant_read.account_balances_v1 AS
        SELECT
            a.id AS account_id,
            a.currency,
            COALESCE(SUM(t.signed_amount_minor), 0) AS booked_minor
        FROM accounts a
        LEFT JOIN assistant_read.transactions_v1 t ON t.account_id = a.id
        GROUP BY a.id, a.currency
    """)

    # El rol nace en infrastructure/postgres/init/02-query-reader.sql. Si falta, esto
    # falla a propósito: sin rol, las vistas no protegen de nada.
    op.execute(f"GRANT USAGE ON SCHEMA assistant_read TO {ROL}")
    op.execute(f"GRANT SELECT ON assistant_read.transactions_v1 TO {ROL}")
    op.execute(f"GRANT SELECT ON assistant_read.account_balances_v1 TO {ROL}")

    # Sin USAGE en public no puede ni listar los nombres de las tablas. Las vistas
    # siguen funcionando porque se ejecutan con los permisos de su propietario.
    op.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")


def downgrade() -> None:
    op.execute("GRANT USAGE ON SCHEMA public TO PUBLIC")
    op.execute("DROP VIEW IF EXISTS assistant_read.account_balances_v1")
    op.execute("DROP VIEW IF EXISTS assistant_read.transactions_v1")
    op.execute("DROP SCHEMA IF EXISTS assistant_read")
