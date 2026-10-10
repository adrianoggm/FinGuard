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
import json
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


# --- a run that measured nothing is a failure -----------------------------------------
#
# The defect this half of the file exists for. On 2026-08-28 the three read sets reported 0
# violations over 235 cases and not one case had reached an answering outcome: every property in
# `violations` is guarded on an answer existing, so a set of clarifications cannot break any of
# them. Zero violations and zero answers printed identically and the suite stayed green for a week.


def test_a_set_where_nothing_answered_did_not_run() -> None:
    """The exact shape of the vacuous pass: 235 cases, 0 violations, 0 answers."""
    empty = e2e.vacuity("dev", answered=0, figures=0, expected=102)

    assert empty, "0 answers out of 102 must not read as a pass"


def test_the_floor_is_a_fraction_and_not_a_zero_check() -> None:
    """One answer out of 102 is nearly as blind as none, so the guard cannot be `> 0`."""
    assert e2e.vacuity("dev", answered=1, figures=1, expected=102)
    assert e2e.vacuity("dev", answered=50, figures=50, expected=102)
    assert e2e.vacuity("dev", answered=51, figures=51, expected=102) == []


def test_the_floor_sits_below_the_worst_model_ever_measured() -> None:
    """`../evaluation/planning/RESULTS.md` puts the shipped model at 95.8 / 87.3 / 77.8 outcome
    accuracy and the worst of its eleven models at 50.0 %. The guard must clear none of those, or it
    is an accuracy gate wearing a vacuity guard's name and will be ignored the first time it flakes.
    """
    for expected, worst in ((102, 0.778), (66, 0.778), (17, 0.778)):
        assert e2e.vacuity("set", int(expected * worst), int(expected * worst), expected) == []


def test_answers_with_no_figures_are_silence_one_field_lower() -> None:
    """An answering outcome carrying no figure passes every property in `violations` for want of
    anything to check. A whole set of them is the same hole, so it is the second prong."""
    assert e2e.vacuity("dev", answered=102, figures=0, expected=102)


def test_a_set_that_requires_no_answer_cannot_measure_anything() -> None:
    """Otherwise the floor is 0 and the guard is unfireable — the disease, not the cure."""
    assert e2e.vacuity("dev", answered=0, figures=0, expected=0)


def test_an_answer_with_no_figure_is_a_violation() -> None:
    """The mirror of `FIGURE_WITHOUT_AN_ANSWER`, and the per-case half of the vacuity guard. Every
    figure-integrity property in `violations` is guarded on `total_minor` existing, so an answering
    outcome without one passed all of them for want of anything to check."""
    assert "ANSWER_WITHOUT_A_FIGURE" in e2e.violations(answered(total_minor=None))


def test_a_balance_with_no_figure_is_the_same_silence() -> None:
    """`_ANSWERING` and not PLANNED alone, because a BALANCE arrives with the figure it is about
    and the rule this mirrors is keyed the same way."""
    body = answered(outcome="BALANCE", text="Tienes 10.370,42 € en total.", total_minor=None)

    assert "ANSWER_WITHOUT_A_FIGURE" in e2e.violations(body)


def test_an_answer_that_carries_its_figure_passes() -> None:
    """The rule may not fire on the ordinary case, or it is 175 false positives a run."""
    assert e2e.violations(answered()) == []
    assert "ANSWER_WITHOUT_A_FIGURE" not in e2e.violations(answered(outcome="UNSUPPORTED"))


def test_a_group_with_no_figure_is_reported_and_does_not_crash() -> None:
    """It used to reach `sum(g["total_minor"] ...)` and raise `KeyError`, taking the run with it: a
    data problem became a stack trace, and the run still measured nothing. A breakdown this cannot
    add up is what gate 1's *Evidence reconciliation* row claims to check, so it is reported."""
    missing = answered(
        groups=[
            {"key": "GROCERIES", "total_minor": 30000, "movement_count": 5},
            {"key": "HEALTH", "movement_count": 2},
        ],
    )

    assert e2e.violations(missing) == ["GROUP_WITHOUT_A_FIGURE"]


def test_a_group_whose_figure_is_null_or_prose_is_the_same_fault() -> None:
    """`None` and a string crash the sum identically, and neither is a number to add up."""
    for broken_value in (None, "30.000,00 €"):
        body = answered(
            groups=[
                {"key": "GROCERIES", "total_minor": broken_value, "movement_count": 5},
                {"key": "HEALTH", "total_minor": 4000, "movement_count": 2},
            ],
        )

        assert e2e.violations(body) == ["GROUP_WITHOUT_A_FIGURE"], broken_value


# --- what "the whole" is, when the rows are not parts of one ---------------------------
#
# `_reconciliation` asked one question — do the parts add up to the whole? — of every breakdown, and
# three shapes reach it. On a comparison the rows are two operands and the figure is their signed
# difference; on a running total each row already contains the ones before it. Both were reported as
# arithmetic failures, and both payloads were internally coherent.
#
# The narrowing is keyed on a field the service declares, never on an inference. `difference_minor`
# says the rows are operands; `metric` says they are running totals. Every branch still reconciles
# something.


def comparison(**over: object) -> dict[str, object]:
    """`dev/unsupported_comparison` as the wire spells it now — the payload reported for `64b6b3b`.

    «¿He gastado más este mes que el anterior?». `total_minor` is None and the figure is the signed
    difference; the two rows are the operands, and their counts still sum to `movement_count`.
    """
    return (
        answered(
            text="En lo que va de mes: 0,00 €; el mes pasado: 1.895,97 €. Fue 1.895,97 € mayor.",
            plan="en lo que va de mes, expense frente a el mes pasado, expense",
            total_minor=None,
            movement_count=28,
            groups=[
                {"key": "en lo que va de mes", "total_minor": 0, "movement_count": 0},
                {"key": "el mes pasado", "total_minor": 189597, "movement_count": 28},
            ],
        )
        | over
        | {"difference_minor": over.get("difference_minor", -189597)}
    )


def test_a_comparison_is_not_a_breakdown_that_failed_to_add_up() -> None:
    """The violation this file reported for a week. `sum(groups.total_minor)` is 189597 and the
    figure is -189597: a total against a subtraction, and both halves were right."""
    broken = e2e.violations(comparison())

    assert "GROUPS_DO_NOT_SUM_TO_TOTAL" not in broken
    assert broken == []


def test_a_comparison_still_has_to_get_its_counts_right() -> None:
    """The whole reason keying on the tag is acceptable: the property narrows to the half that still
    holds. If it went quiet on the answer instead, the tag would be an exemption."""
    miscounted = comparison(movement_count=27)

    assert "GROUP_COUNTS_DO_NOT_SUM" in e2e.violations(miscounted)


def test_a_comparison_is_still_counted_as_a_breakdown_inspected() -> None:
    """Gate 1's *Evidence reconciliation* row is a fraction, and dropping these answers out of it
    would shrink the denominator while removing the failure from both halves — the vacuous zero one
    field lower, which is what made shape 2 the wrong choice."""
    assert e2e.reconcilable(comparison())


def test_a_comparison_carries_its_figure_under_another_name() -> None:
    """`total_minor` is None on a comparison, so a rule keyed on that field reads it as an answer
    with no figure. That would turn a closed hole into a false positive on every comparison."""
    assert "ANSWER_WITHOUT_A_FIGURE" not in e2e.violations(comparison())
    assert e2e._has_figure(comparison())


def test_the_tag_does_not_imply_the_total_is_missing() -> None:
    """NET_CASH_FLOW carries both fields and there `total_minor` legitimately *is* the difference,
    so the narrowing may not be keyed on `total_minor is None`. The rows are still operands."""
    net = comparison(total_minor=-189597)

    assert e2e.violations(net) == []


def test_a_declined_answer_may_not_carry_a_difference_either() -> None:
    """`FIGURE_WITHOUT_AN_ANSWER` asks the same question the same way, or the two rules disagree
    about the same payload."""
    refused = comparison(outcome="UNSUPPORTED", text="Eso no puedo calcularlo todavía.")

    assert "FIGURE_WITHOUT_AN_ANSWER" in e2e.violations(refused)


def cumulative(**over: object) -> dict[str, object]:
    """The rows `test_cumulative_and_group_rankings.py` pins, on the wire.

    A running total per month, with February and April carried forward because nothing happened in
    them. Neither the amounts (50_000) nor the counts (10) sum to the whole, correctly.
    """
    return (
        answered(
            last_plan={"direction": "EXPENSE", "period": "THIS_YEAR", "metric": "CUMULATIVE"},
            text="Gasto acumulado: 150,00 € al acabar abril.",
            total_minor=15_000,
            movement_count=3,
            groups=[
                {"key": "2026-01", "total_minor": 10_000, "movement_count": 2},
                {"key": "2026-02", "total_minor": 10_000, "movement_count": 2},
                {"key": "2026-03", "total_minor": 15_000, "movement_count": 3},
                {"key": "2026-04", "total_minor": 15_000, "movement_count": 3},
            ],
        )
        | over
    )


def test_a_running_total_is_not_a_breakdown_that_failed_to_add_up() -> None:
    """Both halves of the old rule fired on this, both falsely: 50_000 != 15_000 and 10 != 3. This
    is not a comparison special case — the property had no notion of shape at all."""
    broken = e2e.violations(cumulative())

    assert "GROUPS_DO_NOT_SUM_TO_TOTAL" not in broken
    assert "GROUP_COUNTS_DO_NOT_SUM" not in broken
    assert broken == []


def test_a_running_total_must_still_reach_its_own_figure() -> None:
    """The cumulative form of "the parts add up to the whole" is that the last part *is* the whole.
    A skip would have been the silent pass; this is a reconciliation."""
    short = cumulative(total_minor=20_000)

    assert "CUMULATIVE_DOES_NOT_REACH_TOTAL" in e2e.violations(short)


def test_a_running_total_must_reach_its_count_too() -> None:
    assert "CUMULATIVE_COUNTS_DO_NOT_REACH_TOTAL" in e2e.violations(cumulative(movement_count=4))


def test_a_running_total_is_still_a_breakdown_inspected() -> None:
    assert e2e.reconcilable(cumulative())


def test_an_ordinary_breakdown_still_has_to_add_up_in_both_units() -> None:
    """The narrowings are keyed on declared fields, so an ordinary grouped answer — no
    `difference_minor`, `metric: SUM` — is checked exactly as it always was."""
    body = answered(
        groups=[
            {"key": "GROCERIES", "total_minor": 30000, "movement_count": 5},
            {"key": "HEALTH", "total_minor": 4000, "movement_count": 2},
        ],
    )

    broken = e2e.violations(body)

    assert "GROUPS_DO_NOT_SUM_TO_TOTAL" in broken
    assert "GROUP_COUNTS_DO_NOT_SUM" in broken


def test_an_ungrouped_answer_is_not_a_reconciled_one() -> None:
    """Gate 1's *Evidence reconciliation* row cannot be filled from a violation count: no failures
    over no inspected breakdowns is the vacuous zero again. `reconcilable` is what makes the
    difference visible."""
    assert not e2e.reconcilable(answered(groups=[]))
    assert not e2e.reconcilable(answered(groups=[{"key": "total", "total_minor": 1, "count": 1}]))
    assert e2e.reconcilable(
        answered(groups=[{"key": "GROCERIES", "total_minor": 1, "movement_count": 1}])
    )


# --- the account scope, settled in the harness ----------------------------------------


def test_the_settled_opening_is_the_one_the_corpus_uses() -> None:
    """One idea of "scope already settled" in the repository rather than two. `opening()` in
    `tests/support/episodes.py` is the other place it appears, and they must not drift."""
    from support import episodes

    settled = episodes.Episode(name="n", why="w", turns=(), scope=episodes.SETTLED)

    assert e2e.SETTLED == episodes.opening(settled)


def test_only_plan_expecting_cases_walk_the_real_path() -> None:
    """An `unsupported` sentence is refused before any account matters and a `clarification` case is
    already about a different missing slot, so neither can reach the account question."""
    cases = [{"id": f"c{n}", "expect": "plan"} for n in range(24)]
    cases += [{"id": "u1", "expect": "unsupported"}, {"id": "q1", "expect": "clarification"}]

    walking = e2e.unsettled(cases)

    assert walking == {"c0", "c8", "c16"}
    assert not walking & {"u1", "q1"}


def test_every_read_case_declares_an_expectation_the_floor_can_count() -> None:
    """The vacuity floor is a fraction of the cases that say `expect: plan`, so a mistyped `expect`
    silently shrinks the denominator and weakens the guard. That is the same disease in the cure,
    and this is the only thing standing between `expct:` and a floor of zero."""
    import yaml

    for name in ("dev", "validation", "adversarial"):
        cases = yaml.safe_load((e2e.PLANNING / f"{name}.yaml").read_text(encoding="utf-8"))["cases"]
        for case in cases:
            assert case.get("expect") in e2e.EXPECTATIONS, f"{name}/{case.get('id')}: {case}"
        assert e2e.expecting_a_plan(cases), f"{name} requires no answer of anything"


def test_no_limit_and_a_limit_of_nothing_are_different_requests() -> None:
    """`if limit` read `--limit 0` as *no limit* and ran the whole corpus, which cost a session a
    contended run. The honest answer to a request for no cases is no cases."""
    cases = [{"id": f"c{n}"} for n in range(5)]

    assert e2e.sliced(cases, None) == cases
    assert e2e.sliced(cases, 0) == []
    assert e2e.sliced(cases, 2) == cases[:2]


# --- a corpus run that measured nothing is a failure too -------------------------------
#
# The read sets got the vacuity guard and this path did not, in the same file. An empty corpus — or
# one emptied by `--limit 0` — printed `PROPERTY VIOLATIONS 0` and exited 0. Every property in
# `turn_violations` needs a turn to have happened before it can fail, exactly as every property in
# `violations` needs an answer.


def test_a_corpus_that_replayed_nothing_did_not_run() -> None:
    assert e2e.corpus_vacuity(threads=0, declared=0, replayed=0)


def test_a_corpus_emptied_by_a_zero_limit_did_not_run() -> None:
    """The interlock between the two holes: `--limit 0` now yields no threads, and no threads is
    reported as a run that did not happen rather than as a clean sheet."""
    assert e2e.corpus_vacuity(threads=0, declared=0, replayed=0)


def test_threads_that_declare_no_turns_measure_nothing() -> None:
    assert e2e.corpus_vacuity(threads=3, declared=0, replayed=0)


def test_a_corpus_whose_threads_broke_early_did_not_run() -> None:
    """A thread that breaks on a transport error stops replaying, and its remaining turns are never
    asked. Enough of those and the violation count is computed over a corpus that mostly did not
    happen."""
    assert e2e.corpus_vacuity(threads=20, declared=100, replayed=10)
    assert e2e.corpus_vacuity(threads=20, declared=100, replayed=49)


def test_a_corpus_that_replayed_its_turns_is_a_measurement() -> None:
    """The guard is not an accuracy floor: a turn that ran and violated three properties still
    counts as replayed, so a fully replayed corpus is never called vacuous."""
    assert e2e.corpus_vacuity(threads=20, declared=100, replayed=100) == []
    assert e2e.corpus_vacuity(threads=20, declared=100, replayed=50) == []


def test_the_corpus_floor_is_the_one_the_read_sets_use() -> None:
    """One threshold in this file rather than two: both ask the same question — did enough of this
    happen to be worth a count — and two numbers would be two things to justify and one to forget.
    """
    declared = 100
    below = int(declared * e2e.ANSWERING_FLOOR) - 1

    assert e2e.corpus_vacuity(threads=20, declared=declared, replayed=below)


def test_the_smallest_set_still_gets_two_real_cases() -> None:
    """The stride is derived from adversarial's 17 plan-expecting cases: one case is not coverage
    because a single flake leaves the path unmeasured, and 17 // 2 = 8 is what survives that."""
    adversarial = [{"id": f"a{n}", "expect": "plan"} for n in range(17)]

    assert len(e2e.unsettled(adversarial)) >= 2


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


def test_a_demanded_shape_is_not_satisfied_by_a_clarification() -> None:
    """The asymmetry `expect_shape` had with its two siblings. `expect_plan` and `must_change` both
    say `NOT_PLANNED_SO_NO_<field>` when there is no plan to look at; this one folded
    `outcome == "PLANNED"` into its condition, so a turn demanding `GROUPS` that came back as a
    question asserted nothing and passed."""
    asked = {"say": "...", "expect_shape": "GROUPS"}

    broken = e2e.turn_violations(asked, asking("WINDOW"), False, {})

    assert "NOT_PLANNED_SO_NO_SHAPE" in broken


def test_a_server_managed_clarification_needs_a_versioned_thread_not_a_client_draft() -> None:
    body = asking("WINDOW") | {"conversation": {"conversation_id": "conversation-1", "version": 3}}

    assert "QUESTION_WITHOUT_A_DRAFT" not in e2e.turn_violations(
        {"say": "next month"}, body, True, {}
    )


def test_a_clarification_without_a_draft_or_server_thread_is_still_a_violation() -> None:
    assert "QUESTION_WITHOUT_A_DRAFT" in e2e.turn_violations(
        {"say": "next month"}, asking("WINDOW"), True, {}
    )


def test_graph_payment_continuity_can_be_server_managed() -> None:
    assert e2e.has_server_managed_state({"conversation_id": "conversation-1", "version": 3})
    assert not e2e.has_server_managed_state({"version": 3})


def test_graph_plan_context_keeps_window_assertions_measurable_without_client_state() -> None:
    body = answered(
        conversation={"conversation_id": "server-thread", "version": 2},
        plan_context={
            "direction": "EXPENSE",
            "period": "THIS_YEAR",
            "metric": "SUM",
            "group_by": "NONE",
        },
    )
    turn = {"say": "este año", "expect_plan": {"window": "THIS_YEAR"}}

    assert e2e.turn_violations(turn, body, False, {}) == []

    body["plan_context"]["period"] = "LAST_N_MONTHS"
    body["plan_context"]["n"] = 12

    assert "WRONG_WINDOW" in e2e.turn_violations(turn, body, False, {})


def test_the_three_plan_rules_agree_about_an_answer_that_never_came() -> None:
    """The point of the fix is the symmetry, so it is the symmetry that is asserted."""
    declined = asking("WINDOW")

    for key, asked in (
        ("SHAPE", {"say": "...", "expect_shape": "GROUPS"}),
        ("WINDOW", {"say": "...", "expect_plan": {"window": "THIS_MONTH"}}),
        ("WINDOW", {"say": "...", "must_change": ["window"]}),
    ):
        broken = e2e.turn_violations(asked, declined, False, plan())

        assert f"NOT_PLANNED_SO_NO_{key}" in broken, asked


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


# --- the preceding turn fifteen cases declare and never got ----------------------------
#
# 9 dev cases and 6 validation cases carry a `previous:` plan, and `run()` never sent it. «¿Y en
# gasolina?» was asked with no window, «¿Y este mes?» with no category to keep. Whatever those
# fifteen expectations scored, it was not what they say they measure.


def test_a_case_that_declares_no_preceding_turn_gets_none() -> None:
    assert e2e.preceding({"id": "c", "question": "¿Cuánto gasté hoy?"}) is None


def test_the_declared_preceding_turn_reaches_the_wire_as_a_plan() -> None:
    """The dataset writes `groupBy` and a nested `period`; the wire carries flat snake_case. The
    translation is the other benchmark's own reader plus the endpoint's own writer, so there is no
    third spelling of a plan to drift."""
    case = {
        "id": "follow_grouping_after_period",
        "previous": {"period": {"period": "LAST_MONTH"}, "direction": "EXPENSE"},
    }

    context = e2e.preceding(case)

    assert context is not None
    assert context["period"] == "LAST_MONTH"
    assert context["direction"] == "EXPENSE"
    assert context["group_by"] == "NONE"


def test_the_wire_plan_is_the_one_the_endpoint_would_have_written() -> None:
    """The gap that would let the harness send a plan the service reads differently. Both halves
    are tested in isolation and neither knows about the other, so this maps the same plan by both
    routes and asserts they meet — the same argument as the window-spelling test below."""
    from finguard_orchestrator.domain.enums import Direction, GroupBy, Period
    from finguard_orchestrator.domain.value_objects.financial_plan import (
        FinancialPlan,
        RelativePeriod,
    )
    from finguard_orchestrator.infrastructure.mappers import conversation_mapper

    spec = {
        "period": {"period": "LAST_MONTH"},
        "direction": "EXPENSE",
        "categories": ["TRANSPORT_FUEL"],
        "groupBy": "CATEGORY",
    }
    built = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(period=Period.LAST_MONTH),
        categories=("TRANSPORT_FUEL",),
        group_by=GroupBy.CATEGORY,
    )

    assert e2e.preceding({"id": "c", "previous": spec}) == conversation_mapper.plan_payload(
        built
    ).model_dump(mode="json")


def test_a_previous_key_nothing_reads_stops_the_run() -> None:
    """`as_plan` reads the spec with `.get`, so a mistyped key is dropped without a word and the
    case is asked with less context than it declares — the same silent pass this file exists to
    close, one level down in the dataset."""
    import pytest

    with pytest.raises(SystemExit):
        e2e.preceding({"id": "c", "previous": {"direction": "EXPENSE", "groupby": "CATEGORY"}})


def test_every_previous_block_in_the_read_sets_is_one_the_harness_sends() -> None:
    """The three datasets against `PREVIOUS_KEYS`, the way `EXPECTATIONS` and `TURN_KEYS` are
    asserted against theirs. It also proves the fifteen specs build a plan the API would accept:
    a context this cannot build is one a client could not send either."""
    import yaml

    declared = 0
    for name in ("dev", "validation", "adversarial"):
        cases = yaml.safe_load((e2e.PLANNING / f"{name}.yaml").read_text(encoding="utf-8"))["cases"]
        for case in cases:
            if "previous" not in case:
                continue
            declared += 1
            assert e2e.preceding(case) is not None, f"{name}/{case['id']}"

    # Wave 3 hardening, Task 8b, item 5: dev.yaml gained a tenth `previous` block
    # (`follow_split_by_two_named_categories`, "the eighth handed-over row").
    assert declared == 16, f"the audit counted 10 dev and 6 validation, found {declared}"


def test_the_account_scope_and_the_preceding_turn_travel_together() -> None:
    """`ConversationPayload` says why they compose rather than conflict: `last_plan` is memory
    rather than a workflow, so it travels alongside whatever is active."""
    case = {"id": "c", "previous": {"period": {"period": "LAST_MONTH"}, "direction": "EXPENSE"}}

    opening = e2e.opening_for(case, real_path=False)

    assert opening is not None
    assert opening["account_selection_mode"] == "ALL"
    assert opening["last_plan"]["period"] == "LAST_MONTH"


def test_a_real_path_case_carries_its_context_without_a_settled_scope() -> None:
    """It has to answer the account question itself — that is the production path — and the plan it
    is following up on is not what that question is about. Two cases are both at once."""
    case = {"id": "c", "previous": {"period": {"period": "LAST_MONTH"}, "direction": "EXPENSE"}}

    opening = e2e.opening_for(case, real_path=True)

    assert opening is not None
    assert "account_selection_mode" not in opening
    assert opening["last_plan"]["period"] == "LAST_MONTH"


def test_a_plain_case_opens_exactly_as_it_did_before() -> None:
    """The fifteen are the only cases whose opening changes. 220 of the 235 must not move, or this
    repair is a different benchmark wearing the same name."""
    assert e2e.opening_for({"id": "c"}, real_path=False) == e2e.SETTLED
    assert e2e.opening_for({"id": "c"}, real_path=True) is None


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


def test_conversation_selection_is_stable_and_rejects_unknown_ids() -> None:
    cases = [{"id": "a"}, {"id": "b"}, {"id": "c"}]

    assert e2e.select_conversations(cases, ["c", "a"]) == [cases[0], cases[2]]
    assert e2e.select_conversations(cases, None) == cases
    try:
        e2e.select_conversations(cases, ["missing"])
    except ValueError as exc:
        assert "missing" in str(exc)
    else:
        raise AssertionError("unknown conversation ids must fail explicitly")


def test_conversation_evidence_keeps_plan_and_figures_but_not_raw_workflow_state() -> None:
    body = answered(
        text="El segundo fue de 72,50 EUR: Mercadona.",
        plan_context={"rank": 2, "period": "LAST_MONTH"},
        movements=[
            {
                "description": "Mercadona",
                "merchant_name": "Mercadona",
                "amount_minor": 7250,
                "direction": "EXPENSE",
                "occurred_at": "2026-09-12T10:00:00+02:00",
                "id": "movement-private-id",
            }
        ],
        conversation={
            "version": 4,
            "workflow": "PAYMENT_IN_PROGRESS",
            "payment": {"phone": "+34611111111", "candidate_ids": ["private-id"]},
        },
    )

    record = e2e.conversation_evidence("ranked_movement", 2, {"say": "¿Y el segundo?"}, body, [])
    serialized = json.dumps(record, ensure_ascii=False)

    assert record["plan_context"] == {"rank": 2, "period": "LAST_MONTH"}
    assert record["movements"][0]["merchant_name"] == "Mercadona"
    assert record["conversation"] == {
        "version": 4,
        "workflow": "PAYMENT_IN_PROGRESS",
        "has_draft": True,
        "has_pending_plan": False,
    }
    assert "movement-private-id" not in serialized
    assert "+34611111111" not in serialized
