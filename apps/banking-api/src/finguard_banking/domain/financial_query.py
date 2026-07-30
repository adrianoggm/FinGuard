from datetime import date
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.alias_generators import to_camel

MAX_MOVIMIENTOS_JUSTIFICANTES = 100


class Metric(StrEnum):
    SUM = "SUM"
    COUNT = "COUNT"


class Direction(StrEnum):
    """Qué lado del movimiento interesa, visto por el titular."""

    EXPENSE = "EXPENSE"
    INCOME = "INCOME"
    ALL = "ALL"


class GroupBy(StrEnum):
    NONE = "NONE"
    DAY = "DAY"
    WEEK = "WEEK"
    MONTH = "MONTH"
    CATEGORY = "CATEGORY"
    MERCHANT = "MERCHANT"


class DateRange(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, frozen=True)

    desde: date = Field(alias="from")
    hasta: date = Field(alias="to")

    @model_validator(mode="after")
    def _ordenado(self) -> Self:
        if self.desde > self.hasta:
            raise ValueError("dateRange.from no puede ser posterior a dateRange.to")
        return self


class FinancialQuery(BaseModel):
    """Plan de consulta validado.

    Es la representación intermedia de vision.md §9.2: el LLM propone esto, nunca
    SQL. Todo lo que llega aquí es dato no confiable, así que el modelo es estricto
    a propósito: campos cerrados, patrones y límites.
    """

    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, frozen=True, extra="forbid"
    )

    account_id: UUID
    metric: Metric = Metric.SUM
    direction: Direction = Direction.ALL
    categories: tuple[str, ...] = ()
    date_range: DateRange
    group_by: GroupBy = GroupBy.NONE
    # Cuántos movimientos devolver como justificación del cálculo.
    supporting_limit: int = Field(default=20, ge=0, le=MAX_MOVIMIENTOS_JUSTIFICANTES)
    # La traza (SQL compilado y parámetros) se pide expresamente: no viaja por defecto.
    explain: bool = False

    @field_validator("categories")
    @classmethod
    def _categorias_validas(cls, valor: tuple[str, ...]) -> tuple[str, ...]:
        if len(valor) > 20:
            raise ValueError("demasiadas categorías")
        for categoria in valor:
            if not categoria.isascii() or not categoria.replace("_", "").isalnum():
                raise ValueError(f"categoría no válida: {categoria!r}")
            if len(categoria) > 40:
                raise ValueError(f"categoría demasiado larga: {categoria!r}")
        return valor
