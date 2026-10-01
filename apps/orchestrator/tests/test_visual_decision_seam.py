"""The seam records a decision per mode, and the default policy leaves every existing test alone."""

import logging

import pytest
from support.visual_planners import BREAKDOWN_PROFILE, ScriptedVisualPlanner
from support.visual_planners import VERTICAL_DRAFT as VERTICAL
from test_logging import Recording
from test_visualization_stage1 import _result  # the existing result builder

from finguard_orchestrator.application.queries.ask_financial_question import (
    VisualRow,
    _decided_visual,
    record_visual_decision,
    record_visual_decision_refused,
)
from finguard_orchestrator.domain.models.chart import DEFAULT_PRESENTATION, VisualContext
from finguard_orchestrator.domain.models.visual_plan import (
    ProposedEncoding,
    ProposedSort,
    Rejection,
    VisualMode,
    VisualPlanDraft,
)
from finguard_orchestrator.domain.ports.visual_planner import VisualPlannerPort
from finguard_orchestrator.domain.services.deterministic_visual_planner import (
    DeterministicVisualPlanner,
    VisualDecision,
)
from finguard_orchestrator.domain.services.visual_plan_verifier import verify
from finguard_orchestrator.domain.services.visual_planning_policy import VisualPlanningPolicy
from finguard_orchestrator.observability.decisions import Step, Verdict, recording_turn

SPEC = DeterministicVisualPlanner().plan(BREAKDOWN_PROFILE, DEFAULT_PRESENTATION)
assert SPEC is not None

# Two rows, a measure and a nominal dimension -- the same shape `BREAKDOWN_PROFILE` (in
# `support.visual_planners`) draws, and the one `VERTICAL_DRAFT` is a valid, differently-oriented
# proposal against. `_result`'s own `rows` parameter has no default, so every seam test states it.
_ROWS = (VisualRow("ALQUILER", 51_300), VisualRow("OCIO", 4_000))

# The floor's own canonical form for `_result(_ROWS)`'s profile (default `group_by="CATEGORY"`):
# `y/dimension/nominal descending value_minor`, `x/value_minor/quantitative`, plus the two tooltip
# encodings -- confirmed against `DeterministicVisualPlanner.plan()` directly, not guessed. A draft
# built to match it lets `test_a_proposal_identical_to_the_floor_records_shadow_same` reach
# `shadow:same` without loosening `canonical()`'s comparison.
IDENTICAL_DRAFT = VisualPlanDraft(
    "bar",
    (
        ProposedEncoding("y", "dimension", "nominal", ProposedSort("descending", "value_minor")),
        ProposedEncoding("x", "value_minor", "quantitative"),
        ProposedEncoding("tooltip", "dimension", "nominal"),
        ProposedEncoding("tooltip", "value_minor", "quantitative"),
    ),
)
# Names a field the profile does not hold -- rejected by `visual_plan_verifier._fields`, which is
# the first rule that can fire and names itself `"fields"` in the `Rejection`.
UNKNOWN_FIELD_DRAFT = VisualPlanDraft(
    "bar",
    (
        ProposedEncoding("x", "not_a_real_field", "nominal"),
        ProposedEncoding("y", "value_minor", "quantitative"),
    ),
)


def _policy(model: VisualPlannerPort | None, mode: VisualMode) -> VisualPlanningPolicy:
    return VisualPlanningPolicy(
        model=model,
        fallback=DeterministicVisualPlanner(),
        verifier=verify,
        mode=mode,
        budget_seconds=1.0,
    )


def _assert_agrees_with_its_own_values(decision) -> None:
    """`changed` is `was != now` whenever both slots are present.

    `test_decisions.py`'s own invariant test sweeps only the three records it writes itself
    (finding 3) -- nothing swept the seam's five rows, so every test below that gets a decision
    checks it here instead.
    """
    if decision.was is not None and decision.now is not None:
        assert decision.changed is (decision.was != decision.now)


async def test_deterministic_records_that_the_model_never_ran() -> None:
    """The production default: no model wired, so the floor is shown and nothing was tried."""
    result = await _result(_ROWS)
    with recording_turn() as turn:
        await _decided_visual(result, _policy(None, "deterministic"), DEFAULT_PRESENTATION, "q")
    (decision,) = turn.sealed().where(Step.VISUAL_PLANNER)
    assert decision.verdict is Verdict.AGREED and decision.changed is False
    assert decision.was is None and decision.now == "deterministic"
    assert decision.detail == "not_run"
    _assert_agrees_with_its_own_values(decision)


async def test_shadow_records_agreed_with_the_comparison_in_detail() -> None:
    # Built outside the turn: `_result` itself goes through `_decided_visual` (with the handler's
    # own default policy) to give every other test in `test_visualization_stage1.py` a Result with
    # a chart already on it, and that pass would record its own `not_run` decision onto this turn
    # too if it ran inside the `with` block below -- leaving two decisions where the assertion
    # below expects the one this test is actually driving.
    result = await _result(_ROWS)
    with recording_turn() as turn:
        decided = await _decided_visual(
            result,
            _policy(ScriptedVisualPlanner(VERTICAL), "shadow"),
            DEFAULT_PRESENTATION,
            "¿en qué gasto más?",
        )
    (decision,) = turn.sealed().where(Step.VISUAL_PLANNER)
    assert decision.verdict is Verdict.AGREED and decision.changed is False
    assert decision.now == "deterministic" and decision.detail == "shadow:changed"
    assert decided.visual_decision is not None and decided.visual_decision.accepted is False
    _assert_agrees_with_its_own_values(decision)


async def test_a_proposal_identical_to_the_floor_records_shadow_same() -> None:
    """A verified proposal that draws the same chart as the floor: `changed_from_baseline` is
    False, and `detail` says so rather than leaving `shadow:changed` to mean both cases."""
    result = await _result(_ROWS)
    with recording_turn() as turn:
        await _decided_visual(
            result,
            _policy(ScriptedVisualPlanner(IDENTICAL_DRAFT), "shadow"),
            DEFAULT_PRESENTATION,
            "q",
        )
    (decision,) = turn.sealed().where(Step.VISUAL_PLANNER)
    assert decision.verdict is Verdict.AGREED and decision.changed is False
    assert decision.detail == "shadow:same"
    _assert_agrees_with_its_own_values(decision)


async def test_hybrid_records_the_override() -> None:
    result = await _result(_ROWS)
    with recording_turn() as turn:
        await _decided_visual(
            result,
            _policy(ScriptedVisualPlanner(VERTICAL), "hybrid"),
            DEFAULT_PRESENTATION,
            "q",
        )
    (decision,) = turn.sealed().where(Step.VISUAL_PLANNER)
    assert decision.verdict is Verdict.OVERRODE
    assert decision.changed is True and decision.now == "llm"
    _assert_agrees_with_its_own_values(decision)


async def test_a_failure_records_failed_with_the_reason() -> None:
    result = await _result(_ROWS)
    with recording_turn() as turn:
        await _decided_visual(
            result,
            _policy(ScriptedVisualPlanner(None), "hybrid"),
            DEFAULT_PRESENTATION,
            "q",
        )
    (decision,) = turn.sealed().where(Step.VISUAL_PLANNER)
    assert decision.verdict is Verdict.FAILED and decision.detail == "abstention"
    _assert_agrees_with_its_own_values(decision)


async def test_a_rejected_proposal_records_the_rule_and_not_the_bare_reason() -> None:
    """`invalid_proposal` alone would say a rejection happened; the rule is what a rejection rate
    read per rule needs, and it is what the plain `fallback_reason` would otherwise hide."""
    result = await _result(_ROWS)
    with recording_turn() as turn:
        await _decided_visual(
            result,
            _policy(ScriptedVisualPlanner(UNKNOWN_FIELD_DRAFT), "hybrid"),
            DEFAULT_PRESENTATION,
            "q",
        )
    (decision,) = turn.sealed().where(Step.VISUAL_PLANNER)
    assert decision.verdict is Verdict.FAILED
    assert decision.detail == "invalid_proposal:fields"
    _assert_agrees_with_its_own_values(decision)


async def test_a_bug_in_the_adapter_records_provider_error_with_its_own_detail() -> None:
    """U-8 (Wave 3 hardening): a `TypeError` from the model adapter -- not `ProviderFailedError`
    or a timeout, the port's own two named failures -- still records `FallbackReason`'s closed
    `provider_error` (the wire seals four, never five), but `detail` says `code_error:TypeError`
    rather than the bare `provider_error` a real provider outage gets -- the same "reason plus
    which one" shape `test_a_rejected_proposal_records_the_rule_and_not_the_bare_reason` already
    gives `invalid_proposal`."""
    result = await _result(_ROWS)
    with recording_turn() as turn:
        await _decided_visual(
            result,
            _policy(ScriptedVisualPlanner(TypeError("boom")), "hybrid"),
            DEFAULT_PRESENTATION,
            "q",
        )
    (decision,) = turn.sealed().where(Step.VISUAL_PLANNER)
    assert decision.verdict is Verdict.FAILED
    assert decision.detail == "code_error:TypeError"
    _assert_agrees_with_its_own_values(decision)


async def test_a_kpi_never_enters_the_policy() -> None:
    """One row is a KPI, not a chart -- Section G names this exactly: a planner that decorates a
    KPI fails quietly and no validity number would show it, so the guard has to stop it here."""
    result = await _result((VisualRow("total", 1234),))
    with recording_turn() as turn:
        await _decided_visual(
            result,
            _policy(ScriptedVisualPlanner(VERTICAL), "shadow"),
            DEFAULT_PRESENTATION,
            "q",
        )
    assert turn.sealed().where(Step.VISUAL_PLANNER) == ()


async def test_the_question_never_enters_the_record() -> None:
    result = await _result(_ROWS)
    planner = ScriptedVisualPlanner(VERTICAL)
    question = "gasté 734291 en Mercadona"

    recorder = Recording()
    root = logging.getLogger()
    root.addHandler(recorder)
    previous_level = root.level
    root.setLevel(logging.INFO)
    try:
        with recording_turn() as turn:
            await _decided_visual(
                result, _policy(planner, "shadow"), DEFAULT_PRESENTATION, question
            )
    finally:
        root.removeHandler(recorder)
        root.setLevel(previous_level)

    # The positive half: the model is the one place the question goes. Without it, replacing
    # `policy.decide(question, ...)` with `policy.decide("", ...)` would fail nothing here.
    assert planner.received[0][0] == question, "the model is the one place it goes"

    decisions = turn.sealed().where(Step.VISUAL_PLANNER)
    assert decisions, "the sweep would pass an empty record without noticing"
    for decision in decisions:
        for slot in (decision.field, decision.was, decision.now, decision.detail):
            assert slot is None or ("734291" not in slot and "Mercadona" not in slot)

    # The wider of the two sinks: `record_visual_decision`'s `logger.info` has nine free-form keys
    # and no redaction of any kind -- `JsonLines` copies `extra` verbatim, and nothing else in this
    # task inspects it.
    emitted = "\n".join(recorder.lines)
    assert "734291" not in emitted and "Mercadona" not in emitted


def test_a_patch_records_agreed_and_a_refusal_records_declined() -> None:
    """M4 (fix round 2): the two record shapes `record_visual_decision`/
    `record_visual_decision_refused` produce, exercised beside every other seam's rows --
    `test_visualization_stage2_core.py` was, until now, the only place either was ever read back.
    I3's own invariant (`changed is (was != now)`) is asserted here too: a `patch` row *did*
    change what was shown, and a refused one did not.

    `context`'s own `decision` is `None` here (F3, fix wave 2): `declined.now` reports that
    honestly rather than the fabricated `"deterministic"` this used to assert -- the same
    unrecorded-origin argument `record_visual_decision`'s own `was` already makes.
    """
    context = VisualContext("chart-1", SPEC, BREAKDOWN_PROFILE)
    patch_decision = VisualDecision(
        spec=SPEC, profile=BREAKDOWN_PROFILE, source="patch", accepted=True, valid=True
    )
    with recording_turn() as turn:
        record_visual_decision(patch_decision, was="llm")
        record_visual_decision_refused(context, Rejection("order", "ordering does not apply"))
    accepted, declined = turn.sealed().where(Step.VISUAL_PLANNER)
    assert (accepted.verdict, accepted.was, accepted.now, accepted.detail) == (
        Verdict.AGREED,
        "llm",
        "patch",
        "accepted",
    )
    assert accepted.changed is True
    assert (declined.verdict, declined.now, declined.detail) == (
        Verdict.DECLINED,
        None,
        "invalid_proposal:order",
    )
    assert declined.changed is False


async def test_a_patch_decision_never_enters_the_promotion_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """I4 (fix round 2): `record_visual_decision`'s own first line reads "One `record()` per turn
    that entered the policy" -- a restyle never enters the policy, so its `patch` decision must
    not reach `visual_planner.decision`, the promotion-evidence sink Task 9's acceptance/
    abstention/latency rates are read off. A deterministic decision (the seam's own default,
    tested here for contrast) still emits exactly one.
    """
    with caplog.at_level(logging.INFO):
        with recording_turn():
            record_visual_decision(
                VisualDecision(
                    spec=SPEC, profile=BREAKDOWN_PROFILE, source="patch", accepted=True, valid=True
                ),
                was="deterministic",
            )
    assert not [r for r in caplog.records if r.getMessage() == "visual_planner.decision"]

    caplog.clear()
    deterministic_decision = VisualDecision(
        spec=SPEC, profile=BREAKDOWN_PROFILE, source="deterministic"
    )
    with caplog.at_level(logging.INFO):
        with recording_turn():
            record_visual_decision(deterministic_decision)
    promotion_logs = [r for r in caplog.records if r.getMessage() == "visual_planner.decision"]
    assert len(promotion_logs) == 1
