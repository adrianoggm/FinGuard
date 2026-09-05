"""The seam records a decision per mode, and the default policy leaves every existing test alone."""

from support.visual_planners import VERTICAL_DRAFT as VERTICAL
from support.visual_planners import ScriptedVisualPlanner
from test_visualization_stage1 import _result  # the existing result builder

from finguard_orchestrator.application.queries.ask_financial_question import (
    VisualRow,
    _decided_visual,
)
from finguard_orchestrator.domain.models.chart import DEFAULT_PRESENTATION
from finguard_orchestrator.domain.services.deterministic_visual_planner import (
    DeterministicVisualPlanner,
)
from finguard_orchestrator.domain.services.visual_plan_verifier import verify
from finguard_orchestrator.domain.services.visual_planning_policy import VisualPlanningPolicy
from finguard_orchestrator.observability.decisions import Step, Verdict, recording_turn

# Two rows, a measure and a nominal dimension -- the same shape `BREAKDOWN_PROFILE` (in
# `support.visual_planners`) draws, and the one `VERTICAL_DRAFT` is a valid, differently-oriented
# proposal against. `_result`'s own `rows` parameter has no default, so every seam test states it.
_ROWS = (VisualRow("ALQUILER", 51_300), VisualRow("OCIO", 4_000))


def _policy(model, mode):
    return VisualPlanningPolicy(
        model=model,
        fallback=DeterministicVisualPlanner(),
        verifier=verify,
        mode=mode,
        budget_seconds=1.0,
    )


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


async def test_the_question_never_enters_the_record() -> None:
    result = await _result(_ROWS)
    with recording_turn() as turn:
        await _decided_visual(
            result,
            _policy(ScriptedVisualPlanner(VERTICAL), "shadow"),
            DEFAULT_PRESENTATION,
            "gasté 734291 en Mercadona",
        )
    for decision in turn.sealed().where(Step.VISUAL_PLANNER):
        for slot in (decision.field, decision.was, decision.now, decision.detail):
            assert slot is None or ("734291" not in slot and "Mercadona" not in slot)
