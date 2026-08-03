"""The properties an end-to-end answer must have, whatever the data says.

The plan benchmark stops at the plan, and the COUNT defect lived past that line: the plan
was right, the sentence a person read was about money, and 235 cases plus 163 tests saw
nothing. What was missing is a check that follows the value to the end.

These are properties rather than expected figures on purpose. "movementCount: 3" is true
of one seed on one date and becomes a maintenance chore the first time either changes;
"a COUNT plan is answered with a count" is true of every seed and every date, and it is
the thing that was actually broken.
"""

import importlib.util
import sys
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "evaluate_e2e", Path(__file__).resolve().parents[1] / "scripts" / "evaluate_e2e.py"
)
assert _SPEC and _SPEC.loader
e2e = importlib.util.module_from_spec(_SPEC)
sys.modules["evaluate_e2e"] = e2e
_SPEC.loader.exec_module(e2e)


def answered(**over: object) -> dict[str, object]:
    body: dict[str, object] = {
        "outcome": "PLANNED",
        "text": "449,62€ el mes pasado, en 8 movimientos.",
        "plan": "el mes pasado, expense",
        "plan_context": {"direction": "EXPENSE", "period": "LAST_MONTH", "metric": "SUM"},
        "currency": "EUR",
        "total_minor": 44962,
        "movement_count": 8,
        "groups": [],
        "movements": [],
    }
    return body | over


def test_a_count_plan_answered_with_money_is_a_violation() -> None:
    """The defect, as a property. The plan asked how many and the headline says how
    much, which is the one failure mode no layer below could see."""
    body = answered(
        plan_context={"direction": "EXPENSE", "period": "LAST_MONTH", "metric": "COUNT"},
        text="449,62€ el mes pasado, en 8 movimientos.",
    )

    assert "PLAN_ANSWER_MISMATCH" in e2e.violations(body)


def test_the_correct_pair_passes_in_both_directions() -> None:
    counted = answered(
        plan_context={"metric": "COUNT"}, text="8 movimientos el mes pasado.", total_minor=44962
    )
    summed = answered()

    assert e2e.violations(counted) == []
    assert e2e.violations(summed) == []


def test_a_breakdown_whose_parts_do_not_add_up_is_a_violation() -> None:
    """Evidence that does not reconcile is worse than no evidence: it looks like proof.
    Nothing today can produce this, which is the point of asserting it — if a future
    change to grouping or currency handling can, this says so on the same run."""
    body = answered(
        groups=[
            {"key": "GROCERIES", "total_minor": 30000, "movement_count": 5},
            {"key": "HEALTH", "total_minor": 4000, "movement_count": 2},
        ],
    )

    broken = e2e.violations(body)

    assert "GROUPS_DO_NOT_SUM_TO_TOTAL" in broken
    assert "GROUP_COUNTS_DO_NOT_SUM" in broken


def test_an_ungrouped_answer_is_not_reconciled_against_itself() -> None:
    # The engine always groups by something, so an ungrouped query comes back with one
    # row keyed "total". That is the figure again, not a breakdown.
    body = answered(groups=[{"key": "total", "total_minor": 44962, "movement_count": 8}])

    assert e2e.violations(body) == []


def test_a_declined_question_may_not_carry_a_figure() -> None:
    """The worst shape available: the sentence says no and the payload says a number.
    Whichever the caller renders, one of them is a lie."""
    body = answered(outcome="UNSUPPORTED", text="Eso no puedo calcularlo todavía.")

    assert "FIGURE_WITHOUT_AN_ANSWER" in e2e.violations(body)


def test_an_answer_that_says_nothing_is_a_violation() -> None:
    assert "NOTHING_SAID" in e2e.violations(answered(text=""))


def test_a_figure_with_no_plan_beside_it_is_a_violation() -> None:
    # The plan is shown with every answer on purpose: a number from a pipeline with a
    # language model in it should never have to be taken on trust.
    assert "NO_EVIDENCE" in e2e.violations(answered(plan=None))


def test_a_structural_failure_is_counted_and_not_excused() -> None:
    # Unreachable under constrained decoding, which is exactly why it is worth counting:
    # if it fires, the constraint is not doing what we believe.
    body = answered(
        outcome="INVALID_STRUCTURE", text="No he entendido la pregunta.", total_minor=None
    )

    assert "INVALID_STRUCTURE" in e2e.violations(body)
