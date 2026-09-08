"""What a chart's `window` is built from: the ledger's own resolved dates, then the plan's own
explicit range, then nothing -- never a guess."""

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


def test_the_answer_window_wins_over_the_plans_own_date_range() -> None:
    """The ledger's own resolved window (`answer.period_start`/`period_end`) is narrower than a
    named period can be -- a `LAST_MONTH` plan clamped mid-month is exactly this case -- so it
    wins over `plan.date_range` whenever both would apply."""
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
