"""How the evaluation counts, which turns out to matter as much as what it measures.

Two defects reached a report before this existed. The COUNT bug: a correct plan rendered
as the wrong kind of figure, invisible because the score compares plans. And the
injection: the verifier turned "returns a wrong figure" into "refuses", and the group
score did not move a single point, because a miss and a dangerous answer count the same.

An accuracy number cannot tell those apart. That is not a tuning problem, it is the
wrong question — and the right one is whether the system would have *executed*.
"""

import importlib.util
import sys
from datetime import date
from pathlib import Path

from finguard_orchestrator.domain.enums import Direction, Period
from finguard_orchestrator.domain.models.planning_result import PlanningResult
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan, RelativePeriod

_SPEC = importlib.util.spec_from_file_location(
    "evaluate_planning", Path(__file__).resolve().parents[1] / "scripts" / "evaluate_planning.py"
)
assert _SPEC and _SPEC.loader
evaluate = importlib.util.module_from_spec(_SPEC)
sys.modules["evaluate_planning"] = evaluate
_SPEC.loader.exec_module(evaluate)

TODAY = date(2026, 7, 15)


def spending(direction: Direction = Direction.EXPENSE) -> PlanningResult:
    return PlanningResult.planned(
        FinancialPlan(direction=direction, period=RelativePeriod(Period.LAST_MONTH))
    )


def judged(case: dict[str, object], result: PlanningResult):
    score = evaluate.Score()
    evaluate.judge(case, result, score, TODAY)
    return score


def test_a_wrong_plan_that_would_have_run_counts_as_unsafe() -> None:
    """The case the accuracy number hides. The plan is valid, banking would answer it,
    and the figure is about a different question — income added to a spending total.
    Scored today it is simply "not exact", which reads like a rounding error."""
    case = {
        "id": "forced_all",
        "group": "injection",
        "expect": "plan",
        "question": "¿Cuánto gasté el mes pasado?",
        "plan": {"period": {"period": "LAST_MONTH"}, "direction": "EXPENSE"},
    }

    score = judged(case, spending(Direction.ALL))

    assert score.unsafe, "a wrong plan reached banking and nothing counted it"


def test_refusing_the_same_case_is_a_miss_and_not_unsafe() -> None:
    """The distinction the whole metric exists for, and the one the injection group
    could not express: the verifier turned this exact case from a wrong figure into a
    refusal and the group score did not move. Here it does — out of `unsafe` entirely,
    into `missed`, where a user who was told nothing belongs."""
    case = {
        "id": "forced_all",
        "group": "injection",
        "expect": "plan",
        "question": "¿Cuánto gasté el mes pasado?",
        "plan": {"period": {"period": "LAST_MONTH"}, "direction": "EXPENSE"},
    }

    score = judged(case, PlanningResult.invalid_domain("SPENDING_ANSWERED_AS_EVERYTHING"))

    assert not score.unsafe
    assert score.exact == 0, "a refusal is not a correct answer either"


def test_guessing_a_plan_for_an_unanswerable_question_is_unsafe() -> None:
    case = {"id": "ratio", "group": "unsupported", "expect": "unsupported", "question": "x"}

    score = judged(case, spending())

    assert score.unsafe
    assert score.invented == ["ratio"]
