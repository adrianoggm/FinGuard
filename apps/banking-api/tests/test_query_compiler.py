"""What must never happen: a value from outside ending up inside the SQL string."""

import uuid
from datetime import date

import pytest

from finguard_banking.domain.enums import (
    CounterpartyType,
    Direction,
    GroupBy,
    Metric,
    TransactionType,
)
from finguard_banking.domain.errors import InvalidValueError
from finguard_banking.domain.models.financial_query import FinancialQuery
from finguard_banking.domain.value_objects.category import Category
from finguard_banking.domain.value_objects.date_range import DateRange
from finguard_banking.infrastructure.dto.banking_dto import FinancialQueryRequest
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
    # The category validates itself, so a plan can assume every one it holds is valid.
    with pytest.raises(InvalidValueError):
        Category(INJECTION)


def test_even_bypassing_validation_the_category_travels_as_a_parameter() -> None:
    # The object is built without running its invariants: it simulates someone
    # dodging validation. Not even then does the text make it into the SQL.
    smuggled = object.__new__(Category)
    object.__setattr__(smuggled, "value", INJECTION)
    plan = object.__new__(FinancialQuery)
    object.__setattr__(plan, "account_id", ACCOUNT)
    object.__setattr__(plan, "date_range", RANGE)
    object.__setattr__(plan, "categories", (smuggled,))
    object.__setattr__(plan, "excluded_categories", ())
    object.__setattr__(plan, "direction", Direction.EXPENSE)
    object.__setattr__(plan, "group_by", GroupBy.NONE)
    object.__setattr__(plan, "merchants", ())
    object.__setattr__(plan, "excluded_merchants", ())
    object.__setattr__(plan, "only_account_ids", ())
    object.__setattr__(plan, "transaction_ids", ())
    object.__setattr__(plan, "transaction_types", ())
    object.__setattr__(plan, "supporting_limit", 20)
    object.__setattr__(plan, "explain", False)

    compiled = compile_aggregate(plan, "Europe/Madrid")

    assert "DROP TABLE" not in compiled.sql
    assert [INJECTION] in compiled.parameters
    assert "= ANY($" in compiled.sql


def test_no_input_value_appears_in_the_string() -> None:
    compiled = compile_aggregate(
        query(categories=(Category("GROCERIES"),), direction=Direction.EXPENSE), "Europe/Madrid"
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
        (GroupBy.COUNTERPARTY, "NULLIF(BTRIM(t.description)"),
        (GroupBy.ACCOUNT, "assistant_read.my_accounts_v1"),
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
    with pytest.raises(InvalidValueError):
        DateRange(start=date(2026, 7, 31), end=date(2026, 7, 1))


def test_an_unknown_field_is_rejected() -> None:
    # The wire shape is the DTO's business now; the domain never sees the extra field.
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        FinancialQueryRequest.model_validate(
            {
                "accountId": str(ACCOUNT),
                "dateRange": {"from": "2026-07-01", "to": "2026-07-31"},
                "sql": "SELECT 1",
            }
        )


# --- which groups survive the limit -------------------------------------------------


def test_a_breakdown_of_money_is_ordered_by_money() -> None:
    """`ORDER BY 1` with a LIMIT kept the groups that come first *alphabetically*. Asked
    "¿en qué me he gastado más?", the answer showed the alphabetically first five
    categories and called them the biggest — a wrong answer delivered with a straight
    face, and the demo's own opening question."""
    compiled = compile_aggregate(
        query(group_by=GroupBy.CATEGORY, metric=Metric.SUM), "Europe/Madrid"
    )

    assert "ORDER BY total_minor DESC" in compiled.sql


def test_a_breakdown_of_counts_is_ordered_by_counts() -> None:
    compiled = compile_aggregate(
        query(group_by=GroupBy.MERCHANT, metric=Metric.COUNT), "Europe/Madrid"
    )

    assert "ORDER BY movement_count DESC" in compiled.sql


def test_a_breakdown_of_averages_is_ordered_by_each_groups_mean() -> None:
    compiled = compile_aggregate(
        query(
            group_by=GroupBy.CATEGORY,
            metric=Metric.AVERAGE,
            direction=Direction.EXPENSE,
        ),
        "Europe/Madrid",
    )

    assert "ORDER BY AVG(t.amount_minor) DESC" in compiled.sql


def test_a_selected_account_subset_is_one_bound_array_filter() -> None:
    first = uuid.UUID(int=2)
    second = uuid.UUID(int=3)

    compiled = compile_aggregate(
        query(only_account_ids=(first, second), across_accounts=False), "Europe/Madrid"
    )

    assert "t.account_id = ANY($" in compiled.sql
    assert [first, second] in compiled.parameters
    assert str(first) not in compiled.sql
    assert str(second) not in compiled.sql


def test_an_exact_movement_selection_is_bound_and_stays_holder_scoped() -> None:
    first = uuid.UUID(int=7)
    second = uuid.UUID(int=8)

    compiled = compile_aggregate(query(transaction_ids=(first, second)), "Europe/Madrid")

    assert "assistant_read.transactions_v1" in compiled.sql
    assert "t.account_id = $" in compiled.sql
    assert "t.transaction_id = ANY($" in compiled.sql
    assert [first, second] in compiled.parameters
    assert str(first) not in compiled.sql
    assert str(second) not in compiled.sql


def test_transaction_types_are_bound_instead_of_interpolated() -> None:
    compiled = compile_movements(
        query(transaction_types=(TransactionType.DIRECT_DEBIT,)), "Europe/Madrid"
    )

    assert "t.transaction_type = ANY($" in compiled.sql
    assert ["DIRECT_DEBIT"] in compiled.parameters
    assert "'DIRECT_DEBIT'" not in compiled.sql
    assert "t.transaction_type" in compiled.sql


def test_counterparty_name_and_type_are_separate_bound_filters() -> None:
    compiled = compile_movements(
        query(
            counterparties=("Elena",),
            counterparty_types=(CounterpartyType.PERSON,),
        ),
        "Europe/Madrid",
    )

    assert ["elena"] in compiled.parameters
    assert ["PERSON"] in compiled.parameters
    assert "CASE t.transaction_type" in compiled.sql
    assert "lower(COALESCE(NULLIF(BTRIM(t.merchant_name)" in compiled.sql
    assert "Elena" not in compiled.sql


def test_negative_filters_are_bound_and_keep_uncategorised_rows() -> None:
    compiled = compile_aggregate(
        query(
            excluded_categories=(Category("HOUSING_RENT"),),
            excluded_merchants=("Mercadona",),
        ),
        "Europe/Madrid",
    )

    assert "t.category IS NULL OR NOT (t.category = ANY($" in compiled.sql
    assert "t.merchant_name IS NULL OR NOT (lower(t.merchant_name) = ANY($" in compiled.sql
    assert ["HOUSING_RENT"] in compiled.parameters
    assert ["mercadona"] in compiled.parameters
    assert "HOUSING_RENT" not in compiled.sql
    assert "Mercadona" not in compiled.sql


@pytest.mark.parametrize("group_by", [GroupBy.DAY, GroupBy.WEEK, GroupBy.MONTH])
def test_a_breakdown_over_time_stays_chronological(group_by: GroupBy) -> None:
    """Time is the one grouping where the key's own order is the meaningful one. It also
    happens to be what `ORDER BY 1` did, since the keys are ISO dates — so this is the
    case the old behaviour got right by accident, and it is worth pinning before somebody
    "fixes" it into the money ordering too."""
    compiled = compile_aggregate(query(group_by=group_by, metric=Metric.SUM), "Europe/Madrid")

    assert "ORDER BY key" in compiled.sql
    assert "DESC" not in compiled.sql.split("ORDER BY key")[1]


def test_cumulative_buckets_are_exact_sums_in_chronological_order() -> None:
    compiled = compile_aggregate(
        query(group_by=GroupBy.MONTH, metric=Metric.CUMULATIVE, direction=Direction.EXPENSE),
        "Europe/Madrid",
        group_limit=2,
    )

    assert "SUM(t.amount_minor)" in compiled.sql
    assert "ORDER BY key" in compiled.sql
    assert "LIMIT 500" in compiled.sql


def test_cumulative_without_a_calendar_group_is_invalid() -> None:
    with pytest.raises(InvalidValueError, match="CUMULATIVE requires"):
        query(metric=Metric.CUMULATIVE)


def test_asking_across_accounts_while_naming_one_is_invalid() -> None:
    """The combination the compiler cannot express, and the answer it gave was about everything.

    `only_account_ids` is a filter the compiler applies outright; the single `only_account_id` was
    only ever applied through `scoped_to` in the `elif not across_accounts` arm. So naming the
    piggy bank *and* asking across accounts fell out of both arms, no account condition was
    emitted, and the read came back over every account the holder has — while `scoped_to` still
    said the piggy bank. A real figure about a wider scope than the one asked for.

    Rejected rather than resolved by precedence: whichever half you honour, the other half was
    somebody's intent, and there is no reading of "solo la hucha, en todas mis cuentas" to prefer.
    """
    with pytest.raises(InvalidValueError, match="acrossAccounts"):
        query(only_account_id=uuid.UUID(int=4), across_accounts=True)

    with pytest.raises(InvalidValueError, match="acrossAccounts"):
        query(only_account_ids=(uuid.UUID(int=4),), across_accounts=True)
