"""Every visual follow-up is one of five kinds, and a deterministic reader names which."""

from __future__ import annotations

import copy
import json
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import date
from typing import get_args
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from support.visual_planners import BREAKDOWN_PROFILE
from test_decisions_call_sites import ACCOUNT as CS_ACCOUNT
from test_decisions_call_sites import TODAY as CS_TODAY
from test_decisions_call_sites import FakeLedger, handler_for, turn_for

from finguard_orchestrator.application.bus.query_bus import QueryBus
from finguard_orchestrator.application.queries import ask_financial_question
from finguard_orchestrator.application.queries.financial_turn import (
    AskFinancialQuestionQuery,
    AskFinancialQuestionResult,
    SpokenGroup,
    SpokenPlan,
)
from finguard_orchestrator.application.queries.readers.follow_up import (
    read_contextual_follow_up,
)
from finguard_orchestrator.application.queries.readers.visual_follow_up import (
    classify,
    classify_claimed_visual,
)
from finguard_orchestrator.bootstrap.dependencies import (
    authenticated_caller,
    get_ask_handler,
    get_query_bus,
)
from finguard_orchestrator.bootstrap.main import app
from finguard_orchestrator.domain.enums import Direction, GroupBy, Period
from finguard_orchestrator.domain.models.chart import (
    DEFAULT_PRESENTATION,
    VisualContext,
    VisualDataset,
    VisualRow,
)
from finguard_orchestrator.domain.models.conversation import IDLE, Conversation
from finguard_orchestrator.domain.models.saved_metric import SavedMetricDefinition
from finguard_orchestrator.domain.models.turn_analysis import TurnAnalysis, TurnFocus, TurnRelation
from finguard_orchestrator.domain.models.visual_follow_up import (
    Claimant,
    VisualFollowUp,
    VisualFollowUpKind,
)
from finguard_orchestrator.domain.services.deterministic_visual_planner import (
    DeterministicVisualPlanner,
    VisualDecision,
)
from finguard_orchestrator.domain.services.intent_router import Intent
from finguard_orchestrator.domain.services.visual_interpreter import VisualIntent, interpret_visual
from finguard_orchestrator.domain.value_objects.caller import Caller
from finguard_orchestrator.domain.value_objects.financial_plan import (
    AbsoluteRange,
    FinancialPlan,
    RelativePeriod,
)
from finguard_orchestrator.domain.value_objects.scope import Scope
from finguard_orchestrator.infrastructure.controllers import llm_controller
from finguard_orchestrator.infrastructure.controllers.llm_controller import ask as ask_question
from finguard_orchestrator.infrastructure.controllers.llm_controller import ask_streaming
from finguard_orchestrator.infrastructure.dto.llm_dto import (
    MetricSourcePayload,
    QuestionRequest,
    QuestionResponse,
)
from finguard_orchestrator.infrastructure.mappers import conversation_mapper
from finguard_orchestrator.infrastructure.security.metric_tokens import MetricTokenCodec
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


def _query(sentence: str, *, conversation: Conversation = CHARTED) -> AskFinancialQuestionQuery:
    """The conversation is passed to classify separately; the query carries the sentence."""
    return AskFinancialQuestionQuery(sentence, ACCOUNT, TODAY, conversation=conversation)


# Task 2 (Wave 4.2b): a summer chart that names no category yet, over an ordinary window --
# «separa transporte público y gasolina» patches THIS.
SUMMER_PLAN = FinancialPlan(
    direction=Direction.EXPENSE,
    date_range=AbsoluteRange(date(2026, 6, 1), date(2026, 8, 31)),
    group_by=GroupBy.NONE,
    only_account_id=ACCOUNT,
)
CHARTED_SUMMER = Conversation(last_plan=SUMMER_PLAN).with_visual_context(
    VisualContext("chart-summer", SPEC, BREAKDOWN_PROFILE)
)
# The plan in view after that split -- named by both categories, grouped by CATEGORY, same
# window and direction as CHARTED_SUMMER. «ahora compáralo por meses» patches THIS.
SPLIT_PLAN = replace(
    SUMMER_PLAN, categories=("TRANSPORT_PUBLIC", "TRANSPORT_FUEL"), group_by=GroupBy.CATEGORY
)
SPLIT_SUMMER = Conversation(last_plan=SPLIT_PLAN).with_visual_context(
    VisualContext("chart-summer-split", SPEC, BREAKDOWN_PROFILE)
)


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
    ("Ponlo en barras y solo restaurantes", FOLLOW_UP, "AMBIGUOUS", "presentation+reshape"),
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
    assert classify(_query("x"), FOLLOW_UP, CHARTED) == VisualFollowUp(
        "AMBIGUOUS", "presentation+drill"
    )


# --- I-1: the controller's short-circuit asks classify's own table too -----------------------

CLAIMED_ROWS = [
    # Each of these three also matches the drill-down lexicon below -- the pair the two-way
    # hand-built mapping in llm_controller.py could never see (I-1).
    ("muéstrame los movimientos ordenados de mayor a menor", "AMBIGUOUS", "presentation+drill"),
    ("qué movimientos hay, ponlo en barras", "AMBIGUOUS", "presentation+drill"),
    ("enséñame las compras apiladas", "AMBIGUOUS", "presentation+drill"),
    ("Ordénalo de mayor a menor", "PRESENTATION_ONLY", "interpret_visual"),
]


@pytest.mark.parametrize(("sentence", "kind", "claimant"), CLAIMED_ROWS)
def test_classify_claimed_visual_asks_the_drill_lexicon_too(sentence, kind, claimant) -> None:
    intent = interpret_visual(sentence, CHARTED.visual_contexts)
    assert intent is not None
    assert classify_claimed_visual(intent, sentence) == VisualFollowUp(kind, claimant)


def test_classify_claimed_visual_reads_the_interpreters_own_mixed_refusal() -> None:
    """The interpreter's own mixed-turn refusal (a data change and a format change asked
    together) is AMBIGUOUS regardless of what the sentence says, and regardless of the drill
    lexicon -- `intent.mixed` short-circuits `classify_claimed_visual` the same way it
    short-circuits `classify`."""
    mixed = VisualIntent(None, None, "explanation", mixed=True)
    assert classify_claimed_visual(mixed, "cualquier cosa") == VisualFollowUp(
        "AMBIGUOUS", "presentation+reshape"
    )


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
    """Every kind and claimant `classify`/`classify_claimed_visual` can produce is produced at
    least once here, through real (unmocked) calls: `classify` itself over ROWS gives the single
    claimants and `presentation+reshape` (the mixed row); `classify_claimed_visual` is asked
    directly for its own two AMBIGUOUS returns -- a mixed intent (`presentation+reshape` again)
    and an I-1 sentence that matches the drill lexicon (`presentation+drill`).

    The assertion is deliberately NOT a union of `get_args(Claimant)` back into what was produced
    -- that union would make any `+`-shaped member of `Claimant` pass whether or not anything ever
    produces it. Instead it takes the *difference*: `reshape+drill` is the one documented member
    nothing here (or in production) can reach, because `classify`'s own drill-before-reshape
    precedence (`reshape = not drill and ...`) makes drill and reshape mutually exclusive. Any
    other unreached member -- including a new pair added to `Claimant` without a reader to produce
    it -- fails this assertion instead of passing silently."""
    produced = [classify(_query(sentence), analysis, CHARTED) for sentence, analysis, _, _ in ROWS]
    kinds = {result.kind for result in produced} | {"AMBIGUOUS"}
    claimants = {result.claimant for result in produced}

    mixed_intent = VisualIntent(None, None, "explanation", mixed=True)
    claimants.add(classify_claimed_visual(mixed_intent, "cualquier cosa").claimant)
    lexicon_sentence = CLAIMED_ROWS[0][0]
    lexicon_intent = interpret_visual(lexicon_sentence, CHARTED.visual_contexts)
    assert lexicon_intent is not None
    claimants.add(classify_claimed_visual(lexicon_intent, lexicon_sentence).claimant)

    assert kinds == set(get_args(VisualFollowUpKind))
    assert set(get_args(Claimant)) - claimants == {"reshape+drill"}


def test_the_handlers_drill_down_literal_is_in_the_vocabulary() -> None:
    """M2 (fix wave): `ask_financial_question.py`'s own `follow_up_seen[0].kind == "DRILL_DOWN"`
    check is a hand-typed string, not read off `VisualFollowUpKind` -- pinned against the
    vocabulary here the same way `_DERIVING_KINDS` (`llm_mapper.py`) now asserts itself at import
    time, so a typo in either stops silently rather than passing a chart's own follow-up through
    unrecognised."""
    assert "DRILL_DOWN" in get_args(VisualFollowUpKind)


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
    """`_classify_and_record_visual_follow_up` imports `classify` at module level now (the
    interpreter it once reached through `readers.visual_follow_up` lives in `domain/services/`
    and no longer cycles back through `infrastructure`), so the name to patch is the ordinary
    one: where it is used."""
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


async def test_the_result_carries_the_follow_up_when_a_chart_was_in_context() -> None:
    """Task 4: `_classify_and_record_visual_follow_up`'s return travels onto the result the
    handler yields -- the same seam `test_a_charted_turn_records_its_kind_and_claimant` proves is
    recorded."""
    result, _ = await turn_for(
        "Ordenalo de mayor a menor", PREVIOUS, conversation=CHARTED, analysis=FOLLOW_UP
    )
    assert result.visual_follow_up == VisualFollowUp("PRESENTATION_ONLY", "interpret_visual")


async def test_the_result_carries_no_follow_up_without_a_chart_in_context() -> None:
    result, _ = await turn_for("¿Cuánto gasté en mayo?", PREVIOUS, conversation=IDLE, analysis=NEW)
    assert result.visual_follow_up is None


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


async def test_a_reshape_turns_result_carries_the_kind_the_seam_recorded() -> None:
    """The same turn as above, read off the result rather than off the record: the field the
    mapper's `derived_from` (Task 4) reads is set to what the seam classified, not just logged."""
    result, _ = await turn_for(
        "Solo restaurantes", PREVIOUS, conversation=CHARTED, analysis=FOLLOW_UP
    )
    assert result.visual_follow_up == VisualFollowUp("ANALYTICAL_RESHAPE", "follow_up_plan")


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
# Task 3: `_conversation_of` now verifies every echoed visual context against a real codec -- these
# tests drive the route function directly rather than through the DI container, so they need one
# too, and `CHARTED`'s own context (built in-process, `token=None`) needs sealing for `_WHO` before
# it crosses `_charted_request`'s wire round trip, or it would come back `trusted=False` the same
# as a tampered one, and the PRESENTATION_ONLY restyle below would refuse rather than update.
_TEST_TOKENS = MetricTokenCodec({"active": b"x" * 32}, "active")
_SEALED_CHARTED = replace(
    CHARTED,
    # D-3b-9 (fix wave): over every context `CHARTED` carries, not `visual_contexts[0]` alone --
    # `CHARTED` holds one chart today, so the two read the same, but a hard-coded index silently
    # drops the rest the day `CHARTED` gains a second one, and the failure would look like a trust
    # bug in whatever test runs next rather than a stale fixture here.
    visual_contexts=tuple(
        conversation_mapper.seal_context(context, _WHO.holder_reference, _TEST_TOKENS)
        for context in CHARTED.visual_contexts
    ),
)


def _charted_request(sentence: str) -> QuestionRequest:
    payload = conversation_mapper.to_payload(_SEALED_CHARTED).model_copy(
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
        metric_tokens=_TEST_TOKENS,
        metrics_on=False,
    )


@pytest.mark.parametrize(
    ("sentence", "kind", "claimant"),
    [
        ("Ordénalo de mayor a menor", "PRESENTATION_ONLY", "interpret_visual"),
        ("Ponlo en barras y solo restaurantes", "AMBIGUOUS", "presentation+reshape"),
        # I-1: interpret_visual claims this sentence non-mixed, but it also matches the drill
        # lexicon -- the pair the old hand-built two-way mapping recorded as PRESENTATION_ONLY.
        ("muéstrame los movimientos ordenados de mayor a menor", "AMBIGUOUS", "presentation+drill"),
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
            metric_tokens=_TEST_TOKENS,
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


# --- Wave 4.4 (design §6): a spoken restyle takes the typed restyle's own path ------------------
#
# `interpret_visual` no longer refuses on the VOICE channel before resolving a target -- a spoken
# «ponlo en horizontal» is read exactly like a typed one. The metric-saving offer stays a TEXT
# affordance (ruling R5: a save needs a screen control), so it is the one channel read left; the
# restyle itself is not.


async def test_a_voice_restyle_takes_the_typed_restyles_path_on_the_streaming_route() -> None:
    """«Ponlo en vertical» spoken (`channel="VOICE"`) reaches the same short-circuit a typed
    restyle does: zero banking calls, the sealed context re-verified and carried forward with a
    fresh token, `VISUAL_UPDATED`, and no save candidate. The identical request on `channel="TEXT"`
    still offers one -- the save offer is channel-gated, the restyle is not.
    """
    fake_queries = _UnreachableQueries()
    metric_token = _TEST_TOKENS.issue(
        _WHO.holder_reference, SavedMetricDefinition(PREVIOUS, SPEC), chart_id="chart-1"
    )
    payload = conversation_mapper.to_payload(_SEALED_CHARTED).model_copy(
        update={"conversation_id": _CONVERSATION_ID}
    )
    voice_request = QuestionRequest(
        question="Ponlo en vertical",
        conversation=payload,
        channel="VOICE",
        metric_sources=[MetricSourcePayload(chart_id="chart-1", definition_token=metric_token)],
    )

    async def _streamed(request: QuestionRequest) -> dict:
        streamed = await ask_streaming(
            request,
            _WHO,
            asking=None,
            today=CS_TODAY,
            commands=None,
            payments_on=False,
            queries=fake_queries,
            attempt="x",
            reads=None,
            metric_tokens=_TEST_TOKENS,
            metrics_on=True,
        )
        chunks = [chunk async for chunk in streamed.body_iterator]
        body = "".join(chunk.decode() if isinstance(chunk, bytes) else chunk for chunk in chunks)
        frames = [
            json.loads(line.removeprefix("data: "))
            for line in body.splitlines()
            if line.startswith("data: ")
        ]
        assert frames[-1]["type"] == "answer"
        return frames[-1]

    voice_answer = await _streamed(voice_request)
    assert voice_answer["outcome"] == "VISUAL_UPDATED"
    assert voice_answer["save_candidate"] is None
    (context,) = voice_answer["conversation"]["visual_contexts"][-1:]
    assert context["token"]
    assert fake_queries.ran == []

    text_answer = await _streamed(voice_request.model_copy(update={"channel": "TEXT"}))
    assert text_answer["outcome"] == "VISUAL_UPDATED"
    assert text_answer["save_candidate"] is not None
    assert fake_queries.ran == []


async def test_a_voice_restyle_updates_through_the_non_streaming_route_too() -> None:
    """The second route this task touches: the same VOICE-channel restyle updates the chart with
    zero banking calls on `/v1/questions` (the non-streaming route) as well as on the stream."""
    fake_queries = _UnreachableQueries()
    payload = conversation_mapper.to_payload(_SEALED_CHARTED).model_copy(
        update={"conversation_id": _CONVERSATION_ID}
    )
    request = QuestionRequest(question="Ponlo en vertical", conversation=payload, channel="VOICE")

    response = await ask_question(
        request,
        _WHO,
        queries=fake_queries,
        today=CS_TODAY,
        commands=None,
        payments_on=False,
        attempt="x",
        reads=None,
        metric_tokens=_TEST_TOKENS,
        metrics_on=False,
    )

    assert response.outcome == "VISUAL_UPDATED"
    assert fake_queries.ran == []


# --- Task 6: an AMBIGUOUS short-circuit asks instead of guessing -------------------------------
#
# Before this task, a claimed visual turn always went to `_visual_only_response`: the mixed
# sentence surfaced the interpreter's own refusal (`VISUAL_INCOMPATIBLE`), and the I-1 drill
# sentence was silently restyled (`VISUAL_UPDATED`) because `interpret_visual` alone had already
# claimed it. Both are the silent guess §5 says AMBIGUOUS exists to replace with a question.


class _UnreachableQueries:
    """A `Queries` double for the ask path: any call means the short-circuit fell through to the
    handler and its ledger, which the AMBIGUOUS branch must never do."""

    def __init__(self) -> None:
        self.ran: list[object] = []

    async def ask(self, query: object) -> object:
        self.ran.append(query)
        raise AssertionError("the AMBIGUOUS ask path must never reach the query bus")


ASK_ROWS = [
    # I-1: the drill lexicon also matches this sentence -- the pair a silent restyle used to hide.
    ("muéstrame los movimientos ordenados de mayor a menor", "presentation+drill", True),
    # The interpreter's own mixed refusal -- a data change and a format change in one turn.
    ("Ponlo en barras y solo restaurantes", "presentation+reshape", True),
    # m3 (fix wave), pinned rather than left incidental: the ask wins even over an untrusted
    # chart. Classifying a turn AMBIGUOUS takes no decision on the chart -- no merge, no verifier
    # input, no save candidate, no chip -- so the trust gate need not run first, and running it
    # first would refuse a turn the server has not yet decided is even about that chart, losing
    # §5's own ambiguity record for a decision this branch never makes.
    ("muéstrame los movimientos ordenados de mayor a menor", "presentation+drill", False),
]


@pytest.mark.parametrize(("sentence", "claimant", "trusted"), ASK_ROWS)
async def test_an_ambiguous_short_circuit_asks_instead_of_restyling(
    sentence, claimant, trusted
) -> None:
    """§5's row: a claimed turn two readers agree on gets a question, not a guess -- zero ledger
    calls, and the one VISUAL_FOLLOW_UP row the recording seam always writes for a claimed turn.

    m3 (fix wave): the untrusted row pins what a tampered chart can and cannot steer here. It can
    choose which of the two `_ASK` texts comes back (`claimant`, read off the interpreter before
    trust is ever asked about); it cannot reach a merge, a verifier input, a save candidate or a
    chip, and the context's token is nulled in the reply exactly as an untrusted context always
    comes back -- the same refusal `not context.trusted` produces everywhere else, just recorded
    one step later than a plain restyle would be.
    """
    fake_queries = _UnreachableQueries()
    request = _charted_request(sentence)
    if not trusted:
        # `_Payload` is not frozen (`test_visualization_stage1.py`'s own comment): mutate the wire
        # shape in place, exactly what a tampering client's request body would look like.
        context = request.conversation.visual_contexts[0]
        context.profile.comparison = not context.profile.comparison
    with recording_turn() as turn:
        response = await ask_question(
            request,
            _WHO,
            queries=fake_queries,
            today=CS_TODAY,
            commands=None,
            payments_on=False,
            attempt="x",
            reads=None,
            metric_tokens=_TEST_TOKENS,
            metrics_on=False,
        )

    assert response.outcome == "VISUAL_AMBIGUOUS"
    assert response.text == llm_controller._ASK[claimant]
    if trusted:
        assert response.conversation == request.conversation
    else:
        assert response.conversation.visual_contexts[0].token is None
    assert fake_queries.ran == []
    (decision,) = turn.sealed().where(Step.VISUAL_FOLLOW_UP)
    assert (decision.now, decision.detail) == ("AMBIGUOUS", claimant)


async def test_the_streaming_routes_ambiguous_short_circuit_asks_too() -> None:
    """The second branch this task touches: the SSE body's one `answer` event carries the
    question, not a restyle -- drained here because, unlike the recording-only streaming test
    above, what the answer actually says is exactly what changed."""
    fake_queries = _UnreachableQueries()
    sentence = "muéstrame los movimientos ordenados de mayor a menor"
    request = _charted_request(sentence)
    with recording_turn() as turn:
        streamed = await ask_streaming(
            request,
            _WHO,
            asking=None,
            today=CS_TODAY,
            commands=None,
            payments_on=False,
            queries=fake_queries,
            attempt="x",
            reads=None,
            metric_tokens=_TEST_TOKENS,
            metrics_on=False,
        )
        chunks = [chunk async for chunk in streamed.body_iterator]

    body = "".join(chunk.decode() if isinstance(chunk, bytes) else chunk for chunk in chunks)
    (line,) = [line for line in body.splitlines() if line.startswith("data: ")]
    payload = json.loads(line.removeprefix("data: "))
    assert payload["type"] == "answer"
    assert payload["outcome"] == "VISUAL_AMBIGUOUS"
    assert payload["text"] == llm_controller._ASK["presentation+drill"]
    assert fake_queries.ran == []
    (decision,) = turn.sealed().where(Step.VISUAL_FOLLOW_UP)
    assert (decision.now, decision.detail) == ("AMBIGUOUS", "presentation+drill")


async def test_a_presentation_only_short_circuit_still_updates_through_the_controller() -> None:
    """Regression: a non-ambiguous claimed turn still restyles as it did before this task, through
    the same branch point that now also has to recognise AMBIGUOUS.

    `CHARTED`'s dimension axis already ranks descending -- `BREAKDOWN_PROFILE`'s own
    `order="MEASURE_DESC"` (`support/visual_planners.py`), the shape the floor always builds --
    so «de mayor a menor» over it is a genuine no-op after F2 (fix wave 2) and answers
    `VISUAL_UNCHANGED` instead. Asking for the other direction is what actually restyles it.
    """
    response = await _drive_question_route("Ordénalo de menor a mayor")
    assert response.outcome == "VISUAL_UPDATED"


def test_every_ambiguous_claimant_classify_claimed_visual_can_return_has_a_text() -> None:
    """`_ASK` is keyed by the two pairs `classify_claimed_visual` can actually produce (I-1's own
    docstring): this derives them from the function's own outputs, on a mixed intent and on an
    I-1 sentence, rather than hand-listing the pairs again -- so an unreachable pair can never
    `KeyError` into a 500 in production."""
    mixed_intent = VisualIntent(None, None, "explanation", mixed=True)
    mixed = classify_claimed_visual(mixed_intent, "cualquier cosa")
    assert mixed.kind == "AMBIGUOUS"
    assert mixed.claimant in llm_controller._ASK

    lexicon_sentence = "muéstrame los movimientos ordenados de mayor a menor"
    lexicon_intent = interpret_visual(lexicon_sentence, CHARTED.visual_contexts)
    assert lexicon_intent is not None
    drill = classify_claimed_visual(lexicon_intent, lexicon_sentence)
    assert drill.kind == "AMBIGUOUS"
    assert drill.claimant in llm_controller._ASK


# --- Task 3: sealed where it is born, verified where it enters, refused where untrusted --------
#
# The only test in this module driven through a real `TestClient` rather than the route functions
# directly: what is under test here is the app's own wiring -- `app.state.metric_tokens`, the dev
# key it runs on in this process (`tests/conftest.py`'s `ORCHESTRATOR_DEV_TOKENS`), and the fact
# that sealing and verifying a context both go through that one codec -- not any behaviour a bare
# function call could exercise without it.


class _DrawsOneChart:
    """A `QueryBus` handler that answers every `AskFinancialQuestionQuery` with the same charted
    result -- `SPEC` over `BREAKDOWN_PROFILE`, the same pair `CHARTED` already holds and the same
    sentences above already restyle -- so the round trip below is about the seal, not about
    whether this fixture's own chart happens to support a sort.

    `conversation=query.conversation` (fix round 2): a real handler answers on top of whatever the
    query already carried, not on a blank slate -- this is what lets a compound clause's own prior
    context (another chart already in view, an untrusted one carried forward) survive into the
    result `_compound_read_reply` builds on. Every single-turn "draw" test below sends no
    conversation at all, so `query.conversation` there is the same empty one `AskFinancialQuestion
    Result`'s own default (`IDLE`) already was -- this changes nothing for them.

    `calls` (D-3b-8, fix wave): every query this instance ever answered, so a test can assert that
    a refused restyle reached neither this handler nor -- through `_StreamsOneChart` below, which
    wraps the same instance -- the streaming route's own `Asking` dependency.
    """

    def __init__(self) -> None:
        self.calls: list[AskFinancialQuestionQuery] = []

    async def handle(self, query: AskFinancialQuestionQuery) -> AskFinancialQuestionResult:
        self.calls.append(query)
        return AskFinancialQuestionResult(
            outcome="PLANNED",
            text="Aquí tienes el desglose.",
            plan_context=SpokenPlan(direction="EXPENSE", group_by="CATEGORY", metric="SUM"),
            groups=[SpokenGroup("ALQUILER", 51_300, 1), SpokenGroup("OCIO", 4_000, 1)],
            visual_dataset=VisualDataset(
                metric="SUM", rows=(VisualRow("ALQUILER", 51_300), VisualRow("OCIO", 4_000))
            ),
            visual_decision=VisualDecision(
                spec=SPEC, profile=BREAKDOWN_PROFILE, source="deterministic", valid=True
            ),
            conversation=query.conversation,
        )


class _StreamsOneChart:
    """`Asking`'s own interface (`.stream()`), standing in for the real
    `AskFinancialQuestionHandler` on `/v1/questions/stream`. D-3b-8 (fix wave): a draw on that
    route reaches `get_ask_handler` (`Asking`), never the query bus -- overriding only
    `get_query_bus`, which is all `/v1/questions` needs, would leave the real handler in place for
    the streaming route, reaching the real banking API and the real LLM planner the moment a
    first question needs a plan (the same trap the whole-branch review's own process note records
    for this exact route). Wraps a `_DrawsOneChart` rather than answering independently, so both
    routes seal the same chart and share the same call count.
    """

    def __init__(self, inner: _DrawsOneChart) -> None:
        self._inner = inner

    async def stream(
        self, query: AskFinancialQuestionQuery
    ) -> AsyncIterator[AskFinancialQuestionResult]:
        yield await self._inner.handle(query)


def _answer_of(response, route: str) -> dict:
    """The answer payload, whichever route produced it: a plain JSON body from `/v1/questions`,
    or the one `"answer"` SSE event from `/v1/questions/stream` -- D-3b-8, so a route-level test
    can assert the same thing against both without knowing which one it is running against.
    """
    if not route.endswith("/stream"):
        return response.json()
    events = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    (answer,) = [event for event in events if event.get("type") == "answer"]
    return answer


@pytest.mark.parametrize("route", ["/v1/questions", "/v1/questions/stream"])
def test_a_sealed_context_verifies_a_restyle_and_a_tampered_or_missing_one_is_refused(
    route: str,
) -> None:
    """The route boundary, through a real `TestClient`, on both routes (D-3b-8, fix wave --
    before this, only `/v1/questions` had this test and the streaming route had only the whole-
    branch review's own probe): `_conversation_of` verifies every echoed visual context against
    the app's own `metric_tokens` codec -- the development key in this process, same as
    production when no signing key is configured, so sealing and verifying through the same app
    works.

    A first question draws a chart and its response seals the context; echoing that seal back
    lets a restyle through (`VISUAL_UPDATED`). Echoing the same conversation with one bit the seal
    covers flipped, or with the token dropped altogether, answers `VISUAL_UNTRUSTED` instead of
    silently trusting -- or silently refusing to restyle -- an echo nobody can vouch for, and
    reaches neither the query bus nor (on the streaming route) `Asking` to do it.
    """
    draws = _DrawsOneChart()
    bus = QueryBus()
    bus.register(AskFinancialQuestionQuery, draws)
    app.dependency_overrides[get_query_bus] = lambda: bus
    app.dependency_overrides[authenticated_caller] = lambda: _WHO
    if route.endswith("/stream"):
        # D-3b-8: the streaming route's draw step reaches `Asking` (`get_ask_handler`), never the
        # query bus -- overriding only `get_query_bus`, as the question route below needs, would
        # leave the real handler in place here and reach a real model. `_StreamsOneChart` wraps
        # the same `draws` instance the bus above uses, so `draws.calls` counts both routes.
        app.dependency_overrides[get_ask_handler] = lambda: _StreamsOneChart(draws)
    previously_metrics_enabled = app.state.metrics_enabled
    previously_questions_enabled = app.state.questions_enabled
    app.state.metrics_enabled = True
    app.state.questions_enabled = True
    try:
        client = TestClient(app)

        drawn = client.post(route, json={"question": "¿Cuánto gasté el mes pasado?"})
        assert drawn.status_code == 200
        sealed_conversation = _answer_of(drawn, route)["conversation"]
        (context,) = sealed_conversation["visual_contexts"]
        assert context["token"]
        assert len(draws.calls) == 1

        restyled = client.post(
            route,
            json={"question": "ordénalo de menor a mayor", "conversation": sealed_conversation},
        )
        assert _answer_of(restyled, route)["outcome"] == "VISUAL_UPDATED"

        tampered = copy.deepcopy(sealed_conversation)
        tampered["visual_contexts"][0]["profile"]["comparison"] = not context["profile"][
            "comparison"
        ]
        flipped = client.post(
            route,
            json={"question": "ordénalo de menor a mayor", "conversation": tampered},
        )
        assert _answer_of(flipped, route)["outcome"] == "VISUAL_UNTRUSTED"
        # D-3b-8: still exactly one call -- the original draw. Neither the restyle above nor this
        # tampered one ever reaches the bus/handler a second time; the restyle changes the chart
        # through the trusted in-process merge, and the tampered one refuses before either would.
        assert len(draws.calls) == 1

        stripped = copy.deepcopy(sealed_conversation)
        stripped["visual_contexts"][0]["token"] = None
        untokened = client.post(
            route,
            json={"question": "ordénalo de menor a mayor", "conversation": stripped},
        )
        assert _answer_of(untokened, route)["outcome"] == "VISUAL_UNTRUSTED"
        assert len(draws.calls) == 1
    finally:
        app.dependency_overrides.clear()
        app.state.metrics_enabled = previously_metrics_enabled
        app.state.questions_enabled = previously_questions_enabled


def test_a_chart_is_sealed_even_with_metrics_off_but_offers_no_save_candidate() -> None:
    """Fix round 1: the corpus harness (and a voice turn, and any deployment with
    `ORCHESTRATOR_METRICS_ENABLED=false`) never turns metrics on, and Task 3's own first cut of
    the seal silently rode along on that same flag -- so a chart drawn there was never sealed, and
    every later restyle of it came back `VISUAL_UNTRUSTED` for no reason a holder caused. The seal
    is now unconditional: with metrics off (this test's `app.state.metrics_enabled = False`, this
    module's own default), a first question still comes back sealed -- no save candidate to go
    with it, since that offer is still exactly `metrics_on and request.channel == "TEXT"` -- and a
    restyle over that same context still succeeds.
    """
    bus = QueryBus()
    bus.register(AskFinancialQuestionQuery, _DrawsOneChart())
    app.dependency_overrides[get_query_bus] = lambda: bus
    app.dependency_overrides[authenticated_caller] = lambda: _WHO
    previously_metrics_enabled = app.state.metrics_enabled
    previously_questions_enabled = app.state.questions_enabled
    app.state.metrics_enabled = False
    app.state.questions_enabled = True
    try:
        client = TestClient(app)

        drawn = client.post("/v1/questions", json={"question": "¿Cuánto gasté el mes pasado?"})
        assert drawn.status_code == 200
        body = drawn.json()
        assert body["save_candidate"] is None
        sealed_conversation = body["conversation"]
        (context,) = sealed_conversation["visual_contexts"]
        assert context["token"]

        restyled = client.post(
            "/v1/questions",
            json={"question": "ordénalo de menor a mayor", "conversation": sealed_conversation},
        )
        assert restyled.json()["outcome"] == "VISUAL_UPDATED"
    finally:
        app.dependency_overrides.clear()
        app.state.metrics_enabled = previously_metrics_enabled
        app.state.questions_enabled = previously_questions_enabled


# --- Fix round 2 (Task 3, C1/I2): a compound turn's charts are sealed too -----------------------


def test_a_compound_turns_charts_are_sealed_and_each_restyles() -> None:
    """C1: `_compound_read_reply` draws a chart per clause the same as the plain path does, and
    until this fix it called `question_to_dto(result)` with no holder and no codec -- so every
    chart a compound question drew was born unsealed, and the very next turn refused it as
    untrusted (measured at `56fc066`: this exact sentence answered `VISUAL_UPDATED` at BASE and
    regressed to `VISUAL_UNTRUSTED`). Both charts a compound turn draws now carry a token, and a
    restyle of the one in view succeeds.
    """
    bus = QueryBus()
    bus.register(AskFinancialQuestionQuery, _DrawsOneChart())
    app.dependency_overrides[get_query_bus] = lambda: bus
    app.dependency_overrides[authenticated_caller] = lambda: _WHO
    previously_metrics_enabled = app.state.metrics_enabled
    previously_questions_enabled = app.state.questions_enabled
    app.state.metrics_enabled = True
    app.state.questions_enabled = True
    try:
        client = TestClient(app)

        drawn = client.post(
            "/v1/questions", json={"question": "Cuanto tengo y cuanto gaste este mes?"}
        )
        assert drawn.status_code == 200
        body = drawn.json()
        assert body["outcome"] == "COMPOUND"
        contexts = body["conversation"]["visual_contexts"]
        assert len(contexts) == 2
        assert all(context["token"] for context in contexts)

        restyled = client.post(
            "/v1/questions",
            json={
                "question": "ordénalo de menor a mayor",
                "conversation": body["conversation"],
            },
        )
        assert restyled.json()["outcome"] == "VISUAL_UPDATED"
    finally:
        app.dependency_overrides.clear()
        app.state.metrics_enabled = previously_metrics_enabled
        app.state.questions_enabled = previously_questions_enabled


def test_an_untrusted_context_survives_a_compound_round_trip_still_tokenless() -> None:
    """I2: `_compound_read_reply`'s own `to_domain(rendered.conversation)` call passes no
    `verify=`, so every context it round-trips -- including one the caller's own turn already
    marked untrusted -- comes back `trusted=True` in-process. That relaundering is inert only
    because `trusted` never crosses the wire (pinned directly by
    `test_trusted_never_crosses_the_wire`, `test_visual_context_producers.py`) and because the
    untrusted context's `token` was already nulled by `_conversation_of` before this function ever
    saw it, and nothing here re-seals it. Proven end to end: a tampered chart carried into a
    compound turn comes out the other side of the round trip still tokenless, and restyling it
    alone on the next turn is still refused -- the relaundered `trusted=True` never leaks past this
    function's own return.
    """
    bus = QueryBus()
    bus.register(AskFinancialQuestionQuery, _DrawsOneChart())
    app.dependency_overrides[get_query_bus] = lambda: bus
    app.dependency_overrides[authenticated_caller] = lambda: _WHO
    previously_metrics_enabled = app.state.metrics_enabled
    previously_questions_enabled = app.state.questions_enabled
    app.state.metrics_enabled = True
    app.state.questions_enabled = True
    try:
        client = TestClient(app)

        drawn = client.post("/v1/questions", json={"question": "¿Cuánto gasté el mes pasado?"})
        sealed_conversation = drawn.json()["conversation"]
        tampered_chart_id = sealed_conversation["visual_contexts"][0]["chart_id"]
        tampered = copy.deepcopy(sealed_conversation)
        was_comparison = tampered["visual_contexts"][0]["profile"]["comparison"]
        tampered["visual_contexts"][0]["profile"]["comparison"] = not was_comparison

        compound = client.post(
            "/v1/questions",
            json={"question": "Cuanto tengo y cuanto gaste este mes?", "conversation": tampered},
        )
        assert compound.status_code == 200
        assert compound.json()["outcome"] == "COMPOUND"
        by_chart_id = {
            context["chart_id"]: context
            for context in compound.json()["conversation"]["visual_contexts"]
        }
        carried = by_chart_id[tampered_chart_id]
        assert carried["token"] is None

        # Isolated exactly as the next real turn's `_conversation_of` will see it: a lone chart in
        # the echoed conversation, still tokenless from the compound round trip.
        isolated = copy.deepcopy(compound.json()["conversation"])
        isolated["visual_contexts"] = [carried]
        restyle = client.post(
            "/v1/questions",
            json={"question": "ordénalo de menor a mayor", "conversation": isolated},
        )
        assert restyle.json()["outcome"] == "VISUAL_UNTRUSTED"
    finally:
        app.dependency_overrides.clear()
        app.state.metrics_enabled = previously_metrics_enabled
        app.state.questions_enabled = previously_questions_enabled


# --- Task 2 (Wave 4.2b): the reader splits by names and regroups by calendar -------------------


def test_a_two_category_split_re_queries_the_plan_in_view_by_those_categories() -> None:
    previous = CHARTED_SUMMER.last_plan
    assert previous is not None
    plan = read_contextual_follow_up(
        _query("Separa transporte público y gasolina.", conversation=CHARTED_SUMMER)
    )
    assert plan is not None
    assert plan.categories == ("TRANSPORT_PUBLIC", "TRANSPORT_FUEL")
    assert plan.group_by is GroupBy.CATEGORY
    assert plan.period == previous.period and plan.date_range == previous.date_range
    assert plan.direction == previous.direction
    assert plan.only_account_id == previous.only_account_id


def test_a_regroup_by_months_turns_the_plan_in_view_into_a_series() -> None:
    previous = SPLIT_SUMMER.last_plan
    assert previous is not None
    plan = read_contextual_follow_up(
        _query("Ahora compáralo por meses.", conversation=SPLIT_SUMMER)
    )
    assert plan is not None and plan.group_by is GroupBy.MONTH
    assert plan.categories == previous.categories
    assert plan.period == previous.period and plan.date_range == previous.date_range
    assert plan.direction == previous.direction


def test_a_one_name_split_still_declines() -> None:
    assert (
        read_contextual_follow_up(_query("Separa la gasolina.", conversation=CHARTED_SUMMER))
        is None
    )
