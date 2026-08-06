"""The taxonomy, and the states it makes unrepresentable."""

from datetime import date

import pytest

from finguard_orchestrator.domain.enums import Direction, GroupBy, Period, PlanningOutcome
from finguard_orchestrator.domain.errors import InvalidValueError
from finguard_orchestrator.domain.models.planning_result import PlanningResult
from finguard_orchestrator.domain.value_objects.financial_plan import (
    AbsoluteRange,
    FinancialPlan,
    RelativePeriod,
)

LAST_MONTH = RelativePeriod(period=Period.LAST_MONTH)


def a_plan(**kwargs: object) -> FinancialPlan:
    return FinancialPlan(direction=Direction.EXPENSE, period=LAST_MONTH, **kwargs)  # type: ignore[arg-type]


def test_only_planned_reaches_the_ledger() -> None:
    # Five of six outcomes are answered without calling banking at all. That is the
    # point of the enum: a wrong plan is cheaper to catch here than there.
    assert PlanningResult.planned(a_plan()).is_answerable
    for result in (
        PlanningResult.needs_clarification("¿qué mes?"),
        PlanningResult.unsupported("no average"),
        PlanningResult.invalid_structure("not json"),
        PlanningResult.invalid_domain("unknown category"),
        PlanningResult.provider_error("daemon down"),
    ):
        assert not result.is_answerable


def test_an_outcome_cannot_disagree_with_its_payload() -> None:
    with pytest.raises(InvalidValueError):
        PlanningResult(PlanningOutcome.PLANNED)  # planned, but nothing planned

    with pytest.raises(InvalidValueError):
        PlanningResult(PlanningOutcome.PROVIDER_ERROR, plan=a_plan())


def test_clarification_must_carry_the_question() -> None:
    # A clarification with nothing to ask leaves the user with "I did not understand",
    # which is a dead end rather than a turn in the conversation.
    with pytest.raises(InvalidValueError):
        PlanningResult(PlanningOutcome.NEEDS_CLARIFICATION)

    with pytest.raises(InvalidValueError):
        PlanningResult(PlanningOutcome.UNSUPPORTED, question="¿qué mes?")


def test_a_plan_needs_exactly_one_kind_of_window() -> None:
    # Both is not over-specification: it is two different questions, and which one won
    # would depend on who read it.
    with pytest.raises(InvalidValueError):
        FinancialPlan(direction=Direction.EXPENSE)

    with pytest.raises(InvalidValueError):
        FinancialPlan(
            direction=Direction.EXPENSE,
            period=LAST_MONTH,
            date_range=AbsoluteRange(start=date(2026, 1, 1), end=date(2026, 1, 31)),
        )


def test_n_belongs_to_the_counted_periods_only() -> None:
    assert RelativePeriod(period=Period.LAST_N_DAYS, n=30).n == 30

    with pytest.raises(InvalidValueError):
        RelativePeriod(period=Period.LAST_N_DAYS)

    with pytest.raises(InvalidValueError):
        RelativePeriod(period=Period.LAST_MONTH, n=1)

    with pytest.raises(InvalidValueError):
        RelativePeriod(period=Period.LAST_N_MONTHS, n=999)


def test_a_backwards_range_is_not_a_window() -> None:
    with pytest.raises(InvalidValueError):
        AbsoluteRange(start=date(2026, 7, 31), end=date(2026, 7, 1))


def test_a_category_from_a_model_is_checked_not_trusted() -> None:
    assert a_plan(categories=("GROCERIES",)).categories == ("GROCERIES",)

    with pytest.raises(InvalidValueError):
        a_plan(categories=("TRANSPORT_FUEL'; DROP TABLE ledger_entries; --",))

    with pytest.raises(InvalidValueError):
        a_plan(categories=("",))


def test_the_defaults_are_the_conservative_ones() -> None:
    # An unrequested breakdown is as wrong as a missing one, so NONE is the default;
    # SUM because "how much" is the common question and COUNT the marked one.
    plan = a_plan()
    assert plan.group_by is GroupBy.NONE
    assert plan.metric.value == "SUM"
    assert plan.categories == ()
    assert plan.explain is False
