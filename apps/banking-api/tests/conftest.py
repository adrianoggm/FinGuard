import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

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


async def crear_transaccion(
    conn: asyncpg.Connection,
    status: str = "BOOKED",
    *,
    categoria: str | None = None,
    comercio: str | None = None,
    ocurrido: datetime | None = None,
    descripcion: str = "Movimiento de prueba",
) -> uuid.UUID:
    transaction_id = uuid.uuid4()
    metadatos: dict[str, str] = {}
    if categoria is not None:
        metadatos["category"] = categoria
    if comercio is not None:
        metadatos["merchant_name"] = comercio

    await conn.execute(
        "INSERT INTO ledger_transactions "
        "(id, transaction_type, status, reference, description, metadata, occurred_at) "
        "VALUES ($1, 'TRANSFER', $2, $3, $4, $5::jsonb, COALESCE($6::timestamptz, NOW()))",
        transaction_id,
        status,
        f"ref-{transaction_id.hex[:12]}",
        descripcion,
        json.dumps(metadatos),
        ocurrido,
    )
    return transaction_id


async def registrar(
    conn: asyncpg.Connection,
    cuenta: uuid.UUID,
    contrapartida: uuid.UUID,
    importe_minor: int,
    **kwargs: Any,
) -> uuid.UUID:
    """Un movimiento completo y cuadrado. Importe positivo = entra dinero a `cuenta`."""
    transaccion = await crear_transaccion(conn, **kwargs)
    entra = importe_minor > 0
    importe = abs(importe_minor)
    await anotar(conn, transaccion, cuenta, "CREDIT" if entra else "DEBIT", importe)
    await anotar(conn, transaccion, contrapartida, "DEBIT" if entra else "CREDIT", importe)
    return transaccion


def como_pool(conn: asyncpg.Connection) -> Any:
    """Hace pasar una conexión suelta por un pool.

    Los repositorios piden un pool y hacen `acquire()`. En los tests hay que
    reutilizar la conexión del fixture, porque los datos viven en una transacción
    sin confirmar que otra conexión no vería.
    """

    class _PoolDeUnaConexion:
        def acquire(self) -> Any:
            @asynccontextmanager
            async def prestada() -> AsyncIterator[asyncpg.Connection]:
                yield conn

            return prestada()

    return _PoolDeUnaConexion()


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
