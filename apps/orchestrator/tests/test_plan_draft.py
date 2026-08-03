"""The plan the model had already built when it stopped to ask a question.

Today that work is thrown away. The planner reads a sentence, produces a complete reading of
it, notices one piece is missing, and returns `needs_clarification(question)` — a string. The
direction, the categories, the metric and the grouping it got right are discarded, so the
reply "este año" arrives as a two-word sentence with no history and the whole question has to
be derived from it again. That is why answering the clarification asks for the period again.

A draft is that reading, minus the one thing it lacks. It is not a `FinancialPlan` because a
plan needs exactly one window and this has none — the invariant is right and the draft is the
honest way to hold something that does not satisfy it yet.

The only slot that can be absent is the window: every other field in the schema has a value or
a sentinel, so the model always names one. Hence a boolean rather than a set of missing slots —
there is one to be missing. A second one would turn this into a set, and the caller would need
to know which.
"""

from datetime import date

import pytest

from finguard_orchestrator.domain.enums import Direction, GroupBy, Metric, Period
from finguard_orchestrator.domain.errors import InvalidValueError
from finguard_orchestrator.domain.value_objects.financial_plan import (
    AbsoluteRange,
    FinancialPlanDraft,
    RelativePeriod,
)

RENT = FinancialPlanDraft(
    direction=Direction.EXPENSE,
    categories=("HOUSING_RENT",),
    metric=Metric.SUM,
)


def test_a_draft_with_no_window_says_so() -> None:
    """ "¿Cuánto suelo pagar de alquiler?" reads completely except for the period, which is
    exactly what the question back is about."""
    assert RENT.needs_a_window is True


def test_the_window_completes_it_into_a_plan() -> None:
    plan = RENT.with_window(RelativePeriod(period=Period.THIS_YEAR))

    assert plan.period == RelativePeriod(period=Period.THIS_YEAR)
    assert plan.direction is Direction.EXPENSE
    assert plan.categories == ("HOUSING_RENT",)
    assert plan.metric is Metric.SUM
    assert plan.date_range is None


def test_an_explicit_range_completes_it_too() -> None:
    """A reply naming both ends is the same completion. The draft holds one window or the
    other for the same reason the plan does."""
    window = AbsoluteRange(start=date(2026, 1, 1), end=date(2026, 1, 31))

    plan = RENT.with_window(window)

    assert plan.date_range == window
    assert plan.period is None


def test_a_draft_that_already_has_a_window_needs_none() -> None:
    """The model can ask about something else — "¿te refieres a ocio?" — with the window
    already read. Then there is no slot to fill deterministically and the reply goes to the
    planner like any other sentence."""
    drafted = FinancialPlanDraft(
        direction=Direction.EXPENSE,
        period=RelativePeriod(period=Period.LAST_MONTH),
        categories=("LEISURE",),
    )

    assert drafted.needs_a_window is False


def test_a_draft_cannot_hold_two_windows() -> None:
    """Same rule as the plan, and for the same reason: two windows are two questions, and
    which one wins would depend on the reader."""
    with pytest.raises(InvalidValueError):
        FinancialPlanDraft(
            direction=Direction.EXPENSE,
            period=RelativePeriod(period=Period.LAST_MONTH),
            date_range=AbsoluteRange(start=date(2026, 1, 1), end=date(2026, 1, 31)),
        )


def test_the_completed_plan_is_validated() -> None:
    """`with_window` returns a `FinancialPlan`, so every invariant runs on the way out. The
    draft itself checks nothing about its categories — it is a half-built thing, and the place
    that matters is the one where it becomes a query."""
    drafted = FinancialPlanDraft(direction=Direction.EXPENSE, categories=("no válida",))

    with pytest.raises(InvalidValueError):
        drafted.with_window(RelativePeriod(period=Period.THIS_YEAR))


def test_a_plan_can_be_read_back_as_a_draft() -> None:
    """Needed by the merge path, which holds a plan and has to answer a clarification about
    it. Round-tripping keeps one shape on the wire instead of two."""
    plan = RENT.with_window(RelativePeriod(period=Period.THIS_MONTH))

    assert FinancialPlanDraft.of(plan).with_window(plan.period) == plan


def test_the_grouping_and_the_shops_survive() -> None:
    """The fields nobody thinks about are the ones a discarded draft loses. A breakdown by
    category asked about two shops is still that question after the period is supplied."""
    drafted = FinancialPlanDraft(
        direction=Direction.EXPENSE,
        merchants=("Mercadona", "Carrefour"),
        group_by=GroupBy.MERCHANT,
        metric=Metric.COUNT,
    )

    plan = drafted.with_window(RelativePeriod(period=Period.LAST_N_DAYS, n=30))

    assert plan.merchants == ("Mercadona", "Carrefour")
    assert plan.group_by is GroupBy.MERCHANT
    assert plan.metric is Metric.COUNT
