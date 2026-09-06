"""Every visual follow-up is one of five kinds, and a deterministic reader names which."""

from __future__ import annotations

from datetime import date
from typing import get_args
from uuid import UUID

import pytest
from support.visual_planners import BREAKDOWN_PROFILE
from test_decisions_call_sites import ACCOUNT as CS_ACCOUNT
from test_decisions_call_sites import TODAY as CS_TODAY
from test_decisions_call_sites import FakeLedger, handler_for, turn_for

from finguard_orchestrator.application.queries import ask_financial_question
from finguard_orchestrator.application.queries.financial_turn import (
    AskFinancialQuestionQuery,
    AskFinancialQuestionResult,
)
from finguard_orchestrator.application.queries.readers.visual_follow_up import classify
from finguard_orchestrator.domain.enums import Direction, GroupBy, Period
from finguard_orchestrator.domain.models.chart import DEFAULT_PRESENTATION, VisualContext
from finguard_orchestrator.domain.models.conversation import IDLE, Conversation
from finguard_orchestrator.domain.models.turn_analysis import TurnAnalysis, TurnFocus, TurnRelation
from finguard_orchestrator.domain.models.visual_follow_up import (
    Claimant,
    VisualFollowUp,
    VisualFollowUpKind,
)
from finguard_orchestrator.domain.services.deterministic_visual_planner import (
    DeterministicVisualPlanner,
)
from finguard_orchestrator.domain.services.intent_router import Intent
from finguard_orchestrator.domain.services.visual_interpreter import VisualIntent
from finguard_orchestrator.domain.value_objects.caller import Caller
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan, RelativePeriod
from finguard_orchestrator.domain.value_objects.scope import Scope
from finguard_orchestrator.infrastructure.controllers import llm_controller
from finguard_orchestrator.infrastructure.controllers.llm_controller import ask as ask_question
from finguard_orchestrator.infrastructure.controllers.llm_controller import ask_streaming
from finguard_orchestrator.infrastructure.dto.llm_dto import QuestionRequest, QuestionResponse
from finguard_orchestrator.infrastructure.mappers import conversation_mapper
from finguard_orchestrator.observability import Step, TurnRecord, Verdict, recording_turn

SPEC = DeterministicVisualPlanner().plan(BREAKDOWN_PROFILE, DEFAULT_PRESENTATION)
assert SPEC is not None
ACCOUNT = UUID("11111111-1111-1111-1111-111111111111")
TODAY = date(2026, 7, 15)
# the breakdown the chart in context drew; the reshape reader patches THIS
PREVIOUS = FinancialPlan(
    direction=Direction.EXPENSE,
    period=RelativePeriod(Period.LAST_WEEK),
    group_by=GroupBy.CATEGORY,
    only_account_id=ACCOUNT,
)
CHARTED = Conversation(last_plan=PREVIOUS).with_visual_context(
    VisualContext("chart-1", SPEC, BREAKDOWN_PROFILE)
)
FOLLOW_UP = TurnAnalysis(
    intent=Intent.FINANCIAL_QUERY, relation=TurnRelation.FOLLOW_UP, focus=TurnFocus.LAST_ANSWER
)
NEW = TurnAnalysis(
    intent=Intent.FINANCIAL_QUERY, relation=TurnRelation.NEW_REQUEST, focus=TurnFocus.NEW_REQUEST
)


def _query(sentence: str) -> AskFinancialQuestionQuery:
    """The conversation is passed to classify separately; the query carries the sentence."""
    return AskFinancialQuestionQuery(sentence, ACCOUNT, TODAY, conversation=CHARTED)


ROWS = [
    ("Ponlo apilado", FOLLOW_UP, "PRESENTATION_ONLY", "interpret_visual"),
    ("Ordénalo de mayor a menor", FOLLOW_UP, "PRESENTATION_ONLY", "interpret_visual"),
    # a category patch on PREVIOUS; interpret_visual yields (financial word)
    ("Solo restaurantes", FOLLOW_UP, "ANALYTICAL_RESHAPE", "follow_up_plan"),
    ("¿Por qué agosto es tan alto?", FOLLOW_UP, "DRILL_DOWN", "drill_down_reader"),
    ("Enséñame los movimientos responsables", FOLLOW_UP, "DRILL_DOWN", "drill_down_reader"),
    ("¿Cuánto gasté en mayo?", NEW, "UNRELATED", "none"),
    ("Hola", NEW, "UNRELATED", "none"),
    # both a data change ("restaurantes") and a format change ("barras") in one turn: the
    # interpreter's own mixed refusal, read as AMBIGUOUS before the table below is ever reached.
    ("Ponlo en barras y solo restaurantes", FOLLOW_UP, "AMBIGUOUS", "both"),
    # An unqualified "por que" or a bare "responsable"/"explica" used to read every one of these
    # as a drill-down; none of them names anything about the chart in context.
    ("¿Por qué no me funciona la app?", FOLLOW_UP, "UNRELATED", "none"),
    ("Explica el plan", FOLLOW_UP, "UNRELATED", "none"),
    ("¿Qué compras puedo hacer con 100 euros?", FOLLOW_UP, "UNRELATED", "none"),
    ("el responsable de la nómina", FOLLOW_UP, "UNRELATED", "none"),
]


@pytest.mark.parametrize(("sentence", "analysis", "kind", "claimant"), ROWS)
def test_the_deterministic_readers_name_the_kind(sentence, analysis, kind, claimant) -> None:
    assert classify(_query(sentence), analysis, CHARTED) == VisualFollowUp(kind, claimant)


def test_without_a_chart_in_context_every_turn_is_unrelated() -> None:
    result = classify(_query("Ordenalo de mayor a menor"), FOLLOW_UP, IDLE)
    assert result == VisualFollowUp("UNRELATED", "none")


def test_two_readers_claiming_is_ambiguous(monkeypatch) -> None:
    """Forced, because the two real readers rarely overlap: the test is about the table's own
    `sum(...) > 1` branch, not the regex. Presentation+drill rather than presentation+reshape,
    because a real row now reaches presentation+reshape's AMBIGUOUS a different way — through the
    interpreter's own `mixed` refusal, before this table is even consulted."""
    import finguard_orchestrator.application.queries.readers.visual_follow_up as module

    monkeypatch.setattr(
        module, "_visual_intent", lambda query, conversation: VisualIntent(None, None)
    )
    monkeypatch.setattr(module, "_drill_down_claims", lambda query, analysis: True)
    assert classify(_query("x"), FOLLOW_UP, CHARTED) == VisualFollowUp("AMBIGUOUS", "both")


def test_classify_records_nothing_and_leaves_the_query_alone() -> None:
    """`classify` must not route through any `@_recogniser` seam — directly, as
    `contextual_follow_up_plan` once was, or through a nested consultation, as
    `_internal_movement_request` once was inside `read_contextual_follow_up`. A read-only
    classifier that records a RECOGNISER/CLAIMED decision, or leaves so much as one attribute on
    the frozen query it was handed, is not pure, and the module docstring says it is.

    Three sentences, three different claimants, so the seam has three different doors to sneak in
    through: a reshape ("Solo restaurantes"), a presentation change ("Ponlo apilado"), and a
    sentence that makes the *nested* `_internal_movement_request` claim ("Cuanto he movido entre
    mis cuentas") — which used to record a decision even though `classify` itself never claims
    anything for it (the overall result is UNRELATED; `read_contextual_follow_up` declines once its
    nested check claims).
    """
    for sentence, expected in (
        ("Solo restaurantes", VisualFollowUp("ANALYTICAL_RESHAPE", "follow_up_plan")),
        ("Ponlo apilado", VisualFollowUp("PRESENTATION_ONLY", "interpret_visual")),
        ("Cuanto he movido entre mis cuentas", VisualFollowUp("UNRELATED", "none")),
    ):
        query = _query(sentence)
        before = dict(vars(query))
        with recording_turn() as turn:
            result = classify(query, FOLLOW_UP, CHARTED)
        assert result == expected, sentence
        assert turn.sealed().decisions == (), sentence
        assert dict(vars(query)) == before, sentence


def test_every_kind_and_claimant_is_reachable() -> None:
    """Every kind and claimant `classify` can produce is produced at least once here — through
    `classify` itself over ROWS, plus the forced AMBIGUOUS case above for the pair no sentence in
    ROWS happens to force. Collected from `classify`'s own results rather than from `row[2]`/
    `row[3]`, so a `classify` that stopped producing a kind ROWS claims would fail here too."""
    produced = [classify(_query(sentence), analysis, CHARTED) for sentence, analysis, _, _ in ROWS]
    kinds = {result.kind for result in produced} | {"AMBIGUOUS"}
    claimants = {result.claimant for result in produced} | {"both"}
    assert kinds == set(get_args(VisualFollowUpKind))
    assert claimants == set(get_args(Claimant))


def test_the_reader_never_reads_amounts() -> None:
    """The classifier receives a query and an analysis; the profile and spec in context are
    value-free by construction, and the reader must not reach into `visual_dataset` (not on the
    query)."""
    import inspect

    import finguard_orchestrator.application.queries.readers.visual_follow_up as module

    source = inspect.getsource(module)
    assert "visual_dataset" not in source and "amount" not in source


# --- the twelfth seam: recorded where every visual turn passes --------------------------------


@pytest.fixture
def seam_turn():
    """`seam_turn(sentence, conversation)` drives the handler under `recording_turn()` the way
    `tests/test_visual_decision_seam.py` does, returning the sealed `TurnRecord`."""

    async def _seam_turn(sentence: str, conversation: Conversation) -> TurnRecord:
        _, sealed = await turn_for(sentence, PREVIOUS, conversation=conversation)
        return sealed

    return _seam_turn


@pytest.fixture
def handler_turn():
    async def _handler_turn(
        sentence: str, conversation: Conversation
    ) -> AskFinancialQuestionResult:
        """The same turn with NO recording turn open — `record()` is a no-op outside one."""
        handler = handler_for(PREVIOUS, FakeLedger())
        return await handler.handle(
            AskFinancialQuestionQuery(
                sentence=sentence,
                account_id=CS_ACCOUNT,
                today=CS_TODAY,
                conversation=conversation,
            )
        )

    return _handler_turn


async def test_a_charted_turn_records_its_kind_and_claimant(seam_turn) -> None:
    sealed = await seam_turn("Ordenalo de mayor a menor", CHARTED)
    (decision,) = sealed.where(Step.VISUAL_FOLLOW_UP)
    assert (decision.verdict, decision.changed, decision.field) == (
        Verdict.AGREED,
        False,
        "visual_follow_up",
    )
    assert (decision.was, decision.now, decision.detail) == (
        None,
        "PRESENTATION_ONLY",
        "interpret_visual",
    )


async def test_a_turn_without_a_chart_records_no_visual_follow_up(seam_turn) -> None:
    sealed = await seam_turn("¿Cuánto gasté en mayo?", IDLE)
    assert sealed.where(Step.VISUAL_FOLLOW_UP) == ()


@pytest.mark.parametrize("kind", get_args(VisualFollowUpKind))
async def test_every_kind_is_a_record_row(seam_turn, monkeypatch, kind) -> None:
    """`_record_visual_follow_up` imports `classify` at module level now (the interpreter it once
    reached through `readers.visual_follow_up` lives in `domain/services/` and no longer cycles
    back through `infrastructure`), so the name to patch is the ordinary one: where it is used."""
    monkeypatch.setattr(
        ask_financial_question,
        "classify_visual_follow_up",
        lambda query, analysis, conversation: VisualFollowUp(kind, "none"),
    )
    sealed = await seam_turn("x", CHARTED)
    (decision,) = sealed.where(Step.VISUAL_FOLLOW_UP)
    assert decision.now == kind and decision.changed is False


async def test_the_classification_changes_nothing_visible(handler_turn) -> None:
    """The plan's headline constraint: the same turn, recorded and unrecorded, is the same
    answer."""
    recorded, _ = await turn_for("Ordenalo de mayor a menor", PREVIOUS, conversation=CHARTED)
    unrecorded = await handler_turn("Ordenalo de mayor a menor", CHARTED)
    assert recorded == unrecorded


async def test_a_reshape_turn_records_the_follow_up_reader_at_most_once(seam_turn) -> None:
    """The classifier consults the follow-up reader's undecorated body (Task 5 fix), so the
    cascade's own @_recogniser call is the only one that may record a claim. Two CLAIMED rows
    for one reader in one turn is the regression tests/test_reader_registry.py's invariant
    exists to forbid."""
    # The handler's deterministic analyser labels this turn NEW_REQUEST (probed at 11c1f54), so
    # the reshape gate never opens unless the edge's analysis is handed in, as production does:
    _, sealed = await turn_for(
        "Solo restaurantes", PREVIOUS, conversation=CHARTED, analysis=FOLLOW_UP
    )
    claims = [
        d
        for d in sealed.decisions
        if d.step is Step.RECOGNISER and d.detail == "contextual_follow_up_plan"
    ]
    assert len(claims) <= 1
    (kind,) = sealed.where(Step.VISUAL_FOLLOW_UP)
    assert kind.now == "ANALYTICAL_RESHAPE"


# --- the twelfth seam, on the controller's own visual short-circuit ----------------------------
#
# `llm_controller.py` answers a claimed visual turn -- `interpret_visual` returning non-None -- at
# both the question route and the streaming route without ever calling the handler, so a record
# planted only in `ask_financial_question.py` would never see a real PRESENTATION_ONLY or AMBIGUOUS
# turn in production. These tests drive the route *functions* directly rather than through an
# app-level TestClient: the recording turn is opened per request by the OTel middleware
# (`observability/otel.py:481`), not by the route, so a test calling the route function bypasses
# that middleware and has to open one itself -- exactly `recording_turn()`, the same value the
# middleware would have supplied.

_WHO = Caller(
    holder_reference="ES-0001", account_id=ACCOUNT, scopes=frozenset({Scope.QUESTIONS_READ})
)
# Fixed rather than left to mint: `conversation_mapper.to_domain` mints a fresh `conversation_id`
# for a payload that arrives without one, and that randomness has nothing to do with recording.
_CONVERSATION_ID = UUID("22222222-2222-2222-2222-222222222222")
# `_visual_only_response`'s restyle path mints its own chart_id and turn_id on every call
# (`llm_controller._derived_context`, `uuid.uuid4()`) regardless of whether a turn is being
# recorded -- pinned here for the same reason `_CONVERSATION_ID` is.
_MINTED_ID = UUID("33333333-3333-3333-3333-333333333333")


def _charted_request(sentence: str) -> QuestionRequest:
    payload = conversation_mapper.to_payload(CHARTED).model_copy(
        update={"conversation_id": _CONVERSATION_ID}
    )
    return QuestionRequest(question=sentence, conversation=payload)


async def _drive_question_route(sentence: str) -> QuestionResponse:
    return await ask_question(
        _charted_request(sentence),
        _WHO,
        queries=None,
        today=CS_TODAY,
        commands=None,
        payments_on=False,
        attempt="x",
        reads=None,
        metric_tokens=None,
        metrics_on=False,
    )


@pytest.mark.parametrize(
    ("sentence", "kind", "claimant"),
    [
        ("Ordénalo de mayor a menor", "PRESENTATION_ONLY", "interpret_visual"),
        ("Ponlo en barras y solo restaurantes", "AMBIGUOUS", "both"),
    ],
)
async def test_the_controllers_visual_short_circuit_records_the_kind_and_claimant(
    sentence, kind, claimant
) -> None:
    """The question route never reaches the handler for a claimed visual turn, and the twelfth
    seam is recorded here anyway -- the same vocabulary, the same helper."""
    with recording_turn() as turn:
        await _drive_question_route(sentence)
    (decision,) = turn.sealed().where(Step.VISUAL_FOLLOW_UP)
    assert (decision.now, decision.detail, decision.changed) == (kind, claimant, False)


async def test_the_streaming_routes_short_circuit_records_it_too() -> None:
    """The second branch this task touches. The record happens before the SSE body is ever
    built, so nothing here has to drain the stream to see it."""
    with recording_turn() as turn:
        await ask_streaming(
            _charted_request("Ordénalo de mayor a menor"),
            _WHO,
            asking=None,
            today=CS_TODAY,
            commands=None,
            payments_on=False,
            queries=None,
            attempt="x",
            reads=None,
            metric_tokens=None,
            metrics_on=False,
        )
    (decision,) = turn.sealed().where(Step.VISUAL_FOLLOW_UP)
    assert (decision.now, decision.detail) == ("PRESENTATION_ONLY", "interpret_visual")


async def test_the_controllers_visual_response_is_unchanged_by_recording(monkeypatch) -> None:
    """No visible change, on the controller's own short-circuit: the caller gets exactly the same
    response whether or not a recording turn happens to be open around it."""
    monkeypatch.setattr(llm_controller.uuid, "uuid4", lambda: _MINTED_ID)

    with recording_turn():
        recorded = await _drive_question_route("Ordénalo de mayor a menor")
    unrecorded = await _drive_question_route("Ordénalo de mayor a menor")

    assert recorded == unrecorded
