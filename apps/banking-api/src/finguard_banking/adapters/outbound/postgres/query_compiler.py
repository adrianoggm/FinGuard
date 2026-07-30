"""Compila un plan de consulta validado a SQL parametrizado.

Regla de vision.md §9.3: no se ejecuta SQL producido por el LLM. Aquí la estructura
del SQL sale exclusivamente de enums —conjuntos cerrados definidos en el código— y
todo valor que venga de fuera viaja como parámetro. Ni un dato del usuario se
concatena en la cadena.

Se lee la vista `assistant_read.transactions_v1`, nunca las tablas. Qué cuenta como
contabilizado y qué signo tiene cada asiento son decisiones de la vista, no de aquí.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from finguard_banking.domain.financial_query import Direction, FinancialQuery, GroupBy

VISTA = "assistant_read.transactions_v1"

# Tope de filas devueltas por consulta (vision.md §8.6).
MAX_GRUPOS = 500


@dataclass(frozen=True)
class CompiledQuery:
    sql: str
    parameters: list[Any]


class _Parametros:
    """Numera los parámetros y guarda sus valores en el mismo orden."""

    def __init__(self) -> None:
        self._valores: list[Any] = []

    def de(self, valor: Any) -> str:
        self._valores.append(valor)
        return f"${len(self._valores)}"

    @property
    def valores(self) -> list[Any]:
        return list(self._valores)


def _expresion_de_grupo(group_by: GroupBy, tz: str) -> str:
    match group_by:
        case GroupBy.NONE:
            return "'total'"
        case GroupBy.DAY:
            return f"to_char(t.occurred_at AT TIME ZONE {tz}, 'YYYY-MM-DD')"
        case GroupBy.WEEK:
            return f"to_char(date_trunc('week', t.occurred_at AT TIME ZONE {tz}), 'YYYY-MM-DD')"
        case GroupBy.MONTH:
            return f"to_char(t.occurred_at AT TIME ZONE {tz}, 'YYYY-MM')"
        case GroupBy.CATEGORY:
            return "COALESCE(t.category, 'SIN_CATEGORIA')"
        case GroupBy.MERCHANT:
            return "COALESCE(t.merchant_name, 'SIN_COMERCIO')"


def _expresion_de_importe(direction: Direction) -> str:
    if direction is Direction.ALL:
        # Con los dos sentidos mezclados el total solo tiene sentido con signo.
        return "t.signed_amount_minor"
    # En un solo sentido el importe es positivo: "he gastado 34,20", no "-34,20".
    return "t.amount_minor"


def _condiciones(consulta: FinancialQuery, parametros: _Parametros, tz: str) -> list[str]:
    cuenta = parametros.de(consulta.account_id)
    desde = parametros.de(consulta.date_range.desde)
    hasta = parametros.de(consulta.date_range.hasta)

    condiciones = [
        f"t.account_id = {cuenta}",
        # El rango incluye el día final completo, en la zona horaria del informe.
        f"t.occurred_at >= (({desde}::date)::timestamp AT TIME ZONE {tz})",
        f"t.occurred_at < (({hasta}::date + 1)::timestamp AT TIME ZONE {tz})",
    ]

    if consulta.direction is not Direction.ALL:
        condiciones.append(f"t.direction = {parametros.de(consulta.direction.value)}")

    if consulta.categories:
        condiciones.append(f"t.category = ANY({parametros.de(list(consulta.categories))})")

    return condiciones


def compilar_agregado(consulta: FinancialQuery, zona_horaria: str) -> CompiledQuery:
    parametros = _Parametros()
    tz = parametros.de(zona_horaria)
    condiciones = _condiciones(consulta, parametros, tz)

    sql = f"""
        SELECT
            {_expresion_de_grupo(consulta.group_by, tz)} AS clave,
            COALESCE(SUM({_expresion_de_importe(consulta.direction)}), 0) AS total_minor,
            COUNT(*) AS movement_count
        FROM {VISTA} t
        WHERE {" AND ".join(condiciones)}
        GROUP BY 1
        ORDER BY 1
        LIMIT {MAX_GRUPOS}
    """
    return CompiledQuery(sql=sql, parameters=parametros.valores)


def compilar_movimientos(consulta: FinancialQuery, zona_horaria: str) -> CompiledQuery:
    parametros = _Parametros()
    tz = parametros.de(zona_horaria)
    condiciones = _condiciones(consulta, parametros, tz)
    limite = parametros.de(consulta.supporting_limit)

    sql = f"""
        SELECT
            t.transaction_id,
            t.occurred_at,
            COALESCE(t.description, '') AS description,
            t.merchant_name,
            t.category,
            {_expresion_de_importe(consulta.direction)} AS amount_minor,
            t.direction
        FROM {VISTA} t
        WHERE {" AND ".join(condiciones)}
        ORDER BY t.occurred_at DESC, t.transaction_id
        LIMIT {limite}
    """
    return CompiledQuery(sql=sql, parameters=parametros.valores)
