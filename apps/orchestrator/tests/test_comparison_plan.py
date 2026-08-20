"""ComparisonPlan owns compatibility and scalar-metric preservation."""

from datetime import date

import pytest

from finguard_orchestrator.domain.enums import Direction, GroupBy, Metric, Period
from finguard_orchestrator.domain.errors import InvalidValueError
from finguard_orchestrator.domain.value_objects.comparison_plan import ComparisonPlan
from finguard_orchestrator.domain.value_objects.financial_plan import (
    AbsoluteRange,
    FinancialPlan,
    RelativePeriod,
)


def plan(
    period: Period,
    *,
    metric: Metric = Metric.SUM,
    group_by: GroupBy = GroupBy.NONE,
    categories: tuple[str, ...] = ("GROCERIES",),
) -> FinancialPlan:
    return FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(period),
        categories=categories,
        group_by=group_by,
        metric=metric,
    )


@pytest.mark.parametrize("metric", [Metric.SUM, Metric.COUNT, Metric.AVERAGE])
def test_scalar_metric_is_preserved_while_presentation_grouping_is_removed(metric: Metric) -> None:
    comparison = ComparisonPlan.between(
        plan(Period.LAST_MONTH, metric=metric, group_by=GroupBy.CATEGORY),
        plan(Period.THIS_MONTH, metric=metric, group_by=GroupBy.CATEGORY),
    )

    assert comparison is not None
    assert comparison.metric is metric
    assert comparison.earlier.group_by is GroupBy.NONE
    assert comparison.latest.group_by is GroupBy.NONE


def test_period_average_preserves_its_calendar_divisor() -> None:
    comparison = ComparisonPlan.between(
        plan(Period.LAST_MONTH, metric=Metric.PERIOD_AVERAGE, group_by=GroupBy.WEEK),
        plan(Period.THIS_MONTH, metric=Metric.PERIOD_AVERAGE, group_by=GroupBy.WEEK),
    )

    assert comparison is not None
    assert comparison.metric is Metric.PERIOD_AVERAGE
    assert comparison.latest.group_by is GroupBy.WEEK


def test_mixed_metrics_or_filters_are_not_a_comparison() -> None:
    assert (
        ComparisonPlan.between(
            plan(Period.LAST_MONTH, metric=Metric.COUNT),
            plan(Period.THIS_MONTH, metric=Metric.SUM),
        )
        is None
    )
    assert (
        ComparisonPlan.between(
            plan(Period.LAST_MONTH),
            plan(Period.THIS_MONTH, categories=("HOUSING_RENT",)),
        )
        is None
    )


@pytest.mark.parametrize("metric", [Metric.FREQUENCY, Metric.RECURRENCE])
def test_non_scalar_temporal_analysis_cannot_silently_become_money(metric: Metric) -> None:
    assert (
        ComparisonPlan.between(
            plan(Period.LAST_MONTH, metric=metric),
            plan(Period.THIS_MONTH, metric=metric),
        )
        is None
    )


def test_direct_construction_refuses_the_same_window() -> None:
    with pytest.raises(InvalidValueError, match="different windows"):
        ComparisonPlan(
            earlier=plan(Period.LAST_MONTH),
            latest=plan(Period.LAST_MONTH),
        )


def test_an_arbitrary_comparison_range_is_bounded() -> None:
    earlier = FinancialPlan(
        direction=Direction.EXPENSE,
        date_range=AbsoluteRange(date(2024, 1, 1), date(2025, 1, 1)),
    )
    latest = FinancialPlan(
        direction=Direction.EXPENSE,
        date_range=AbsoluteRange(date(2025, 1, 1), date(2026, 1, 1)),
    )

    with pytest.raises(InvalidValueError, match="at most 366 days"):
        ComparisonPlan(earlier=earlier, latest=latest)
