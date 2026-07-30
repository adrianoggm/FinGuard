"""Resuelve expresiones temporales a un rango de fechas concreto.

El LLM nombra el periodo (`LAST_MONTH`), no lo calcula: aritmética de calendario en
un modelo generativo es exactamente donde aparecen los errores silenciosos de un mes.
Aquí es una función pura, así que el resultado no depende de cuándo se ejecute.
"""

from __future__ import annotations

from datetime import date, timedelta
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

from finguard_banking.domain.financial_query import DateRange

MAX_MESES = 24
MAX_DIAS = 366


class Period(StrEnum):
    TODAY = "TODAY"
    YESTERDAY = "YESTERDAY"
    THIS_WEEK = "THIS_WEEK"
    LAST_WEEK = "LAST_WEEK"
    THIS_MONTH = "THIS_MONTH"
    LAST_MONTH = "LAST_MONTH"
    THIS_YEAR = "THIS_YEAR"
    LAST_N_DAYS = "LAST_N_DAYS"
    LAST_N_MONTHS = "LAST_N_MONTHS"


CON_CANTIDAD = (Period.LAST_N_DAYS, Period.LAST_N_MONTHS)


class RelativePeriod(BaseModel):
    """Periodo relativo. `reference` fija qué día es "hoy": sin él no hay reproducibilidad."""

    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, frozen=True, extra="forbid"
    )

    period: Period
    n: int | None = Field(default=None, ge=1, le=MAX_DIAS)
    reference: date | None = None

    @model_validator(mode="after")
    def _cantidad_coherente(self) -> Self:
        necesita = self.period in CON_CANTIDAD
        if necesita and self.n is None:
            raise ValueError(f"{self.period} necesita n")
        if not necesita and self.n is not None:
            raise ValueError(f"{self.period} no admite n")
        if self.period is Period.LAST_N_MONTHS and self.n is not None and self.n > MAX_MESES:
            raise ValueError(f"como máximo {MAX_MESES} meses")
        return self


def _primero_de_mes(dia: date) -> date:
    return dia.replace(day=1)


def _meses_atras(dia: date, meses: int) -> date:
    """Primer día del mes que queda `meses` antes. Evita el 31 de febrero."""
    total = dia.year * 12 + (dia.month - 1) - meses
    return date(total // 12, total % 12 + 1, 1)


def resolve(periodo: RelativePeriod, hoy: date) -> DateRange:
    """Convierte el periodo en un rango cerrado, ambos extremos incluidos."""
    referencia = periodo.reference or hoy

    match periodo.period:
        case Period.TODAY:
            return DateRange(desde=referencia, hasta=referencia)

        case Period.YESTERDAY:
            ayer = referencia - timedelta(days=1)
            return DateRange(desde=ayer, hasta=ayer)

        case Period.THIS_WEEK:
            # La semana empieza el lunes y termina hoy: nadie pregunta por sus gastos
            # de mañana.
            return DateRange(
                desde=referencia - timedelta(days=referencia.weekday()), hasta=referencia
            )

        case Period.LAST_WEEK:
            lunes = referencia - timedelta(days=referencia.weekday() + 7)
            return DateRange(desde=lunes, hasta=lunes + timedelta(days=6))

        case Period.THIS_MONTH:
            return DateRange(desde=_primero_de_mes(referencia), hasta=referencia)

        case Period.LAST_MONTH:
            return DateRange(
                desde=_meses_atras(referencia, 1),
                hasta=_primero_de_mes(referencia) - timedelta(days=1),
            )

        case Period.THIS_YEAR:
            return DateRange(desde=date(referencia.year, 1, 1), hasta=referencia)

        case Period.LAST_N_DAYS:
            if periodo.n is None:
                raise ValueError("LAST_N_DAYS necesita n")
            # Incluye hoy: "los últimos 7 días" son hoy y los 6 anteriores.
            return DateRange(desde=referencia - timedelta(days=periodo.n - 1), hasta=referencia)

        case Period.LAST_N_MONTHS:
            if periodo.n is None:
                raise ValueError("LAST_N_MONTHS necesita n")
            # Incluye el mes en curso, desde su día 1.
            return DateRange(desde=_meses_atras(referencia, periodo.n - 1), hasta=referencia)
