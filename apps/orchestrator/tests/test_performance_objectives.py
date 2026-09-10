"""Design §8's five performance objectives, each named to a test or to the Wave 3 test that
already is one (ruling R11: no wall-clock threshold anywhere below).

- §8 a "a presentation-only change makes zero banking calls" --
  `test_a_presentation_only_restyle_makes_zero_banking_calls_and_no_policy_call` (both channels).
- §8 b "a deterministic presentation change is near-immediate -- no model call" --
  `test_a_charted_answer_asks_the_policy_once_and_the_default_policy_never_calls_a_model`.
- §8 c "the planner has a budget" -- Wave 3's own
  `tests/test_visual_planning_policy.py::test_a_non_positive_budget_is_refused` (the budget must
  be positive) and `::test_the_three_failures_are_told_apart` (the `(0.5, "timeout")` row,
  `budget=0.05`: the model is cut off at the budget). Nothing added here.
- §8 d "the floor shows on expiry" -- Wave 3's own
  `tests/test_visual_planning_policy.py::test_the_three_failures_are_told_apart`
  (`canonical(decision.spec) == canonical(floor)` on the timeout row) and
  `::test_promoted_pays_for_the_baseline_when_the_model_fails` (the floor is computed exactly when
  the model fails to supply one). Nothing added here.
- §8 e "adding the planner never delays the first visible sign of progress" --
  `test_the_first_frame_is_a_stage_and_arrives_before_the_policy_is_consulted`.

This module drives `llm_controller.ask_streaming` directly (the pattern
`tests/test_visual_follow_up.py` uses for the same route, `_WHO`/`_SEALED_CHARTED`-shaped) rather
than reusing `tests/test_question_routing.py`, whose own frame tests are about payment routing,
not the visual policy.
"""

from __future__ import annotations

import json
from dataclasses import replace
from uuid import UUID

import pytest
from support.visual_planners import BREAKDOWN_PROFILE
from test_decisions_call_sites import (
    ACCOUNT as CS_ACCOUNT,
)
from test_decisions_call_sites import (
    FIVE_MONTHS,
    STATES_WINDOW_AND_DIRECTION,
    FakeAccounts,
    FakeContacts,
    FakeLedger,
    FakePlanner,
    FixedInterpreter,
    NoCards,
)
from test_decisions_call_sites import (
    TODAY as CS_TODAY,
)

from finguard_orchestrator.application.queries.ask_financial_question import (
    AskFinancialQuestionHandler,
)
from finguard_orchestrator.domain.enums import Direction, GroupBy, Period
from finguard_orchestrator.domain.models.chart import DEFAULT_PRESENTATION, VisualContext
from finguard_orchestrator.domain.models.conversation import Conversation
from finguard_orchestrator.domain.models.planning_result import PlanningResult
from finguard_orchestrator.domain.services.deterministic_visual_planner import (
    DeterministicVisualPlanner,
)
from finguard_orchestrator.domain.services.intent_router import Intent
from finguard_orchestrator.domain.services.visual_planning_policy import VisualPlanningPolicy
from finguard_orchestrator.domain.value_objects.caller import Caller
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan, RelativePeriod
from finguard_orchestrator.domain.value_objects.scope import Scope
from finguard_orchestrator.infrastructure.controllers.llm_controller import ask_streaming
from finguard_orchestrator.infrastructure.dto.llm_dto import QuestionRequest
from finguard_orchestrator.infrastructure.mappers import conversation_mapper
from finguard_orchestrator.infrastructure.security.metric_tokens import MetricTokenCodec
from finguard_orchestrator.observability import Step, recording_turn

_WHO = Caller(
    holder_reference="ES-0001", account_id=CS_ACCOUNT, scopes=frozenset({Scope.QUESTIONS_READ})
)
_TEST_TOKENS = MetricTokenCodec({"active": b"x" * 32}, "active")

# A sealed charted conversation -- the same shape `test_visual_follow_up._SEALED_CHARTED` builds,
# rebuilt locally rather than imported. This codebase does import public names across test modules
# (see `test_decisions_call_sites` above) -- what does not travel is anything underscore-private,
# `_SEALED_CHARTED` included, so a module that wants that shape re-types it rather than reaching
# into another module's private fixtures.
_SPEC = DeterministicVisualPlanner().plan(BREAKDOWN_PROFILE, DEFAULT_PRESENTATION)
assert _SPEC is not None
_PREVIOUS = FinancialPlan(
    direction=Direction.EXPENSE,
    period=RelativePeriod(Period.LAST_WEEK),
    group_by=GroupBy.CATEGORY,
    only_account_id=CS_ACCOUNT,
)
_CHARTED = Conversation(last_plan=_PREVIOUS).with_visual_context(
    VisualContext("chart-1", _SPEC, BREAKDOWN_PROFILE)
)
_SEALED_CHARTED = replace(
    _CHARTED,
    visual_contexts=tuple(
        conversation_mapper.seal_context(context, _WHO.holder_reference, _TEST_TOKENS)
        for context in _CHARTED.visual_contexts
    ),
)
_CONVERSATION_ID = UUID("44444444-4444-4444-4444-444444444444")


def _decide_spy(monkeypatch):
    """Patches `VisualPlanningPolicy.decide` on the class (every instance, including a handler's
    own default policy) to record every call while still running the real body."""
    calls: list[object] = []
    original = VisualPlanningPolicy.decide

    async def spy(self, *args: object, **kwargs: object) -> object:
        calls.append(self)
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(VisualPlanningPolicy, "decide", spy)
    return calls


async def _frames_of(streamed) -> list[dict]:
    chunks = [chunk async for chunk in streamed.body_iterator]
    body = "".join(chunk.decode() if isinstance(chunk, bytes) else chunk for chunk in chunks)
    return [
        json.loads(line.removeprefix("data: "))
        for line in body.splitlines()
        if line.startswith("data: ")
    ]


def _restyle_request(channel: str) -> QuestionRequest:
    payload = conversation_mapper.to_payload(_SEALED_CHARTED).model_copy(
        update={"conversation_id": _CONVERSATION_ID}
    )
    return QuestionRequest(question="Ponlo en vertical", conversation=payload, channel=channel)


def _charted_answer_handler() -> tuple[AskFinancialQuestionHandler, FakeLedger]:
    """A real turn that draws a chart -- `STATES_WINDOW_AND_DIRECTION` over `FIVE_MONTHS`, the
    exact pair `test_decisions_call_sites`'s own
    `test_visual_planner_records_the_deterministic_default_on_a_real_turn` already proves reaches
    the policy through a real handler with no `visual_policy` wired (the deterministic default
    `AskFinancialQuestionHandler.__init__` constructs)."""
    ledger = FakeLedger()
    planned = PlanningResult.planned(FinancialPlan(direction=Direction.INCOME, period=FIVE_MONTHS))
    handler = AskFinancialQuestionHandler(
        FakePlanner(planned),
        ledger,
        accounts=FakeAccounts(),
        contacts=FakeContacts(),
        cards=NoCards(),
    )
    return handler, ledger


async def _stream_charted_answer(channel: str):
    handler, ledger = _charted_answer_handler()
    request = QuestionRequest(question=STATES_WINDOW_AND_DIRECTION, channel=channel)
    streamed = await ask_streaming(
        request,
        _WHO,
        asking=handler,
        today=CS_TODAY,
        commands=None,
        payments_on=False,
        queries=None,
        attempt="x",
        reads=FixedInterpreter(Intent.FINANCIAL_QUERY),
        metric_tokens=_TEST_TOKENS,
        metrics_on=False,
    )
    frames = await _frames_of(streamed)
    return frames, ledger


# --- §8 a: a presentation-only change makes zero banking calls, on both channels --------------


@pytest.mark.parametrize("channel", ["TEXT", "VOICE"])
async def test_a_presentation_only_restyle_makes_zero_banking_calls_and_no_policy_call(
    channel, monkeypatch
) -> None:
    """The deterministic restyle path never reaches the handler at all -- `_visual_only_response`
    answers directly off `interpret_visual` -- so it never reaches the ledger and never reaches
    `VisualPlanningPolicy.decide`, on both TEXT and VOICE (Task 2 gave VOICE the same restyle
    path as TEXT). Made structural rather than incidental: the fake ledger records every plan it
    is asked to run, and the spy records every `decide` call, so either one firing at all fails
    this test regardless of the outcome the route returns."""
    calls = _decide_spy(monkeypatch)
    handler, ledger = _charted_answer_handler()
    request = _restyle_request(channel)

    streamed = await ask_streaming(
        request,
        _WHO,
        asking=handler,
        today=CS_TODAY,
        commands=None,
        payments_on=False,
        queries=None,
        attempt="x",
        reads=FixedInterpreter(Intent.FINANCIAL_QUERY),
        metric_tokens=_TEST_TOKENS,
        metrics_on=False,
    )
    frames = await _frames_of(streamed)

    assert frames[-1]["type"] == "answer"
    assert frames[-1]["outcome"] == "VISUAL_UPDATED"
    assert ledger.plans == []
    assert calls == []


# --- §8 b: a deterministic presentation change makes no model call ----------------------------


async def test_a_charted_answer_asks_the_policy_once_and_the_default_policy_never_calls_a_model(
    monkeypatch,
) -> None:
    """A charted ANSWER (a new financial question, not a restyle) reaches
    `VisualPlanningPolicy.decide` exactly once, and the default policy the handler built for
    itself (`model=None`) never constructs or calls a model planner port: `decision.proposed`
    stays `False` (`visual_planning_policy.py:74`, `self._mode == "deterministic" or
    self._model is None`, no `asyncio.wait_for(_call(), ...)` on that branch), which is also what
    the record `Step.VISUAL_PLANNER` seam writes as `detail="not_run"` -- read here off the
    record rather than off a private attribute, to prove the same fact the ledger of decisions
    would show an operator."""
    calls = _decide_spy(monkeypatch)

    with recording_turn() as turn:
        frames, _ = await _stream_charted_answer("TEXT")

    assert frames[-1]["type"] == "answer"
    assert len(calls) == 1

    sealed = turn.sealed()
    (decision,) = sealed.where(Step.VISUAL_PLANNER)
    assert decision.detail == "not_run"


# --- §8 e: the planner never delays the first visible sign of progress ------------------------


async def test_the_first_frame_is_a_stage_and_arrives_before_the_policy_is_consulted(
    monkeypatch,
) -> None:
    """The stream's first frame is a `stage`, and it is produced before `decide` is ever awaited:
    a shared list records a marker for every frame as it is drained and for every `decide` call
    as it happens, and because `ask_streaming`'s own `events()` generator is lazy (nothing inside
    it runs before the first `__anext__`), draining one frame at a time and stamping the list from
    both sides shows the true interleaving of a real turn rather than two facts checked in
    isolation. If the first frame could not be told apart from the policy call any other way, the
    weaker form (`streamed[0]["type"] == "stage"`, already true) would be all that is left --
    it is not needed here because this ordering is directly observable without touching
    production code."""
    events: list[tuple[str, str]] = []
    original = VisualPlanningPolicy.decide

    async def spy(self, *args: object, **kwargs: object) -> object:
        events.append(("decide", ""))
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(VisualPlanningPolicy, "decide", spy)

    handler, _ = _charted_answer_handler()
    request = QuestionRequest(question=STATES_WINDOW_AND_DIRECTION, channel="TEXT")
    streamed = await ask_streaming(
        request,
        _WHO,
        asking=handler,
        today=CS_TODAY,
        commands=None,
        payments_on=False,
        queries=None,
        attempt="x",
        reads=FixedInterpreter(Intent.FINANCIAL_QUERY),
        metric_tokens=_TEST_TOKENS,
        metrics_on=False,
    )

    async for chunk in streamed.body_iterator:
        body = chunk.decode() if isinstance(chunk, bytes) else chunk
        for line in body.splitlines():
            if line.startswith("data: "):
                frame = json.loads(line.removeprefix("data: "))
                events.append(("frame", frame["type"]))

    assert events[0] == ("frame", "stage")
    decide_indexes = [index for index, event in enumerate(events) if event[0] == "decide"]
    assert decide_indexes, "the policy was never consulted on this turn"
    assert decide_indexes[0] > 0


# --- the final frame's chart identity, and `spoken` gated by channel alone ---------------------


@pytest.mark.parametrize("channel", ["TEXT", "VOICE"])
async def test_the_final_frame_carries_the_charts_own_id_and_spoken_iff_voice(channel) -> None:
    frames, _ = await _stream_charted_answer(channel)
    answer = frames[-1]
    assert answer["type"] == "answer"
    context = answer["conversation"]["visual_contexts"][-1]
    assert answer["visualization"]["chart_id"] == context["chart_id"]
    if channel == "VOICE":
        assert answer["spoken"] is not None
    else:
        assert answer["spoken"] is None
