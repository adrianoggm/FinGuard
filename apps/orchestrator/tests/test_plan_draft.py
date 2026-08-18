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


def test_a_complete_draft_can_become_a_plan_without_replacing_its_window() -> None:
    drafted = FinancialPlanDraft(
        direction=Direction.EXPENSE,
        period=RelativePeriod(period=Period.LAST_MONTH),
        categories=("LEISURE",),
    )

    plan = drafted.into_plan()

    assert plan is not None
    assert plan.period == RelativePeriod(period=Period.LAST_MONTH)
    assert plan.categories == ("LEISURE",)


def test_an_incomplete_draft_cannot_become_a_plan() -> None:
    assert RENT.into_plan() is None


def test_a_draft_cannot_hold_two_windows() -> None:
    """Same rule as the plan, and for the same reason: two windows are two questions, and
    which one wins would depend on the reader."""
    with pytest.raises(InvalidValueError):
        FinancialPlanDraft(
            direction=Direction.EXPENSE,
            period=RelativePeriod(period=Period.LAST_MONTH),
            date_range=AbsoluteRange(start=date(2026, 1, 1), end=date(2026, 1, 31)),
        )


def test_the_window_is_the_only_invariant_a_draft_relaxes() -> None:
    """A category is checked here, not left until the window arrives.

    It was left until then at first, and that made the draft weaker than the plan it sits
    beside on the wire: an echoed draft naming a category that is not one was accepted at the
    edge and only refused a layer deeper, where the honest answer is no longer a 422. A draft
    is half-built, not half-checked.
    """
    with pytest.raises(InvalidValueError):
        FinancialPlanDraft(direction=Direction.EXPENSE, categories=("no válida",))


def test_the_completed_plan_is_validated_too() -> None:
    """`with_window` returns a `FinancialPlan`, so the invariants that need a window — the
    period's count, a range that ends before it starts — run on the way out."""
    drafted = FinancialPlanDraft(direction=Direction.EXPENSE)

    with pytest.raises(InvalidValueError):
        drafted.with_window(RelativePeriod(period=Period.THIS_YEAR, n=3))


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


def test_negative_filters_survive_completion_and_cannot_contradict_inclusions() -> None:
    drafted = FinancialPlanDraft(
        direction=Direction.EXPENSE,
        excluded_categories=("HOUSING_RENT",),
        excluded_merchants=("Mercadona",),
    )

    plan = drafted.with_window(RelativePeriod(period=Period.THIS_YEAR))

    assert plan.excluded_categories == ("HOUSING_RENT",)
    assert plan.excluded_merchants == ("Mercadona",)
    with pytest.raises(InvalidValueError):
        FinancialPlanDraft(
            direction=Direction.EXPENSE,
            categories=("HOUSING_RENT",),
            excluded_categories=("HOUSING_RENT",),
        )
