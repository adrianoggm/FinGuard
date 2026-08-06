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


def answered(*, last_plan: dict[str, object] | None = None, **over: object) -> dict[str, object]:
    """A PLANNED answer as the wire spells it now: the plan lives inside the conversation, under
    `last_plan`, because it is the memory of this turn rather than a field of its own."""
    body: dict[str, object] = {
        "outcome": "PLANNED",
        "text": "449,62€ el mes pasado, en 8 movimientos.",
        "plan": "el mes pasado, expense",
        "conversation": {
            "workflow": None,
            "last_plan": last_plan
            or {"direction": "EXPENSE", "period": "LAST_MONTH", "metric": "SUM"},
        },
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
        last_plan={"direction": "EXPENSE", "period": "LAST_MONTH", "metric": "COUNT"},
        text="449,62€ el mes pasado, en 8 movimientos.",
    )

    assert "PLAN_ANSWER_MISMATCH" in e2e.violations(body)


def test_the_correct_pair_passes_in_both_directions() -> None:
    counted = answered(
        last_plan={"metric": "COUNT"}, text="8 movimientos el mes pasado.", total_minor=44962
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


# --- the conversational contract ------------------------------------------------------
#
# `turn_violations` is the half that needs history, and until now it could only ask what a turn
# *kept*. Everything below is the other half: what a turn must be, must change, and what shape the
# answer takes. The worst defect in the report passed every check this file used to have.


def plan(**over: object) -> dict[str, object]:
    """A plan as the wire spells it, inside `conversation.last_plan`."""
    return {
        "direction": "EXPENSE",
        "period": "LAST_N_MONTHS",
        "n": 5,
        "start": None,
        "end": None,
        "categories": [],
        "merchants": [],
        "group_by": "NONE",
        "metric": "SUM",
        "action": "AGGREGATE",
        "rank": None,
    } | over


def turn(body_plan: dict[str, object], **body: object) -> dict[str, object]:
    return answered(last_plan=body_plan) | body


def test_a_window_the_sentence_named_may_not_be_inherited() -> None:
    """The reported failure as a property. Five months before, "los últimos 3 meses" now, and the
    answer came back over five — with a real figure, a valid plan and `must_keep` satisfied."""
    before = plan(n=5)

    broken = e2e.turn_violations(
        {"say": "...", "must_change": ["window"]}, turn(before), False, before
    )

    assert "INHERITED_WINDOW" in broken


def test_a_window_that_did_change_passes() -> None:
    before = plan(n=5)

    broken = e2e.turn_violations(
        {"say": "...", "must_change": ["window"]}, turn(plan(n=3)), False, before
    )

    assert broken == []


def test_a_field_may_be_required_to_have_a_particular_value() -> None:
    """`expect_plan` is only legitimate for what a deterministic path owns, and the window is one
    now: `explicit_facts` enforces it after the merge, so this asserts our behaviour rather than
    betting on the model."""
    asked = {"say": "...", "expect_plan": {"window": "LAST_N_MONTHS:3"}}

    assert e2e.turn_violations(asked, turn(plan(n=3)), False, {}) == []
    assert "WRONG_WINDOW" in e2e.turn_violations(asked, turn(plan(n=5)), False, {})


def test_an_absolute_range_is_written_the_way_a_person_would() -> None:
    july = plan(period=None, n=None, start="2025-07-01", end="2025-07-31")
    asked = {"say": "...", "expect_plan": {"window": "2025-07-01..2025-07-31"}}

    assert e2e.turn_violations(asked, turn(july), False, {}) == []


def test_a_list_field_is_compared_without_its_order() -> None:
    """The order a model emits filters in is not part of the question."""
    both = plan(categories=["RESTAURANTS", "GROCERIES"])
    asked = {"say": "...", "expect_plan": {"categories": ["GROCERIES", "RESTAURANTS"]}}

    assert e2e.turn_violations(asked, turn(both), False, {}) == []


def test_a_field_the_checker_does_not_know_stops_the_run() -> None:
    """Loudly, rather than passing. A check nobody can express is a check nobody writes, and a
    silently ignored key in the dataset is worse than no key at all."""
    import pytest

    with pytest.raises(SystemExit):
        e2e.turn_violations({"say": "...", "must_keep": ["mood"]}, turn(plan()), False, plan())


def test_a_question_about_people_must_come_back_as_rows() -> None:
    """Correct number, wrong shape. "¿A qué personas les he hecho Bizum?" was answered with a
    total, and no rule about outcomes or windows could see it."""
    total = turn(plan(), groups=[])
    asked = {"say": "...", "expect_shape": "GROUPS", "expect_groups": ["Elena"]}

    broken = e2e.turn_violations(asked, total, False, {})

    assert "SHAPE_IS_TOTAL" in broken
    assert "NO_GROUP_ELENA" in broken


def test_the_rows_it_asked_for_satisfy_it() -> None:
    grouped = turn(
        plan(group_by="MERCHANT"),
        groups=[
            {"key": "Elena", "total_minor": 4656, "movement_count": 2},
            {"key": "Sergio", "total_minor": 3166, "movement_count": 1},
        ],
        total_minor=7822,
        movement_count=3,
    )
    asked = {"say": "...", "expect_shape": "GROUPS", "expect_groups": ["Elena", "Sergio"]}

    assert e2e.turn_violations(asked, grouped, False, {}) == []


def test_an_ungrouped_total_is_not_a_breakdown() -> None:
    """The engine always groups by something, so an ungrouped query returns one row keyed "total".
    That is the figure again, and a question about who is not answered by it."""
    body = turn(plan(), groups=[{"key": "total", "total_minor": 44962, "movement_count": 8}])

    assert e2e.shape_of(body) == "TOTAL"


def test_one_movement_is_its_own_shape() -> None:
    """A ranking answers with a row, not a figure. "¿Cuál es el Bizum más alto?" asking for a
    MOVEMENT is what separates it from the total it used to come back as."""
    ranked = turn(
        plan(action="RANK_MOVEMENTS", rank=1),
        groups=[],
        movements=[
            {
                "occurred_at": "2026-06-07T12:00:00",
                "description": "Bizum",
                "amount_minor": 3948,
                "direction": "EXPENSE",
            }
        ],
    )

    assert e2e.shape_of(ranked) == "MOVEMENT"


# --- asking the same thing three times --------------------------------------------------


def asking(slot: str) -> dict[str, object]:
    return {
        "outcome": "NEEDS_CLARIFICATION",
        "text": "¿De qué periodo?",
        "conversation": {"workflow": "FINANCIAL_CLARIFICATION", "awaiting": slot},
    }


def test_the_slot_identifies_a_repeat_and_not_the_wording() -> None:
    """Two phrasings of "which period?" are the same dead end, and the wording comes from a model
    in the first place."""
    assert e2e.asked_slot(asking("WINDOW")) == "WINDOW"
    assert e2e.asked_slot(answered()) is None


def test_an_answer_is_not_a_clarification_however_it_reads() -> None:
    assert e2e.asked_slot(answered(text="¿De qué periodo?")) is None


def test_a_run_of_the_same_question_is_counted() -> None:
    """Two is a clarification and one retry. The third is the loop from the report, where
    "¿de qué periodo?" came back about a period the user had already written twice."""
    run = e2e.clarification_run("WINDOW", None, 0)
    run = e2e.clarification_run("WINDOW", "WINDOW", run)
    run = e2e.clarification_run("WINDOW", "WINDOW", run)

    assert run == 3 > e2e.MAX_REPEATED_CLARIFICATIONS


def test_an_answer_between_two_questions_breaks_the_run() -> None:
    """Asking, being answered, and asking again later is a conversation. Only consecutive turns
    about the same slot are a loop."""
    assert e2e.clarification_run(None, "WINDOW", 2) == 0
    assert e2e.clarification_run("WINDOW", None, 0) == 1


def test_a_different_question_breaks_the_run_too() -> None:
    """Asking for the period and then for the category is progress, however slow."""
    assert e2e.clarification_run("CATEGORY", "WINDOW", 2) == 1


# --- the dataset says what the checker can check ------------------------------------------


def conversations() -> list[dict]:
    import yaml

    return yaml.safe_load((e2e.PLANNING / "conversations.yaml").read_text(encoding="utf-8"))[
        "conversations"
    ]


def test_every_key_in_the_dataset_is_one_a_reader_acts_on() -> None:
    """A mistyped key is silently ignored by every reader in the script, so a case that asserts
    nothing reads exactly like a case that passes. This is the only thing standing between
    `must_chang:` and a green run."""
    for case in conversations():
        assert set(case) <= e2e.CONVERSATION_KEYS, (
            f"{case['id']}: {set(case) - e2e.CONVERSATION_KEYS}"
        )
        for index, turn in enumerate(case["turns"], start=1):
            unknown = set(turn) - e2e.TURN_KEYS
            assert not unknown, f"{case['id']}#{index}: {unknown}"


def test_every_field_the_dataset_names_is_one_the_checker_knows() -> None:
    """`_value` raises SystemExit on an unknown field, which is right at runtime and late. This
    finds it without a running service."""
    for case in conversations():
        for index, turn in enumerate(case["turns"], start=1):
            named = (
                list(turn.get("must_keep") or [])
                + list(turn.get("must_change") or [])
                + list(turn.get("expect_plan") or {})
            )
            for field in named:
                assert field in e2e._FIELDS, f"{case['id']}#{index}: {field}"
            shape = turn.get("expect_shape")
            assert shape is None or shape in e2e.SHAPES, f"{case['id']}#{index}: {shape}"


def test_the_dataset_spells_a_window_the_way_the_wire_produces_one() -> None:
    """The gap that would make every `expect_plan.window` fail on a live run and pass here.

    The dataset writes `LAST_N_MONTHS:3` and `2025-07-01..2025-12-31`; the service emits a payload
    with `period`, `n`, `start` and `end`. Both halves are tested in isolation and neither knows
    about the other, so this builds a real plan, maps it the way the endpoint does, and asserts the
    two spellings meet.
    """
    from datetime import date

    from finguard_orchestrator.domain.enums import Direction, Period
    from finguard_orchestrator.domain.value_objects.financial_plan import (
        AbsoluteRange,
        FinancialPlan,
        RelativePeriod,
    )
    from finguard_orchestrator.infrastructure.mappers import conversation_mapper

    def on_the_wire(plan: FinancialPlan) -> dict:
        return conversation_mapper.plan_payload(plan).model_dump(mode="json")

    counted = FinancialPlan(
        direction=Direction.EXPENSE, period=RelativePeriod(period=Period.LAST_N_MONTHS, n=3)
    )
    named = FinancialPlan(direction=Direction.EXPENSE, period=RelativePeriod(Period.THIS_MONTH))
    ranged = FinancialPlan(
        direction=Direction.EXPENSE,
        date_range=AbsoluteRange(start=date(2025, 7, 1), end=date(2025, 7, 31)),
    )

    assert e2e._window_of(on_the_wire(counted)) == "LAST_N_MONTHS:3"
    assert e2e._window_of(on_the_wire(named)) == "THIS_MONTH"
    assert e2e._window_of(on_the_wire(ranged)) == "2025-07-01..2025-07-31"
