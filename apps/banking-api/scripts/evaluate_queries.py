"""Mide la exactitud de ejecución del motor de consultas (vision.md §17).

Ejecuta cada caso de evaluation/queries.yaml contra la base cargada con el dataset y
compara con las cifras que calculó evaluation/expected.sql. Sale con código distinto
de cero si alguna falla, así que sirve tanto de informe como de comprobación en CI.

    just evaluate          # necesita `just seed` antes

Las consultas van por el pool de solo lectura, el mismo rol que usa el servicio: si
una consulta necesitara privilegios de más, aquí también fallaría.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import asyncpg
import yaml

from finguard_banking.adapters.inbound.http.routes import FinancialQueryRequest
from finguard_banking.adapters.outbound.postgres.financial_query_repository import (
    PostgresFinancialQueryRepository,
)
from finguard_banking.bootstrap.config import get_settings
from finguard_banking.domain.financial_query_result import FinancialQueryResult

POR_DEFECTO = Path(__file__).resolve().parent.parent / "evaluation" / "queries.yaml"


@dataclass
class Resultado:
    caso: str
    correcto: bool
    detalle: str


def _comparar(caso: dict[str, Any], obtenido: FinancialQueryResult) -> Resultado:
    esperado = caso["expected"]
    fallos: list[str] = []

    if obtenido.total_minor != esperado["totalMinor"]:
        fallos.append(f"total {obtenido.total_minor} != {esperado['totalMinor']}")
    if obtenido.movement_count != esperado["movementCount"]:
        fallos.append(f"movimientos {obtenido.movement_count} != {esperado['movementCount']}")

    if "groups" in esperado:
        obtenidos = [
            {"key": g.key, "totalMinor": g.total_minor, "movementCount": g.movement_count}
            for g in obtenido.groups
        ]
        if obtenidos != esperado["groups"]:
            fallos.append(f"grupos {obtenidos} != {esperado['groups']}")

    detalle = "; ".join(fallos) if fallos else f"{obtenido.total_minor / 100:.2f} EUR"
    return Resultado(caso=caso["id"], correcto=not fallos, detalle=detalle)


async def ejecutar(casos: list[dict[str, Any]]) -> int:
    ajustes = get_settings()

    propietario = await asyncpg.connect(ajustes.database_url)
    lector: asyncpg.Pool[Any] = await asyncpg.create_pool(ajustes.query_database_url)
    try:
        cuentas = {
            fila["account_reference"]: fila["id"]
            for fila in await propietario.fetch("SELECT id, account_reference FROM accounts")
        }
        if not cuentas:
            print("La base está vacía: ejecuta `just seed` antes.", file=sys.stderr)
            return 2

        repositorio = PostgresFinancialQueryRepository(lector, ajustes.reporting_timezone)
        resultados: list[Resultado] = []

        for caso in casos:
            plan = dict(caso["plan"])
            # Un periodo relativo sin referencia daría un resultado distinto cada día.
            if "period" in plan and "reference" not in plan["period"]:
                print(f"{caso['id']}: el periodo relativo no fija reference", file=sys.stderr)
                return 2

            plan["accountId"] = str(cuentas[caso["account"]])
            peticion = FinancialQueryRequest.model_validate(plan)
            consulta = peticion.a_consulta(date.today())
            resultados.append(_comparar(caso, await repositorio.run(consulta)))
    finally:
        await lector.close()
        await propietario.close()

    aciertos = sum(1 for r in resultados if r.correcto)
    total = len(resultados)
    ancho = max(len(r.caso) for r in resultados)
    for r in resultados:
        marca = "ok  " if r.correcto else "FALLA"
        print(f"  {marca} {r.caso:<{ancho}}  {r.detalle}")

    print(f"\nexactitud de ejecución: {aciertos}/{total} ({aciertos / total:.1%})")
    return 0 if aciertos == total else 1


if __name__ == "__main__":
    # Admite otro fichero de casos como argumento, que es como se comprueba que este
    # comprobador puede fallar de verdad.
    ruta = Path(sys.argv[1]) if len(sys.argv) > 1 else POR_DEFECTO
    raise SystemExit(asyncio.run(ejecutar(yaml.safe_load(ruta.read_text(encoding="utf-8")))))
