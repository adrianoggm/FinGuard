"""Lo que no puede pasar nunca: que un dato de fuera acabe dentro de la cadena SQL."""

import uuid
from datetime import date

import pytest
from pydantic import ValidationError

from finguard_banking.adapters.outbound.postgres.query_compiler import (
    compilar_agregado,
    compilar_movimientos,
)
from finguard_banking.domain.financial_query import (
    DateRange,
    Direction,
    FinancialQuery,
    GroupBy,
)

CUENTA = uuid.UUID("11111111-1111-1111-1111-111111111111")
RANGO = DateRange(desde=date(2026, 7, 1), hasta=date(2026, 7, 31))
INYECCION = "TRANSPORT_FUEL'; DROP TABLE ledger_entries; --"


def consulta(**kwargs: object) -> FinancialQuery:
    return FinancialQuery(account_id=CUENTA, date_range=RANGO, **kwargs)  # type: ignore[arg-type]


def test_el_dominio_rechaza_una_categoria_con_sql_dentro() -> None:
    with pytest.raises(ValidationError):
        consulta(categories=(INYECCION,))


def test_aunque_la_validacion_falle_la_categoria_viaja_como_parametro() -> None:
    # model_construct salta la validación: simula que alguien la esquiva. Ni así el
    # texto entra en el SQL.
    plan = FinancialQuery.model_construct(
        account_id=CUENTA,
        date_range=RANGO,
        categories=(INYECCION,),
        direction=Direction.EXPENSE,
        group_by=GroupBy.NONE,
        supporting_limit=20,
        explain=False,
    )

    compilada = compilar_agregado(plan, "Europe/Madrid")

    assert "DROP TABLE" not in compilada.sql
    assert [INYECCION] in compilada.parameters
    assert "= ANY($" in compilada.sql


def test_ningun_valor_de_entrada_aparece_en_la_cadena() -> None:
    compilada = compilar_agregado(
        consulta(categories=("GROCERIES",), direction=Direction.EXPENSE), "Europe/Madrid"
    )

    assert str(CUENTA) not in compilada.sql
    assert "GROCERIES" not in compilada.sql
    assert "2026-07-01" not in compilada.sql
    assert "Europe/Madrid" not in compilada.sql
    # Cuenta, zona, fechas, sentido y categorías: todo parametrizado.
    assert len(compilada.parameters) == 6


def test_el_sentido_del_movimiento_tambien_es_parametro() -> None:
    compilada = compilar_agregado(consulta(direction=Direction.EXPENSE), "Europe/Madrid")

    assert "'DEBIT'" not in compilada.sql
    assert "DEBIT" in compilada.parameters


@pytest.mark.parametrize(
    ("agrupacion", "fragmento"),
    [
        (GroupBy.NONE, "'total'"),
        (GroupBy.DAY, "'YYYY-MM-DD'"),
        (GroupBy.WEEK, "date_trunc('week'"),
        (GroupBy.MONTH, "'YYYY-MM'"),
        (GroupBy.CATEGORY, "'category'"),
        (GroupBy.MERCHANT, "'merchant_name'"),
    ],
)
def test_cada_agrupacion_compila_a_su_expresion(agrupacion: GroupBy, fragmento: str) -> None:
    compilada = compilar_agregado(consulta(group_by=agrupacion), "Europe/Madrid")

    assert fragmento in compilada.sql


def test_solo_se_consultan_movimientos_contabilizados() -> None:
    for compilada in (
        compilar_agregado(consulta(), "Europe/Madrid"),
        compilar_movimientos(consulta(), "Europe/Madrid"),
    ):
        assert "t.status = 'BOOKED'" in compilada.sql


def test_el_dia_final_del_rango_entra_completo() -> None:
    compilada = compilar_agregado(consulta(), "Europe/Madrid")

    # Comparar con `<= hasta` dejaría fuera todo lo posterior a medianoche.
    assert "::date + 1)::timestamp AT TIME ZONE" in compilada.sql


def test_un_rango_al_reves_no_es_una_consulta() -> None:
    with pytest.raises(ValidationError):
        DateRange(desde=date(2026, 7, 31), hasta=date(2026, 7, 1))


def test_un_campo_desconocido_se_rechaza() -> None:
    with pytest.raises(ValidationError):
        FinancialQuery.model_validate(
            {
                "accountId": str(CUENTA),
                "dateRange": {"from": "2026-07-01", "to": "2026-07-31"},
                "sql": "SELECT 1",
            }
        )
