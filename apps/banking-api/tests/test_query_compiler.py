"""What must never happen: a value from outside ending up inside the SQL string."""

import uuid
from datetime import date

import pytest
from pydantic import ValidationError

from finguard_banking.domain.enums import Direction, GroupBy
from finguard_banking.domain.financial_query import DateRange, FinancialQuery
from finguard_banking.infrastructure.persistence.query_compiler import (
    compile_aggregate,
    compile_movements,
)

ACCOUNT = uuid.UUID("11111111-1111-1111-1111-111111111111")
RANGE = DateRange(start=date(2026, 7, 1), end=date(2026, 7, 31))
INJECTION = "TRANSPORT_FUEL'; DROP TABLE ledger_entries; --"


def query(**kwargs: object) -> FinancialQuery:
    return FinancialQuery(account_id=ACCOUNT, date_range=RANGE, **kwargs)  # type: ignore[arg-type]


def test_the_domain_rejects_a_category_with_sql_inside() -> None:
    with pytest.raises(ValidationError):
        query(categories=(INJECTION,))


def test_even_bypassing_validation_the_category_travels_as_a_parameter() -> None:
    # model_construct skips validation: it simulates someone dodging it. Not even
    # then does the text make it into the SQL.
    plan = FinancialQuery.model_construct(
        account_id=ACCOUNT,
        date_range=RANGE,
        categories=(INJECTION,),
        direction=Direction.EXPENSE,
        group_by=GroupBy.NONE,
        supporting_limit=20,
        explain=False,
    )

    compiled = compile_aggregate(plan, "Europe/Madrid")

    assert "DROP TABLE" not in compiled.sql
    assert [INJECTION] in compiled.parameters
    assert "= ANY($" in compiled.sql


def test_no_input_value_appears_in_the_string() -> None:
    compiled = compile_aggregate(
        query(categories=("GROCERIES",), direction=Direction.EXPENSE), "Europe/Madrid"
    )

    assert str(ACCOUNT) not in compiled.sql
    assert "GROCERIES" not in compiled.sql
    assert "2026-07-01" not in compiled.sql
    assert "Europe/Madrid" not in compiled.sql
    # Account, time zone, dates, direction and categories: all parameterised.
    assert len(compiled.parameters) == 6


def test_the_direction_is_a_parameter_too() -> None:
    compiled = compile_aggregate(query(direction=Direction.EXPENSE), "Europe/Madrid")

    assert "'EXPENSE'" not in compiled.sql
    assert "EXPENSE" in compiled.parameters


@pytest.mark.parametrize(
    ("group_by", "fragment"),
    [
        (GroupBy.NONE, "'total'"),
        (GroupBy.DAY, "'YYYY-MM-DD'"),
        (GroupBy.WEEK, "date_trunc('week'"),
        (GroupBy.MONTH, "'YYYY-MM'"),
        (GroupBy.CATEGORY, "COALESCE(t.category"),
        (GroupBy.MERCHANT, "COALESCE(t.merchant_name"),
    ],
)
def test_each_grouping_compiles_to_its_expression(group_by: GroupBy, fragment: str) -> None:
    compiled = compile_aggregate(query(group_by=group_by), "Europe/Madrid")

    assert fragment in compiled.sql


def test_only_the_authorised_view_is_read() -> None:
    # What counts as booked is the view's decision. If the compiler touched the
    # tables, that rule would be duplicated again and could drift.
    for compiled in (
        compile_aggregate(query(), "Europe/Madrid"),
        compile_movements(query(), "Europe/Madrid"),
    ):
        assert "assistant_read.transactions_v1" in compiled.sql
        assert "ledger_entries" not in compiled.sql
        assert "ledger_transactions" not in compiled.sql


def test_the_last_day_of_the_range_is_included_in_full() -> None:
    compiled = compile_aggregate(query(), "Europe/Madrid")

    # Comparing with `<= to` would drop everything after midnight.
    assert "::date + 1)::timestamp AT TIME ZONE" in compiled.sql


def test_a_backwards_range_is_not_a_query() -> None:
    with pytest.raises(ValidationError):
        DateRange(start=date(2026, 7, 31), end=date(2026, 7, 1))


def test_an_unknown_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        FinancialQuery.model_validate(
            {
                "accountId": str(ACCOUNT),
                "dateRange": {"from": "2026-07-01", "to": "2026-07-31"},
                "sql": "SELECT 1",
            }
        )
