"""El saldo sale de los asientos, no de una columna que alguien haya escrito."""

import uuid

import asyncpg
import pytest
from conftest import anotar, como_pool, crear_cuenta, crear_transaccion

from finguard_banking.adapters.outbound.postgres.ledger_repository import (
    PostgresLedgerRepository,
)
from finguard_banking.domain.errors import AccountNotFoundError

pytestmark = pytest.mark.integration


async def test_una_cuenta_sin_movimientos_tiene_saldo_cero(
    connection: asyncpg.Connection,
) -> None:
    cuenta = await crear_cuenta(connection)
    repositorio = PostgresLedgerRepository(como_pool(connection))

    saldo = await repositorio.balance_of(cuenta)

    assert saldo.booked_minor == 0
    assert saldo.available_minor == 0
    assert saldo.currency == "EUR"


async def test_un_debito_resta_y_un_credito_suma(connection: asyncpg.Connection) -> None:
    origen = await crear_cuenta(connection)
    destino = await crear_cuenta(connection)
    transaccion = await crear_transaccion(connection)
    await anotar(connection, transaccion, origen, "DEBIT", 2500)
    await anotar(connection, transaccion, destino, "CREDIT", 2500)
    repositorio = PostgresLedgerRepository(como_pool(connection))

    # Enviar dinero se anota como débito en la cuenta de quien lo envía.
    assert (await repositorio.balance_of(origen)).booked_minor == -2500
    assert (await repositorio.balance_of(destino)).booked_minor == 2500


async def test_el_saldo_acumula_varias_transacciones(connection: asyncpg.Connection) -> None:
    cuenta = await crear_cuenta(connection)
    contrapartida = await crear_cuenta(connection)
    for importe in (10_000, 2_549):
        transaccion = await crear_transaccion(connection)
        await anotar(connection, transaccion, cuenta, "CREDIT", importe)
        await anotar(connection, transaccion, contrapartida, "DEBIT", importe)
    repositorio = PostgresLedgerRepository(como_pool(connection))

    assert (await repositorio.balance_of(cuenta)).booked_minor == 12_549


async def test_lo_que_no_esta_contabilizado_no_cuenta(connection: asyncpg.Connection) -> None:
    cuenta = await crear_cuenta(connection)
    contrapartida = await crear_cuenta(connection)

    contabilizada = await crear_transaccion(connection, status="BOOKED")
    await anotar(connection, contabilizada, cuenta, "CREDIT", 5_000)
    await anotar(connection, contabilizada, contrapartida, "DEBIT", 5_000)

    pendiente = await crear_transaccion(connection, status="PENDING")
    await anotar(connection, pendiente, cuenta, "CREDIT", 99_999)
    await anotar(connection, pendiente, contrapartida, "DEBIT", 99_999)

    repositorio = PostgresLedgerRepository(como_pool(connection))

    assert (await repositorio.balance_of(cuenta)).booked_minor == 5_000


async def test_una_cuenta_que_no_existe_se_distingue_de_una_a_cero(
    connection: asyncpg.Connection,
) -> None:
    repositorio = PostgresLedgerRepository(como_pool(connection))

    with pytest.raises(AccountNotFoundError):
        await repositorio.balance_of(uuid.uuid4())
