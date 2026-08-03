"""A follow-up inherits what it does not mention, and code decides what that means.

The prompt used to ask the model for three things at once: whether the sentence continues the
last one, which fields it changes, and the full value of every field it does not. The third is
where it failed — measured, `¿Y la semana pasada?` after a question about the supermarket came
back with `categories=()`, losing the filter it was supposed to keep. Nothing was wrong with
its reading; it was asked to copy six fields and copied five.

So it no longer copies. It says what changed, and the merge is arithmetic on a validated plan.
"""

from datetime import date

import pytest

from finguard_orchestrator.domain.enums import Direction, GroupBy, Metric, Period
from finguard_orchestrator.domain.errors import InvalidValueError
from finguard_orchestrator.domain.services.plan_merger import Field, merge
from finguard_orchestrator.domain.value_objects.financial_plan import (
    AbsoluteRange,
    FinancialPlan,
    RelativePeriod,
)

THIS_MONTH = RelativePeriod(Period.THIS_MONTH)
LAST_WEEK = RelativePeriod(Period.LAST_WEEK)

# "¿Cuánto gasté en el súper este mes?" — the question a follow-up follows.
PREVIOUS = FinancialPlan(
    direction=Direction.EXPENSE,
    period=THIS_MONTH,
    categories=("GROCERIES",),
)


def emitted(**kwargs: object) -> FinancialPlan:
    """What the model produced for the follow-up, fields it was not asked about included."""
    return FinancialPlan(direction=Direction.ALL, period=LAST_WEEK, **kwargs)  # type: ignore[arg-type]


def test_a_changed_window_keeps_everything_else() -> None:
    """The measured failure. "¿Y la semana pasada?" changes the window and nothing else, and
    the category it was supposed to keep is the one the model dropped."""
    merged = merge(PREVIOUS, emitted(), changed=frozenset({Field.WINDOW}))

    assert merged.period == LAST_WEEK
    assert merged.categories == ("GROCERIES",)
    assert merged.direction is Direction.EXPENSE, "direction was not mentioned, so it is inherited"


def test_a_changed_direction_keeps_the_window() -> None:
    merged = merge(PREVIOUS, emitted(), changed=frozenset({Field.DIRECTION}))

    assert merged.direction is Direction.ALL
    assert merged.period == THIS_MONTH


def test_an_absolute_range_replaces_a_named_window() -> None:
    """The two spell one thing. Inheriting a period while accepting a range would build a plan
    with both, which the domain refuses — and refusing here would lose a legitimate follow-up."""
    range_plan = FinancialPlan(
        direction=Direction.EXPENSE,
        date_range=AbsoluteRange(date(2026, 1, 1), date(2026, 1, 31)),
    )

    merged = merge(PREVIOUS, range_plan, changed=frozenset({Field.WINDOW}))

    assert merged.date_range is not None
    assert merged.period is None


def test_changing_nothing_repeats_the_previous_question() -> None:
    # Not an error: "¿y eso?" is a real thing to say, and repeating the last answer is a
    # better response than inventing a window.
    merged = merge(PREVIOUS, emitted(), changed=frozenset())

    assert merged == PREVIOUS


def test_changing_everything_ignores_the_previous_plan() -> None:
    merged = merge(PREVIOUS, emitted(), changed=frozenset(Field))

    assert merged == emitted()


def test_an_empty_category_list_is_a_change_when_it_is_named() -> None:
    """ "¿Y en general?" removes the filter, and the merge has to be able to express that —
    which is why `changed` is explicit rather than inferred from emptiness."""
    merged = merge(PREVIOUS, emitted(), changed=frozenset({Field.CATEGORIES}))

    assert merged.categories == ()


def test_the_result_is_a_validated_plan() -> None:
    """The merge cannot produce something the domain would refuse. It builds a FinancialPlan,
    so every invariant runs on the way out — a merge is not a place to skip them."""
    with pytest.raises(InvalidValueError):
        merge(
            PREVIOUS,
            emitted(categories=("BAD CATEGORY",)),
            changed=frozenset({Field.CATEGORIES}),
        )


def test_metric_and_grouping_are_inherited_independently() -> None:
    previous = FinancialPlan(
        direction=Direction.EXPENSE,
        period=THIS_MONTH,
        group_by=GroupBy.CATEGORY,
        metric=Metric.SUM,
    )

    merged = merge(previous, emitted(metric=Metric.COUNT), changed=frozenset({Field.METRIC}))

    assert merged.metric is Metric.COUNT
    assert merged.group_by is GroupBy.CATEGORY
