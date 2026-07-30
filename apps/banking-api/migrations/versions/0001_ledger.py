"""libro mayor de doble entrada: clientes, cuentas, asientos y proyección de saldo

El DDL sigue el de docs/vision.md §8.5. Se añaden las claves foráneas que el spec
describe en §8.1 pero no escribe, y las dos invariantes que ADR-002 y ADR-003 dan
por ciertas: que cada transacción cuadre y que los asientos no se toquen.

Revision ID: 0001_ledger
Create Date: 2026-07-30
"""

from alembic import op

revision = "0001_ledger"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE customers (
            id UUID PRIMARY KEY,
            full_name VARCHAR(140) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    op.execute("""
        CREATE TABLE accounts (
            id UUID PRIMARY KEY,
            customer_id UUID NOT NULL REFERENCES customers(id),
            account_reference VARCHAR(64) NOT NULL UNIQUE,
            currency CHAR(3) NOT NULL,
            status VARCHAR(30) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            version BIGINT NOT NULL DEFAULT 0
        )
    """)

    op.execute("""
        CREATE TABLE ledger_transactions (
            id UUID PRIMARY KEY,
            transaction_type VARCHAR(40) NOT NULL,
            status VARCHAR(30) NOT NULL,
            reference VARCHAR(120) NOT NULL UNIQUE,
            description TEXT,
            metadata JSONB NOT NULL DEFAULT '{}',
            occurred_at TIMESTAMPTZ NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    op.execute("""
        CREATE TABLE ledger_entries (
            id UUID PRIMARY KEY,
            transaction_id UUID NOT NULL
                REFERENCES ledger_transactions(id),
            account_id UUID NOT NULL
                REFERENCES accounts(id),
            entry_type VARCHAR(10) NOT NULL
                CHECK (entry_type IN ('DEBIT', 'CREDIT')),
            amount_minor BIGINT NOT NULL
                CHECK (amount_minor > 0),
            currency CHAR(3) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

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

    # Índices de docs/vision.md §8.7 que aplican a estas tablas.
    op.execute("CREATE INDEX idx_ledger_entries_account ON ledger_entries(account_id)")
    op.execute("CREATE INDEX idx_ledger_entries_transaction ON ledger_entries(transaction_id)")
    op.execute("CREATE INDEX idx_transactions_occurred_at ON ledger_transactions(occurred_at)")
    op.execute(
        "CREATE INDEX idx_transactions_type_date "
        "ON ledger_transactions(transaction_type, occurred_at)"
    )
    op.execute("CREATE INDEX idx_transactions_metadata ON ledger_transactions USING GIN(metadata)")

    # ADR-003: toda transacción tiene débitos y créditos equilibrados.
    # Se comprueba por moneda, no sobre el total: 25 EUR al debe contra 25 USD al
    # haber suman cero y no son una transacción válida.
    # Es un CONSTRAINT TRIGGER diferido, así que se evalúa al COMMIT y los asientos
    # de una misma transacción pueden insertarse en varias sentencias.
    op.execute("""
        CREATE FUNCTION assert_transaction_is_balanced() RETURNS TRIGGER AS $$
        DECLARE
            descuadres INTEGER;
            monedas_ajenas INTEGER;
        BEGIN
            SELECT COUNT(*) INTO descuadres FROM (
                SELECT currency,
                       SUM(CASE WHEN entry_type = 'DEBIT' THEN amount_minor
                                ELSE -amount_minor END) AS delta
                FROM ledger_entries
                WHERE transaction_id = NEW.transaction_id
                GROUP BY currency
            ) por_moneda
            WHERE delta <> 0;

            IF descuadres > 0 THEN
                RAISE EXCEPTION
                    'transaccion % desequilibrada: los debitos y creditos no cuadran por moneda',
                    NEW.transaction_id;
            END IF;

            SELECT COUNT(*) INTO monedas_ajenas
            FROM ledger_entries e
            JOIN accounts a ON a.id = e.account_id
            WHERE e.transaction_id = NEW.transaction_id
              AND e.currency <> a.currency;

            IF monedas_ajenas > 0 THEN
                RAISE EXCEPTION
                    'transaccion %: hay asientos en una moneda distinta a la de su cuenta',
                    NEW.transaction_id;
            END IF;

            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql
    """)

    # Solo en INSERT: UPDATE y DELETE los bloquea el trigger de inmutabilidad.
    op.execute("""
        CREATE CONSTRAINT TRIGGER ledger_entries_balanced
            AFTER INSERT ON ledger_entries
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION assert_transaction_is_balanced()
    """)

    # ADR-002: los movimientos no se modifican ni se borran; una corrección es una
    # transacción compensatoria nueva. El status de ledger_transactions sí cambia
    # (por ejemplo a BOOKED), por eso ahí solo se bloquea el DELETE.
    op.execute("""
        CREATE FUNCTION reject_ledger_mutation() RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION
                'el libro mayor es inmutable: % sobre % no esta permitido, '
                'usa una transaccion compensatoria',
                TG_OP, TG_TABLE_NAME;
        END;
        $$ LANGUAGE plpgsql
    """)

    op.execute("""
        CREATE TRIGGER ledger_entries_immutable
            BEFORE UPDATE OR DELETE ON ledger_entries
            FOR EACH ROW EXECUTE FUNCTION reject_ledger_mutation()
    """)

    op.execute("""
        CREATE TRIGGER ledger_transactions_undeletable
            BEFORE DELETE ON ledger_transactions
            FOR EACH ROW EXECUTE FUNCTION reject_ledger_mutation()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS ledger_transactions_undeletable ON ledger_transactions")
    op.execute("DROP TRIGGER IF EXISTS ledger_entries_immutable ON ledger_entries")
    op.execute("DROP TRIGGER IF EXISTS ledger_entries_balanced ON ledger_entries")
    op.execute("DROP FUNCTION IF EXISTS reject_ledger_mutation()")
    op.execute("DROP FUNCTION IF EXISTS assert_transaction_is_balanced()")
    op.execute("DROP TABLE IF EXISTS balance_projections")
    op.execute("DROP TABLE IF EXISTS ledger_entries")
    op.execute("DROP TABLE IF EXISTS ledger_transactions")
    op.execute("DROP TABLE IF EXISTS accounts")
    op.execute("DROP TABLE IF EXISTS customers")
