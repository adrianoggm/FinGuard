"""La consulta financiera contra PostgreSQL de verdad, con datos propios y deshechos."""

import uuid
from datetime import UTC, date, datetime

import asyncpg
import pytest
from conftest import como_pool, crear_cuenta, registrar

from finguard_banking.adapters.outbound.postgres.financial_query_repository import (
    PostgresFinancialQueryRepository,
)
from finguard_banking.domain.errors import AccountNotFoundError
from finguard_banking.domain.financial_query import (
    DateRange,
    Direction,
    FinancialQuery,
    GroupBy,
)

pytestmark = pytest.mark.integration

JULIO = DateRange(desde=date(2026, 7, 1), hasta=date(2026, 7, 31))


def en(dia: int, hora: int = 12) -> datetime:
    return datetime(2026, 7, dia, hora, tzinfo=UTC)


def plan(cuenta: uuid.UUID, **kwargs: object) -> FinancialQuery:
    return FinancialQuery(account_id=cuenta, date_range=JULIO, **kwargs)  # type: ignore[arg-type]


def repositorio(connection: asyncpg.Connection) -> PostgresFinancialQueryRepository:
    return PostgresFinancialQueryRepository(como_pool(connection), "Europe/Madrid")


async def test_suma_solo_el_gasto_de_las_categorias_pedidas(
    connection: asyncpg.Connection,
) -> None:
    cuenta = await crear_cuenta(connection)
    banco = await crear_cuenta(connection)
    await registrar(connection, cuenta, banco, -4_500, categoria="TRANSPORT_FUEL", ocurrido=en(3))
    await registrar(connection, cuenta, banco, -3_200, categoria="TRANSPORT_FUEL", ocurrido=en(17))
    await registrar(connection, cuenta, banco, -9_000, categoria="GROCERIES", ocurrido=en(18))

    resultado = await repositorio(connection).run(
        plan(cuenta, direction=Direction.EXPENSE, categories=("TRANSPORT_FUEL",))
    )

    # El gasto se devuelve en positivo: "he gastado 77,00", no "-77,00".
    assert resultado.total_minor == 7_700
    assert resultado.movement_count == 2
    assert resultado.currency == "EUR"


async def test_lo_que_esta_fuera_del_rango_no_cuenta(connection: asyncpg.Connection) -> None:
    cuenta = await crear_cuenta(connection)
    banco = await crear_cuenta(connection)
    await registrar(connection, cuenta, banco, -5_000, categoria="GROCERIES", ocurrido=en(15))
    await registrar(
        connection,
        cuenta,
        banco,
        -5_000,
        categoria="GROCERIES",
        ocurrido=datetime(2026, 6, 30, 12, tzinfo=UTC),
    )

    resultado = await repositorio(connection).run(plan(cuenta, direction=Direction.EXPENSE))

    assert resultado.total_minor == 5_000


async def test_lo_no_contabilizado_no_cuenta(connection: asyncpg.Connection) -> None:
    cuenta = await crear_cuenta(connection)
    banco = await crear_cuenta(connection)
    await registrar(connection, cuenta, banco, -5_000, categoria="GROCERIES", ocurrido=en(10))
    await registrar(
        connection, cuenta, banco, -70_000, categoria="GROCERIES", ocurrido=en(11), status="PENDING"
    )

    resultado = await repositorio(connection).run(plan(cuenta, direction=Direction.EXPENSE))

    assert resultado.total_minor == 5_000


async def test_agrupa_por_semana(connection: asyncpg.Connection) -> None:
    cuenta = await crear_cuenta(connection)
    banco = await crear_cuenta(connection)
    await registrar(connection, cuenta, banco, -3_420, categoria="TRANSPORT_FUEL", ocurrido=en(7))
    await registrar(connection, cuenta, banco, -6_200, categoria="TRANSPORT_FUEL", ocurrido=en(14))
    await registrar(connection, cuenta, banco, -3_123, categoria="TRANSPORT_FUEL", ocurrido=en(21))

    resultado = await repositorio(connection).run(
        plan(cuenta, direction=Direction.EXPENSE, group_by=GroupBy.WEEK)
    )

    assert [grupo.total_minor for grupo in resultado.groups] == [3_420, 6_200, 3_123]
    assert resultado.total_minor == 12_743


async def test_agrupa_por_categoria(connection: asyncpg.Connection) -> None:
    cuenta = await crear_cuenta(connection)
    banco = await crear_cuenta(connection)
    await registrar(connection, cuenta, banco, -4_000, categoria="GROCERIES", ocurrido=en(5))
    await registrar(connection, cuenta, banco, -1_000, categoria="GROCERIES", ocurrido=en(6))
    await registrar(connection, cuenta, banco, -7_000, categoria="RESTAURANTS", ocurrido=en(8))

    resultado = await repositorio(connection).run(
        plan(cuenta, direction=Direction.EXPENSE, group_by=GroupBy.CATEGORY)
    )

    assert {(g.key, g.total_minor) for g in resultado.groups} == {
        ("GROCERIES", 5_000),
        ("RESTAURANTS", 7_000),
    }


async def test_los_ingresos_se_separan_del_gasto(connection: asyncpg.Connection) -> None:
    cuenta = await crear_cuenta(connection)
    banco = await crear_cuenta(connection)
    await registrar(connection, cuenta, banco, 245_000, categoria="SALARY", ocurrido=en(25))
    await registrar(connection, cuenta, banco, -9_000, categoria="GROCERIES", ocurrido=en(26))

    ingresos = await repositorio(connection).run(plan(cuenta, direction=Direction.INCOME))
    gastos = await repositorio(connection).run(plan(cuenta, direction=Direction.EXPENSE))
    todo = await repositorio(connection).run(plan(cuenta, direction=Direction.ALL))

    assert ingresos.total_minor == 245_000
    assert gastos.total_minor == 9_000
    # Con los dos sentidos mezclados el total va con signo: es el neto del periodo.
    assert todo.total_minor == 236_000


async def test_devuelve_los_movimientos_que_sustentan_el_calculo(
    connection: asyncpg.Connection,
) -> None:
    cuenta = await crear_cuenta(connection)
    banco = await crear_cuenta(connection)
    await registrar(
        connection,
        cuenta,
        banco,
        -4_500,
        categoria="TRANSPORT_FUEL",
        comercio="Repsol",
        descripcion="Compra en Repsol",
        ocurrido=en(3),
    )

    resultado = await repositorio(connection).run(
        plan(cuenta, direction=Direction.EXPENSE, categories=("TRANSPORT_FUEL",))
    )

    assert len(resultado.supporting_movements) == 1
    movimiento = resultado.supporting_movements[0]
    assert movimiento.merchant_name == "Repsol"
    assert movimiento.category == "TRANSPORT_FUEL"
    assert movimiento.amount_minor == 4_500
    assert movimiento.direction == "EXPENSE"
    assert movimiento.description == "Compra en Repsol"


async def test_se_puede_pedir_el_calculo_sin_justificantes(
    connection: asyncpg.Connection,
) -> None:
    cuenta = await crear_cuenta(connection)
    banco = await crear_cuenta(connection)
    await registrar(connection, cuenta, banco, -4_500, categoria="GROCERIES", ocurrido=en(3))

    resultado = await repositorio(connection).run(
        plan(cuenta, direction=Direction.EXPENSE, supporting_limit=0)
    )

    assert resultado.total_minor == 4_500
    assert resultado.supporting_movements == []


async def test_la_traza_solo_viaja_si_se_pide(connection: asyncpg.Connection) -> None:
    cuenta = await crear_cuenta(connection)

    sin_traza = await repositorio(connection).run(plan(cuenta))
    con_traza = await repositorio(connection).run(plan(cuenta, explain=True))

    assert sin_traza.trace is None
    assert con_traza.trace is not None
    assert "SELECT" in con_traza.trace.sql
    assert con_traza.trace.elapsed_ms >= 0


async def test_una_cuenta_que_no_existe_no_devuelve_cero(connection: asyncpg.Connection) -> None:
    with pytest.raises(AccountNotFoundError):
        await repositorio(connection).run(plan(uuid.uuid4()))
