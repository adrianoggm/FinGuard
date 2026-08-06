"""No SQL on the read path may name a table.

This test exists because the mistake already happened: the query repository read
`accounts` to get the currency, and it only surfaced when running with the restricted
role. The integration tests use the owner connection, so they would never have caught
it.
"""

import re

from finguard_banking.infrastructure.persistence import (
    financial_query_repository,
    ledger_repository,
    query_compiler,
)

FORBIDDEN_TABLES = (
    "ledger_entries",
    "ledger_transactions",
    "accounts",
    "customers",
    "balance_projections",
)

MODULES = (financial_query_repository, ledger_repository, query_compiler)


def statements() -> dict[str, str]:
    """Every SQL constant in the modules on the read path."""
    found = {
        f"{module.__name__}.{name}": value
        for module in MODULES
        for name, value in vars(module).items()
        if name.endswith("_SQL") and isinstance(value, str)
    }
    assert found, "no _SQL constant found: have they been renamed?"
    return found


def test_no_statement_names_a_table() -> None:
    for where, sql in statements().items():
        for table in FORBIDDEN_TABLES:
            # \b so that `accounts` is not confused with `account_balances_v1`.
            assert not re.search(rf"\b{table}\b", sql), f"{where} names the table {table}"


def test_statements_read_from_the_authorised_schema() -> None:
    for where, sql in statements().items():
        assert "assistant_read." in sql, f"{where} does not read from assistant_read"
