"""Genera el dataset sintético del núcleo bancario como un fichero SQL versionable.

Es determinista: con la misma semilla y la misma fecha final produce el mismo
fichero byte a byte. Por eso el dataset se guarda en git en lugar de generarse en
cada máquina: los tests, el benchmark de la Fase 2 y la demo trabajan todos sobre
exactamente los mismos movimientos.

    python scripts/generate_seed.py                 # regenera seeds/0001_demo.sql
    python scripts/generate_seed.py --months 6      # un dataset más corto

Si regeneras sin cambiar argumentos, `git diff` tiene que salir vacío. Si sale algo,
el generador ha dejado de ser determinista.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

SEMILLA = 20260730
FECHA_FINAL = date(2026, 7, 31)
MESES = 18
LOTE = 500

MONEDA = "EUR"


@dataclass(frozen=True)
class Gasto:
    categoria: str
    comercios: tuple[str, ...]
    minimo: int
    maximo: int
    veces_al_mes: tuple[int, int]


# Los importes van en céntimos, como en el libro mayor.
GASTOS = (
    Gasto("GROCERIES", ("Mercadona", "Carrefour", "Lidl", "Dia", "Alcampo"), 1_200, 9_500, (6, 13)),
    Gasto("TRANSPORT_FUEL", ("Repsol", "Cepsa", "BP", "Shell"), 3_500, 7_800, (2, 6)),
    Gasto(
        "RESTAURANTS", ("Bar Manolo", "La Tagliatella", "Goiko", "Cafe Central"), 900, 6_200, (3, 9)
    ),
    Gasto("TRANSPORT_PUBLIC", ("Renfe", "EMT Madrid", "Cabify", "Uber"), 150, 4_500, (0, 7)),
    Gasto("HEALTH", ("Farmacia Gran Via", "Clinica Dental Sur"), 800, 5_500, (0, 3)),
    Gasto("LEISURE", ("Cines Yelmo", "Fnac", "Decathlon", "Steam"), 700, 8_900, (0, 4)),
)

SUSCRIPCIONES = (
    ("Netflix", "SUBSCRIPTIONS", 1_399),
    ("Spotify", "SUBSCRIPTIONS", 1_099),
    ("Gimnasio Altafit", "SUBSCRIPTIONS", 2_990),
)


@dataclass(frozen=True)
class Titular:
    nombre: str
    referencia: str
    nomina_minor: int
    alquiler_minor: int
    saldo_inicial_minor: int


TITULARES = (
    Titular("Laura Martin Ruiz", "ES-0001", 245_000, 92_000, 180_000),
    Titular("Diego Ferrer Gomez", "ES-0002", 189_000, 78_000, 95_000),
    Titular("Nuria Salas Ortega", "ES-0003", 312_000, 115_000, 260_000),
)

# Contrapartida de toda operación: la cuenta de compensación del banco. Sin ella no
# habría doble entrada y el trigger de cuadre rechazaría cada transacción.
COMPENSACION = "ES-COMPENSACION"


@dataclass(frozen=True)
class Movimiento:
    ocurrido: datetime
    tipo: str
    descripcion: str
    comercio: str
    categoria: str
    importe_minor: int  # positivo = entra dinero al titular
    cuenta: uuid.UUID
    status: str = "BOOKED"


def identificador(rng: random.Random) -> uuid.UUID:
    """UUID v4 reproducible: sale del generador con semilla, no de os.urandom."""
    return uuid.UUID(int=rng.getrandbits(128), version=4)


def texto(valor: str) -> str:
    return "'" + valor.replace("'", "''") + "'"


def instante(rng: random.Random, dia: date) -> datetime:
    return datetime.combine(dia, time(rng.randrange(7, 23), rng.randrange(60)), tzinfo=UTC)


def primeros_de_mes(final: date, meses: int) -> list[date]:
    primero = final.replace(day=1)
    resultado = []
    for _ in range(meses):
        resultado.append(primero)
        primero = (primero - timedelta(days=1)).replace(day=1)
    return sorted(resultado)


def dia_del_mes(rng: random.Random, mes: date, desde: int, hasta: int, final: date) -> date:
    siguiente = (mes + timedelta(days=31)).replace(day=1)
    ultimo = (siguiente - timedelta(days=1)).day
    dia = mes.replace(day=min(rng.randrange(desde, hasta + 1), ultimo))
    return min(dia, final)


def generar(meses: int, final: date, semilla: int) -> tuple[list[Movimiento], dict[str, uuid.UUID]]:
    rng = random.Random(semilla)
    cuentas: dict[str, uuid.UUID] = {COMPENSACION: identificador(rng)}
    movimientos: list[Movimiento] = []

    for titular in TITULARES:
        cuenta = identificador(rng)
        cuentas[titular.referencia] = cuenta
        arranque = primeros_de_mes(final, meses)[0]

        movimientos.append(
            Movimiento(
                ocurrido=instante(rng, arranque),
                tipo="ACCOUNT_OPENING",
                descripcion="Saldo de apertura",
                comercio="FinGuard",
                categoria="OPENING",
                importe_minor=titular.saldo_inicial_minor,
                cuenta=cuenta,
            )
        )

        for mes in primeros_de_mes(final, meses):
            movimientos.append(
                Movimiento(
                    ocurrido=instante(rng, dia_del_mes(rng, mes, 25, 28, final)),
                    tipo="SALARY",
                    descripcion="Nomina mensual",
                    comercio="Innovasur SL",
                    categoria="SALARY",
                    importe_minor=titular.nomina_minor + rng.randrange(-2_000, 8_000),
                    cuenta=cuenta,
                )
            )
            movimientos.append(
                Movimiento(
                    ocurrido=instante(rng, dia_del_mes(rng, mes, 1, 5, final)),
                    tipo="DIRECT_DEBIT",
                    descripcion="Alquiler",
                    comercio="Arrendamiento Centro",
                    categoria="HOUSING_RENT",
                    importe_minor=-titular.alquiler_minor,
                    cuenta=cuenta,
                )
            )
            for comercio, categoria, importe in SUSCRIPCIONES:
                movimientos.append(
                    Movimiento(
                        ocurrido=instante(rng, dia_del_mes(rng, mes, 2, 12, final)),
                        tipo="CARD_PAYMENT",
                        descripcion=f"Suscripcion {comercio}",
                        comercio=comercio,
                        categoria=categoria,
                        importe_minor=-importe,
                        cuenta=cuenta,
                    )
                )
            for gasto in GASTOS:
                for _ in range(rng.randrange(*gasto.veces_al_mes)):
                    # Un solo sorteo: la descripción y el merchant_name tienen que
                    # nombrar el mismo comercio, o el movimiento se contradice.
                    comercio = rng.choice(gasto.comercios)
                    movimientos.append(
                        Movimiento(
                            ocurrido=instante(rng, dia_del_mes(rng, mes, 1, 28, final)),
                            tipo="CARD_PAYMENT",
                            descripcion=f"Compra en {comercio}",
                            comercio=comercio,
                            categoria=gasto.categoria,
                            importe_minor=-rng.randrange(gasto.minimo, gasto.maximo),
                            cuenta=cuenta,
                        )
                    )
            for _ in range(rng.randrange(0, 5)):
                enviado = rng.random() < 0.6
                importe = rng.randrange(500, 9_000)
                movimientos.append(
                    Movimiento(
                        ocurrido=instante(rng, dia_del_mes(rng, mes, 1, 28, final)),
                        tipo="BIZUM",
                        descripcion="Bizum enviado" if enviado else "Bizum recibido",
                        comercio=rng.choice(("Marta", "Sergio", "Elena", "Javi", "Rocio")),
                        categoria="TRANSFER_BIZUM",
                        importe_minor=-importe if enviado else importe,
                        cuenta=cuenta,
                    )
                )

    # Un puñado de movimientos sin contabilizar, para que se pueda comprobar que el
    # saldo ignora lo que todavía no está BOOKED.
    for titular in TITULARES:
        movimientos.append(
            Movimiento(
                ocurrido=instante(rng, final),
                tipo="CARD_PAYMENT",
                descripcion="Compra pendiente de liquidar",
                comercio="Amazon",
                categoria="LEISURE",
                importe_minor=-rng.randrange(1_000, 12_000),
                cuenta=cuentas[titular.referencia],
                status="PENDING",
            )
        )

    movimientos.sort(key=lambda m: (m.ocurrido, str(m.cuenta)))
    return movimientos, cuentas


def a_sql(movimientos: list[Movimiento], cuentas: dict[str, uuid.UUID], semilla: int) -> str:
    rng = random.Random(semilla ^ 0xC0FFEE)
    lineas: list[str] = []

    lineas.append("BEGIN;")
    lineas.append("")
    lineas.append("-- El libro mayor es inmutable: no se puede borrar con DELETE.")
    lineas.append("TRUNCATE ledger_entries, ledger_transactions, balance_projections,")
    lineas.append("         accounts, customers;")
    lineas.append("")

    cliente_banco = identificador(rng)
    filas_clientes = [f"({texto(str(cliente_banco))}, {texto('FinGuard (compensacion)')})"]
    filas_cuentas = [
        f"({texto(str(cuentas[COMPENSACION]))}, {texto(str(cliente_banco))}, "
        f"{texto(COMPENSACION)}, {texto(MONEDA)}, {texto('ACTIVE')})"
    ]
    for titular in TITULARES:
        cliente = identificador(rng)
        filas_clientes.append(f"({texto(str(cliente))}, {texto(titular.nombre)})")
        filas_cuentas.append(
            f"({texto(str(cuentas[titular.referencia]))}, {texto(str(cliente))}, "
            f"{texto(titular.referencia)}, {texto(MONEDA)}, {texto('ACTIVE')})"
        )

    lineas.append("INSERT INTO customers (id, full_name) VALUES")
    lineas.append(",\n".join(f"    {fila}" for fila in filas_clientes) + ";")
    lineas.append("")
    lineas.append(
        "INSERT INTO accounts (id, customer_id, account_reference, currency, status) VALUES"
    )
    lineas.append(",\n".join(f"    {fila}" for fila in filas_cuentas) + ";")
    lineas.append("")

    compensacion = cuentas[COMPENSACION]
    filas_transacciones: list[str] = []
    filas_asientos: list[str] = []

    for numero, movimiento in enumerate(movimientos, start=1):
        transaccion = identificador(rng)
        metadatos = json.dumps(
            {"merchant_name": movimiento.comercio, "category": movimiento.categoria},
            ensure_ascii=True,
            sort_keys=True,
        )
        filas_transacciones.append(
            f"({texto(str(transaccion))}, {texto(movimiento.tipo)}, {texto(movimiento.status)}, "
            f"{texto(f'seed-{numero:06d}')}, {texto(movimiento.descripcion)}, "
            f"{texto(metadatos)}::jsonb, {texto(movimiento.ocurrido.isoformat())}::timestamptz)"
        )

        importe = abs(movimiento.importe_minor)
        entra = movimiento.importe_minor > 0
        # Doble entrada: lo que el titular recibe sale de la compensación, y al revés.
        for cuenta, tipo in (
            (movimiento.cuenta, "CREDIT" if entra else "DEBIT"),
            (compensacion, "DEBIT" if entra else "CREDIT"),
        ):
            filas_asientos.append(
                f"({texto(str(identificador(rng)))}, {texto(str(transaccion))}, "
                f"{texto(str(cuenta))}, {texto(tipo)}, {importe}, {texto(MONEDA)})"
            )

    lineas.extend(
        _lotes(
            "INSERT INTO ledger_transactions "
            "(id, transaction_type, status, reference, description, metadata, occurred_at) VALUES",
            filas_transacciones,
        )
    )
    lineas.extend(
        _lotes(
            "INSERT INTO ledger_entries "
            "(id, transaction_id, account_id, entry_type, amount_minor, currency) VALUES",
            filas_asientos,
        )
    )

    # El cuadre se comprueba aquí, al cerrar la transacción: el trigger es diferido.
    lineas.append("COMMIT;")
    lineas.append("")
    return "\n".join(lineas)


def _lotes(cabecera: str, filas: list[str]) -> list[str]:
    lineas: list[str] = []
    for inicio in range(0, len(filas), LOTE):
        lineas.append(cabecera)
        lineas.append(",\n".join(f"    {fila}" for fila in filas[inicio : inicio + LOTE]) + ";")
        lineas.append("")
    return lineas


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--months", type=int, default=MESES)
    parser.add_argument("--end-date", type=date.fromisoformat, default=FECHA_FINAL)
    parser.add_argument("--seed", type=int, default=SEMILLA)
    parser.add_argument("--out", type=Path, default=Path("seeds/0001_demo.sql"))
    argumentos = parser.parse_args()

    movimientos, cuentas = generar(argumentos.months, argumentos.end_date, argumentos.seed)
    cuerpo = a_sql(movimientos, cuentas, argumentos.seed)
    huella = hashlib.sha256(cuerpo.encode()).hexdigest()

    contabilizados = sum(1 for m in movimientos if m.status == "BOOKED")
    cabecera = "\n".join(
        (
            "-- Dataset sintetico del nucleo bancario de FinGuard. GENERADO, no editar a mano.",
            "--   scripts/generate_seed.py",
            "--",
            f"-- semilla        {argumentos.seed}",
            f"-- meses          {argumentos.months}, hasta {argumentos.end_date.isoformat()}",
            f"-- titulares      {len(TITULARES)} + cuenta de compensacion",
            f"-- transacciones  {len(movimientos)} ({contabilizados} BOOKED,"
            f" {len(movimientos) - contabilizados} PENDING)",
            f"-- asientos       {len(movimientos) * 2}",
            f"-- sha256         {huella}",
            "--",
            "-- CUIDADO: empieza con un TRUNCATE. Es un dataset de desarrollo.",
            "",
        )
    )

    argumentos.out.parent.mkdir(parents=True, exist_ok=True)
    argumentos.out.write_text(cabecera + cuerpo, encoding="utf-8", newline="\n")
    print(f"{argumentos.out}: {len(movimientos)} transacciones, {len(movimientos) * 2} asientos")


if __name__ == "__main__":
    main()
