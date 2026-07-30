"""La barrera del §8.6: lo que el rol de consulta NO puede hacer.

Estas comprobaciones no van sobre el código de la aplicación, van sobre los permisos
de PostgreSQL. Si el compilador de consultas tuviera un agujero, esto es lo que
seguiría en pie.
"""

from collections.abc import AsyncIterator

import asyncpg
import pytest

from finguard_banking.bootstrap.config import get_settings

pytestmark = pytest.mark.integration

TABLAS = ["ledger_entries", "ledger_transactions", "accounts", "customers", "balance_projections"]


@pytest.fixture
async def lector() -> AsyncIterator[asyncpg.Connection]:
    conn: asyncpg.Connection = await asyncpg.connect(get_settings().query_database_url)
    try:
        yield conn
    finally:
        await conn.close()


@pytest.fixture
async def propietario() -> AsyncIterator[asyncpg.Connection]:
    conn: asyncpg.Connection = await asyncpg.connect(get_settings().database_url)
    try:
        yield conn
    finally:
        await conn.close()


async def test_puede_leer_las_vistas_autorizadas(lector: asyncpg.Connection) -> None:
    await lector.fetchval("SELECT COUNT(*) FROM assistant_read.transactions_v1")
    await lector.fetchval("SELECT COUNT(*) FROM assistant_read.account_balances_v1")


@pytest.mark.parametrize("tabla", TABLAS)
async def test_las_tablas_existen_pero_el_lector_no_las_ve(
    lector: asyncpg.Connection, propietario: asyncpg.Connection, tabla: str
) -> None:
    # Para el propietario la tabla está ahí...
    assert await propietario.fetchval(
        "SELECT COUNT(*) FROM pg_tables WHERE schemaname = 'public' AND tablename = $1", tabla
    )

    # ...y para el lector no existe siquiera. Sin USAGE en el esquema public no puede
    # ni confirmar qué tablas hay: es más fuerte que un "permiso denegado".
    with pytest.raises(asyncpg.UndefinedTableError):
        await lector.fetchval(f"SELECT COUNT(*) FROM {tabla}")  # noqa: S608


async def test_no_puede_escribir_ni_en_lo_que_ve(lector: asyncpg.Connection) -> None:
    # La vista es lo único que alcanza, y su sesión es de solo lectura.
    with pytest.raises(
        (
            asyncpg.ReadOnlySQLTransactionError,
            asyncpg.InsufficientPrivilegeError,
            asyncpg.ErrorInAssignmentError,
        )
    ):
        await lector.execute("CREATE TEMP TABLE intento (id int)")


async def test_su_sesion_es_de_solo_lectura(lector: asyncpg.Connection) -> None:
    assert await lector.fetchval("SHOW default_transaction_read_only") == "on"


async def test_tiene_un_limite_de_tiempo_por_sentencia(lector: asyncpg.Connection) -> None:
    # Sin esto, una consulta mal planteada podría ocupar la base indefinidamente (§8.6).
    assert await lector.fetchval("SHOW statement_timeout") == "3s"
