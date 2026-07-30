"""Ningún SQL del camino de lectura puede nombrar una tabla.

Este test existe porque el fallo ya ocurrió: el repositorio de consultas leía
`accounts` para sacar la moneda, y solo se descubrió al ejecutarlo con el rol
restringido. Los tests de integración usan la conexión del propietario, así que no
lo habrían visto nunca.
"""

import re

from finguard_banking.adapters.outbound.postgres import (
    financial_query_repository,
    ledger_repository,
    query_compiler,
)

TABLAS_PROHIBIDAS = (
    "ledger_entries",
    "ledger_transactions",
    "accounts",
    "customers",
    "balance_projections",
)

MODULOS = (financial_query_repository, ledger_repository, query_compiler)


def sentencias() -> dict[str, str]:
    """Todas las constantes de SQL de los módulos del camino de lectura."""
    encontradas = {
        f"{modulo.__name__}.{nombre}": valor
        for modulo in MODULOS
        for nombre, valor in vars(modulo).items()
        if nombre.endswith("_SQL") and isinstance(valor, str)
    }
    assert encontradas, "no se ha encontrado ninguna constante _SQL: ¿han cambiado de nombre?"
    return encontradas


def test_ninguna_sentencia_nombra_una_tabla() -> None:
    for donde, sql in sentencias().items():
        for tabla in TABLAS_PROHIBIDAS:
            # \b para no confundir `accounts` con `account_balances_v1`.
            assert not re.search(rf"\b{tabla}\b", sql), f"{donde} nombra la tabla {tabla}"


def test_las_sentencias_leen_del_esquema_autorizado() -> None:
    for donde, sql in sentencias().items():
        assert "assistant_read." in sql, f"{donde} no lee de assistant_read"
