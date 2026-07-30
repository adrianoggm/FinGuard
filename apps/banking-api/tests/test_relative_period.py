"""Aritmética de calendario: donde se cuelan los errores de un mes sin que nadie los vea."""

from datetime import date

import pytest
from pydantic import ValidationError

from finguard_banking.domain.relative_period import Period, RelativePeriod, resolve

# Un miércoles, para que "esta semana" y "la semana pasada" no coincidan con el mes.
MIERCOLES = date(2026, 7, 15)


def periodo(period: Period, **kwargs: object) -> RelativePeriod:
    return RelativePeriod(period=period, **kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("period", "extra", "referencia", "esperado"),
    [
        (Period.TODAY, {}, MIERCOLES, ("2026-07-15", "2026-07-15")),
        (Period.YESTERDAY, {}, MIERCOLES, ("2026-07-14", "2026-07-14")),
        # Del lunes a hoy: nadie pregunta por sus gastos de mañana.
        (Period.THIS_WEEK, {}, MIERCOLES, ("2026-07-13", "2026-07-15")),
        (Period.LAST_WEEK, {}, MIERCOLES, ("2026-07-06", "2026-07-12")),
        (Period.THIS_MONTH, {}, MIERCOLES, ("2026-07-01", "2026-07-15")),
        (Period.LAST_MONTH, {}, MIERCOLES, ("2026-06-01", "2026-06-30")),
        (Period.THIS_YEAR, {}, MIERCOLES, ("2026-01-01", "2026-07-15")),
        # Incluye hoy: son hoy y los seis anteriores.
        (Period.LAST_N_DAYS, {"n": 7}, MIERCOLES, ("2026-07-09", "2026-07-15")),
        (Period.LAST_N_DAYS, {"n": 1}, MIERCOLES, ("2026-07-15", "2026-07-15")),
        # Incluye el mes en curso desde su día 1: julio, junio y mayo.
        (Period.LAST_N_MONTHS, {"n": 3}, MIERCOLES, ("2026-05-01", "2026-07-15")),
        # Un lunes: "esta semana" es solo ese día.
        (Period.THIS_WEEK, {}, date(2026, 7, 13), ("2026-07-13", "2026-07-13")),
        # Día 1: "este mes" es solo ese día, y el mes anterior está completo.
        (Period.THIS_MONTH, {}, date(2026, 7, 1), ("2026-07-01", "2026-07-01")),
        (Period.LAST_MONTH, {}, date(2026, 7, 1), ("2026-06-01", "2026-06-30")),
        # 31 de marzo: el mes anterior tiene 28 días, no 31.
        (Period.LAST_MONTH, {}, date(2026, 3, 31), ("2026-02-01", "2026-02-28")),
        # Año bisiesto.
        (Period.LAST_MONTH, {}, date(2024, 3, 15), ("2024-02-01", "2024-02-29")),
        # Cruce de año hacia atrás.
        (Period.LAST_MONTH, {}, date(2026, 1, 10), ("2025-12-01", "2025-12-31")),
        (Period.LAST_N_MONTHS, {"n": 3}, date(2026, 1, 10), ("2025-11-01", "2026-01-10")),
        (Period.LAST_WEEK, {}, date(2026, 1, 1), ("2025-12-22", "2025-12-28")),
        (Period.YESTERDAY, {}, date(2026, 1, 1), ("2025-12-31", "2025-12-31")),
    ],
)
def test_resuelve_el_rango(
    period: Period, extra: dict[str, int], referencia: date, esperado: tuple[str, str]
) -> None:
    rango = resolve(periodo(period, reference=referencia, **extra), hoy=date(2000, 1, 1))

    assert (rango.desde.isoformat(), rango.hasta.isoformat()) == esperado


def test_sin_referencia_usa_el_hoy_que_le_dan() -> None:
    # La función es pura: el "hoy" entra como argumento, no lo consulta ella.
    rango = resolve(periodo(Period.TODAY), hoy=date(2026, 2, 3))

    assert rango.desde == date(2026, 2, 3)


def test_un_periodo_con_cantidad_la_exige() -> None:
    with pytest.raises(ValidationError, match="necesita n"):
        periodo(Period.LAST_N_DAYS)


def test_un_periodo_sin_cantidad_no_la_admite() -> None:
    # LAST_MONTH con n=3 sería ambiguo: ¿el mes pasado o los tres últimos?
    with pytest.raises(ValidationError, match="no admite n"):
        periodo(Period.LAST_MONTH, n=3)


@pytest.mark.parametrize("meses", [0, 25])
def test_los_meses_tienen_limite(meses: int) -> None:
    with pytest.raises(ValidationError):
        periodo(Period.LAST_N_MONTHS, n=meses)


def test_un_periodo_inventado_se_rechaza() -> None:
    with pytest.raises(ValidationError):
        RelativePeriod.model_validate({"period": "DESDE_SIEMPRE"})


def test_el_rango_resuelto_nunca_esta_al_reves() -> None:
    # DateRange lo valida, así que cualquier resolución que se invierta explota aquí.
    for period in Period:
        extra = {"n": 3} if period in (Period.LAST_N_DAYS, Period.LAST_N_MONTHS) else {}
        for dia in (date(2026, 1, 1), date(2026, 2, 28), date(2024, 2, 29), date(2026, 12, 31)):
            rango = resolve(periodo(period, reference=dia, **extra), hoy=dia)
            assert rango.desde <= rango.hasta
