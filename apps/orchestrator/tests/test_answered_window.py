"""What a chart's `window` is built from: the ledger's own resolved dates, bounded by the plan's
own explicit range when it has one, then the ledger's dates alone, then the plan's alone, then
nothing -- never a guess, and never wider than what the plan itself asked for (N-2, fix round 2)."""

from datetime import date

from finguard_orchestrator.application.queries.ask_financial_question import (
    _answered_window,
    _visual_dataset,
)
from finguard_orchestrator.domain.enums import Direction, GroupBy, Metric, Period
from finguard_orchestrator.domain.models.query_answer import Group, QueryAnswer
from finguard_orchestrator.domain.value_objects.financial_plan import (
    AbsoluteRange,
    FinancialPlan,
    RelativePeriod,
)


def test_the_answered_window_is_the_intersection_of_the_ledger_and_the_plan() -> None:
    """Both known: the window is their intersection, not the ledger's alone. Here the ledger's own
    resolved window (narrower than a named period can be -- a `LAST_MONTH` plan clamped mid-month
    is exactly this case) already sits inside the plan's own range, so the intersection equals the
    ledger's window -- proven properly by the N-2 regression below, where it does not."""
    plan = FinancialPlan(
        direction=Direction.EXPENSE,
        date_range=AbsoluteRange(start=date(2026, 6, 10), end=date(2026, 7, 20)),
    )
    answer = QueryAnswer(
        currency="EUR",
        total_minor=0,
        movement_count=0,
        period_start=date(2026, 7, 1),
        period_end=date(2026, 7, 5),
    )
    assert _answered_window(plan, answer) == AbsoluteRange(date(2026, 7, 1), date(2026, 7, 5))


def test_n2_the_reviewers_probe_a_wider_ledger_answer_is_bounded_by_the_plans_own_range() -> None:
    """N-2, the re-reviewer's own numbers: a plan asking `2026-06-10..2026-07-20` answered by a
    ledger (or the corpus fake) that reports the whole of June and July, `2026-06-01..2026-07-31` --
    eleven days wider than the plan ever queried. The window must be bounded by the plan's own
    range, not the ledger's alone: `2026-06-10..2026-07-20`, exactly what was asked."""
    plan = FinancialPlan(
        direction=Direction.EXPENSE,
        date_range=AbsoluteRange(start=date(2026, 6, 10), end=date(2026, 7, 20)),
    )
    answer = QueryAnswer(
        currency="EUR",
        total_minor=0,
        movement_count=0,
        period_start=date(2026, 6, 1),
        period_end=date(2026, 7, 31),
    )
    assert _answered_window(plan, answer) == AbsoluteRange(date(2026, 6, 10), date(2026, 7, 20))


def test_an_empty_intersection_is_none() -> None:
    """The ledger answered a window disjoint from the plan's own -- fail closed, never a guess,
    never a month drill over a window that does not overlap what was actually asked."""
    plan = FinancialPlan(
        direction=Direction.EXPENSE,
        date_range=AbsoluteRange(start=date(2026, 6, 10), end=date(2026, 7, 20)),
    )
    answer = QueryAnswer(
        currency="EUR",
        total_minor=0,
        movement_count=0,
        period_start=date(2026, 8, 1),
        period_end=date(2026, 8, 31),
    )
    assert _answered_window(plan, answer) is None


def test_the_ledgers_window_alone_for_a_relative_period_plan() -> None:
    """A `RelativePeriod` plan has no `date_range` of its own to bound the ledger's answer with --
    the ledger's window is all there is, and it is read alone."""
    plan = FinancialPlan(direction=Direction.EXPENSE, period=RelativePeriod(Period.LAST_MONTH))
    answer = QueryAnswer(
        currency="EUR",
        total_minor=0,
        movement_count=0,
        period_start=date(2026, 7, 1),
        period_end=date(2026, 7, 5),
    )
    assert _answered_window(plan, answer) == AbsoluteRange(date(2026, 7, 1), date(2026, 7, 5))


def test_the_plans_date_range_is_read_when_the_answer_names_no_window() -> None:
    plan = FinancialPlan(
        direction=Direction.EXPENSE,
        date_range=AbsoluteRange(start=date(2026, 6, 10), end=date(2026, 7, 20)),
    )
    answer = QueryAnswer(currency="EUR", total_minor=0, movement_count=0)
    assert _answered_window(plan, answer) == plan.date_range


def test_the_window_is_none_when_neither_the_answer_nor_the_plan_names_one() -> None:
    plan = FinancialPlan(direction=Direction.EXPENSE, period=RelativePeriod(Period.LAST_MONTH))
    answer = QueryAnswer(currency="EUR", total_minor=0, movement_count=0)
    assert _answered_window(plan, answer) is None


def test_a_partial_ledger_window_is_not_read_as_resolved() -> None:
    """Both ends or nothing: an answer naming only one end of a window is not a window this
    function trusts -- it falls through to the plan's own range instead of half-guessing."""
    plan = FinancialPlan(
        direction=Direction.EXPENSE,
        date_range=AbsoluteRange(start=date(2026, 6, 10), end=date(2026, 7, 20)),
    )
    answer = QueryAnswer(
        currency="EUR", total_minor=0, movement_count=0, period_start=date(2026, 7, 1)
    )
    assert _answered_window(plan, answer) == plan.date_range


def test_the_visual_dataset_carries_the_answered_window() -> None:
    """`_visual_dataset` wires `_answered_window` into every `VisualDataset` it builds off
    `visible_groups` -- the plain breakdown path, exercised through a real plan and answer."""
    plan = FinancialPlan(
        direction=Direction.EXPENSE,
        date_range=AbsoluteRange(start=date(2026, 7, 1), end=date(2026, 7, 20)),
        group_by=GroupBy.CATEGORY,
        metric=Metric.SUM,
    )
    answer = QueryAnswer(
        currency="EUR",
        total_minor=18_000,
        movement_count=5,
        period_start=date(2026, 7, 1),
        period_end=date(2026, 7, 20),
    )
    groups = (Group(key="GROCERIES", total_minor=18_000, movement_count=5),)
    dataset = _visual_dataset(plan, answer, groups, reporting_timezone="Europe/Madrid")
    assert dataset is not None
    assert dataset.window == AbsoluteRange(date(2026, 7, 1), date(2026, 7, 20))
