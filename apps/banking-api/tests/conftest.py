import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest

from finguard_banking.bootstrap.config import get_settings


@pytest.fixture
async def connection() -> AsyncIterator[asyncpg.Connection]:
    """Conexión cuyo trabajo se deshace al terminar: los tests no dejan rastro.

    El libro mayor es inmutable, así que un test no podría borrar sus propios
    datos: la única limpieza posible es no confirmar la transacción.
    """
    conn: asyncpg.Connection = await asyncpg.connect(get_settings().database_url)
    transaction = conn.transaction()
    await transaction.start()
    try:
        yield conn
    finally:
        await transaction.rollback()
        await conn.close()


async def crear_cuenta(conn: asyncpg.Connection, currency: str = "EUR") -> uuid.UUID:
    customer_id = uuid.uuid4()
    account_id = uuid.uuid4()
    await conn.execute(
        "INSERT INTO customers (id, full_name) VALUES ($1, $2)", customer_id, "Cliente de prueba"
    )
    await conn.execute(
        "INSERT INTO accounts (id, customer_id, account_reference, currency, status) "
        "VALUES ($1, $2, $3, $4, 'ACTIVE')",
        account_id,
        customer_id,
        f"ES-{account_id.hex[:12]}",
        currency,
    )
    return account_id


async def crear_transaccion(conn: asyncpg.Connection) -> uuid.UUID:
    transaction_id = uuid.uuid4()
    await conn.execute(
        "INSERT INTO ledger_transactions (id, transaction_type, status, reference, occurred_at) "
        "VALUES ($1, 'TRANSFER', 'BOOKED', $2, NOW())",
        transaction_id,
        f"ref-{transaction_id.hex[:12]}",
    )
    return transaction_id


async def anotar(
    conn: asyncpg.Connection,
    transaction_id: uuid.UUID,
    account_id: uuid.UUID,
    entry_type: str,
    amount_minor: int,
    currency: str = "EUR",
) -> uuid.UUID:
    entry_id = uuid.uuid4()
    await conn.execute(
        "INSERT INTO ledger_entries "
        "(id, transaction_id, account_id, entry_type, amount_minor, currency) "
        "VALUES ($1, $2, $3, $4, $5, $6)",
        entry_id,
        transaction_id,
        account_id,
        entry_type,
        amount_minor,
        currency,
    )
    return entry_id


async def forzar_comprobacion(conn: asyncpg.Connection) -> None:
    """Dispara los triggers diferidos sin confirmar la transacción.

    La invariante del cuadre se evalúa al COMMIT; esto la evalúa ahora, que es la
    única forma de comprobarla sin dejar datos escritos.
    """
    await conn.execute("SET CONSTRAINTS ALL IMMEDIATE")
