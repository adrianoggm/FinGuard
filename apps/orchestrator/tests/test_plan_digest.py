"""A fingerprint of a plan, stable across turns and sensitive to every field."""

import re
from dataclasses import replace
from datetime import date

from finguard_orchestrator.domain.enums import Direction, GroupBy, Period
from finguard_orchestrator.domain.services.plan_digest import plan_digest
from finguard_orchestrator.domain.value_objects.financial_plan import (
    AbsoluteRange,
    FinancialPlan,
    RelativePeriod,
)

_HEX16 = re.compile(r"^[0-9a-f]{16}$")


def test_equal_plans_digest_equal() -> None:
    a = FinancialPlan(direction=Direction.EXPENSE, period=RelativePeriod(Period.LAST_MONTH))
    b = FinancialPlan(direction=Direction.EXPENSE, period=RelativePeriod(Period.LAST_MONTH))
    assert plan_digest(a) == plan_digest(b)


def test_one_changed_field_changes_the_digest() -> None:
    plan = FinancialPlan(direction=Direction.EXPENSE, period=RelativePeriod(Period.LAST_MONTH))
    changed = replace(plan, group_by=GroupBy.CATEGORY)
    assert plan_digest(plan) != plan_digest(changed)


def test_the_digest_is_sixteen_lowercase_hex_characters() -> None:
    plan = FinancialPlan(direction=Direction.EXPENSE, period=RelativePeriod(Period.LAST_MONTH))
    digest = plan_digest(plan)
    assert len(digest) == 16
    assert _HEX16.fullmatch(digest)


def test_a_date_range_plan_and_a_period_plan_digest_differently() -> None:
    with_period = FinancialPlan(
        direction=Direction.EXPENSE, period=RelativePeriod(Period.LAST_MONTH)
    )
    with_range = FinancialPlan(
        direction=Direction.EXPENSE,
        period=None,
        date_range=AbsoluteRange(start=date(2026, 7, 1), end=date(2026, 7, 31)),
    )
    assert plan_digest(with_period) != plan_digest(with_range)
