"""Las invariantes del libro mayor las garantiza PostgreSQL, no el código que escribe.

Cada test comprueba que la base de datos rechaza lo que no debe entrar, sin importar
qué servicio o qué consola lo intente.
"""

import asyncpg
import pytest
from conftest import anotar, crear_cuenta, crear_transaccion, forzar_comprobacion

pytestmark = pytest.mark.integration


async def test_una_transaccion_equilibrada_es_valida(connection: asyncpg.Connection) -> None:
    origen = await crear_cuenta(connection)
    destino = await crear_cuenta(connection)
    transaccion = await crear_transaccion(connection)

    await anotar(connection, transaccion, origen, "DEBIT", 2500)
    await anotar(connection, transaccion, destino, "CREDIT", 2500)

    await forzar_comprobacion(connection)


async def test_una_transaccion_descuadrada_se_rechaza(connection: asyncpg.Connection) -> None:
    origen = await crear_cuenta(connection)
    destino = await crear_cuenta(connection)
    transaccion = await crear_transaccion(connection)

    await anotar(connection, transaccion, origen, "DEBIT", 2500)
    await anotar(connection, transaccion, destino, "CREDIT", 2400)

    with pytest.raises(asyncpg.RaiseError, match="desequilibrada"):
        await forzar_comprobacion(connection)


async def test_un_asiento_suelto_no_es_una_transaccion(connection: asyncpg.Connection) -> None:
    cuenta = await crear_cuenta(connection)
    transaccion = await crear_transaccion(connection)

    await anotar(connection, transaccion, cuenta, "DEBIT", 2500)

    with pytest.raises(asyncpg.RaiseError, match="desequilibrada"):
        await forzar_comprobacion(connection)


async def test_no_se_cuadra_mezclando_monedas(connection: asyncpg.Connection) -> None:
    en_euros = await crear_cuenta(connection, currency="EUR")
    en_dolares = await crear_cuenta(connection, currency="USD")
    transaccion = await crear_transaccion(connection)

    # Los importes son iguales, pero 2500 EUR al debe no compensan 2500 USD al haber.
    await anotar(connection, transaccion, en_euros, "DEBIT", 2500, currency="EUR")
    await anotar(connection, transaccion, en_dolares, "CREDIT", 2500, currency="USD")

    with pytest.raises(asyncpg.RaiseError, match="desequilibrada"):
        await forzar_comprobacion(connection)


async def test_un_asiento_no_puede_ir_en_otra_moneda_que_su_cuenta(
    connection: asyncpg.Connection,
) -> None:
    origen = await crear_cuenta(connection, currency="EUR")
    destino = await crear_cuenta(connection, currency="EUR")
    transaccion = await crear_transaccion(connection)

    # Cuadra en USD, pero las dos cuentas son en euros.
    await anotar(connection, transaccion, origen, "DEBIT", 2500, currency="USD")
    await anotar(connection, transaccion, destino, "CREDIT", 2500, currency="USD")

    with pytest.raises(asyncpg.RaiseError, match="moneda distinta"):
        await forzar_comprobacion(connection)


@pytest.mark.parametrize("importe", [0, -2500])
async def test_un_asiento_sin_importe_positivo_se_rechaza(
    connection: asyncpg.Connection, importe: int
) -> None:
    cuenta = await crear_cuenta(connection)
    transaccion = await crear_transaccion(connection)

    with pytest.raises(asyncpg.CheckViolationError):
        await anotar(connection, transaccion, cuenta, "DEBIT", importe)


async def test_un_tipo_de_asiento_inventado_se_rechaza(connection: asyncpg.Connection) -> None:
    cuenta = await crear_cuenta(connection)
    transaccion = await crear_transaccion(connection)

    with pytest.raises(asyncpg.CheckViolationError):
        await anotar(connection, transaccion, cuenta, "APUNTE", 2500)


async def test_un_asiento_no_se_puede_modificar(connection: asyncpg.Connection) -> None:
    origen = await crear_cuenta(connection)
    destino = await crear_cuenta(connection)
    transaccion = await crear_transaccion(connection)
    asiento = await anotar(connection, transaccion, origen, "DEBIT", 2500)
    await anotar(connection, transaccion, destino, "CREDIT", 2500)

    with pytest.raises(asyncpg.RaiseError, match="inmutable"):
        await connection.execute(
            "UPDATE ledger_entries SET amount_minor = 1 WHERE id = $1", asiento
        )


async def test_un_asiento_no_se_puede_borrar(connection: asyncpg.Connection) -> None:
    origen = await crear_cuenta(connection)
    destino = await crear_cuenta(connection)
    transaccion = await crear_transaccion(connection)
    asiento = await anotar(connection, transaccion, origen, "DEBIT", 2500)
    await anotar(connection, transaccion, destino, "CREDIT", 2500)

    with pytest.raises(asyncpg.RaiseError, match="inmutable"):
        await connection.execute("DELETE FROM ledger_entries WHERE id = $1", asiento)


async def test_una_transaccion_no_se_puede_borrar(connection: asyncpg.Connection) -> None:
    transaccion = await crear_transaccion(connection)

    with pytest.raises(asyncpg.RaiseError, match="inmutable"):
        await connection.execute("DELETE FROM ledger_transactions WHERE id = $1", transaccion)
