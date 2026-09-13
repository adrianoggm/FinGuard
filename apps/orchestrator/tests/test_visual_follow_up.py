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
from support import episodes
from support.visual_planners import BREAKDOWN_PROFILE
from test_decisions_call_sites import ACCOUNT as CS_ACCOUNT
from test_decisions_call_sites import TODAY as CS_TODAY
from test_decisions_call_sites import FakeLedger, FixedInterpreter, handler_for, turn_for

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
from finguard_orchestrator.domain.constants import MAX_VISUAL_CONTEXTS
from finguard_orchestrator.domain.enums import Direction, GroupBy, PaymentOutcome, Period
from finguard_orchestrator.domain.models.chart import (
    DEFAULT_PRESENTATION,
    VisualContext,
    VisualDataset,
    VisualRow,
)
from finguard_orchestrator.domain.models.conversation import IDLE, Conversation, PaymentConfirmation
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
from finguard_orchestrator.domain.services.visual_explanation import explain_chart
from finguard_orchestrator.domain.services.visual_interpreter import VisualIntent, interpret_visual
from finguard_orchestrator.domain.services.visual_planning_policy import VisualPlanningPolicy
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
    ConversationPayload,
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
    vocabulary here, so a typo stops silently rather than passing a chart's own follow-up through
    unrecognised. `llm_mapper.py`'s own `derived_from` no longer reads `visual_follow_up.kind` at
    all (B-3, Wave 3 hardening: the fact, not the verb) so it has nothing left to pin here."""
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
    #
    # A-1 (Wave 3 hardening): sealed against `_CONVERSATION_ID` -- the id `_charted_request` below
    # stamps onto the payload -- and each context's own index in `CHARTED.visual_contexts`, so the
    # seal verifies against the exact conversation and position `_conversation_of` re-derives from
    # the wire.
    visual_contexts=tuple(
        conversation_mapper.seal_context(
            context, _CONVERSATION_ID, position, _WHO.holder_reference, _TEST_TOKENS
        )
        for position, context in enumerate(CHARTED.visual_contexts)
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


async def test_a_financial_ask_beside_a_sort_word_reaches_the_planner_not_a_restyle() -> None:
    """F-H1 (Wave 3 hardening, fix round): «Ordena mis gastos por categoría y dime el total»
    carries a financial word ("gastos") and a style word ("ordena") together with a real
    data-asking clause ("dime el total") that neither `_FINANCIAL_QUERY_SIGNAL` (no window, no
    comparison verb) nor `category_words.named_in` (no named category, only the generic axis
    noun "categoría") catches. A-10's own fix left no `else` for this residue in
    `interpret_visual`, so the turn fell all the way through to the ordinary restyle path and the
    controller answered a sort patch (`VISUAL_UPDATED`) with zero ledger calls, discarding the
    actual question. It must fall through to the planner instead, exactly like any other real
    financial question would -- driven through the controller's route function directly (the
    same seam `test_the_controllers_visual_short_circuit_records_the_kind_and_claimant` uses)
    since the bug is in the controller's own short-circuit gate, not in the handler.
    """
    draws = _DrawsOneChart()
    bus = QueryBus()
    bus.register(AskFinancialQuestionQuery, draws)
    response = await ask_question(
        _charted_request("Ordena mis gastos por categoría y dime el total"),
        _WHO,
        queries=bus,
        today=CS_TODAY,
        commands=None,
        payments_on=False,
        attempt="x",
        reads=FixedInterpreter(Intent.FINANCIAL_QUERY),
        metric_tokens=_TEST_TOKENS,
        metrics_on=False,
    )
    assert len(draws.calls) == 1
    assert response.outcome not in ("VISUAL_UPDATED", "VISUAL_AMBIGUOUS")


async def test_the_controllers_visual_response_is_unchanged_by_recording(monkeypatch) -> None:
    """No visible change, on the controller's own short-circuit: the caller gets exactly the same
    response whether or not a recording turn happens to be open around it."""
    monkeypatch.setattr(llm_controller.uuid, "uuid4", lambda: _MINTED_ID)

    with recording_turn():
        recorded = await _drive_question_route("Ordénalo de mayor a menor")
    unrecorded = await _drive_question_route("Ordénalo de mayor a menor")

    assert recorded == unrecorded


# --- Task 3 (Wave 3 hardening, C-1/C-6/C-3): the restyle short-circuit needs a chart and yields
# to an open payment ------------------------------------------------------------------------------


def test_a_first_turn_naming_a_chart_style_reaches_the_planner_not_visual_ambiguous() -> None:
    """C-1: a brand-new conversation has no `visual_contexts` at all. «Compara junio y mayo en
    barras» names both a financial comparison and a chart style -- `interpret_visual` used to
    answer this `VISUAL_AMBIGUOUS` (a question about a chart that does not exist) whether or not
    any chart was on screen, because the mixed-turn refusal fires before the interpreter's own
    `if not contexts` guard ever runs. Gated on `was.visual_contexts` the same way `asks_why`
    already is, the turn now falls through to the handler -- proven here by a real `QueryBus` and
    `TestClient` round trip that reaches `_DrawsOneChart` exactly once.
    """
    draws = _DrawsOneChart()
    bus = QueryBus()
    bus.register(AskFinancialQuestionQuery, draws)
    app.dependency_overrides[get_query_bus] = lambda: bus
    app.dependency_overrides[authenticated_caller] = lambda: _WHO
    previously_questions_enabled = app.state.questions_enabled
    app.state.questions_enabled = True
    try:
        client = TestClient(app)
        response = client.post("/v1/questions", json={"question": "Compara junio y mayo en barras"})
    finally:
        app.dependency_overrides.clear()
        app.state.questions_enabled = previously_questions_enabled

    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] != "VISUAL_AMBIGUOUS"
    assert len(draws.calls) == 1


def test_a_bare_all_reply_with_no_chart_falls_through_to_the_clarification() -> None:
    """C-3: with no chart in context, «todos» used to be claimed by `_VISUAL_ALL` and answered
    `VISUAL_INCOMPATIBLE` («No hay ningún gráfico anterior que pueda cambiar.») -- a sentence about
    a chart the holder never mentioned, and the clarification it was actually answering (which
    lives in the handler, not the controller) never saw it. Gated on `was.visual_contexts`, the
    turn now falls through to the handler exactly like any other reply would.
    """
    draws = _DrawsOneChart()
    bus = QueryBus()
    bus.register(AskFinancialQuestionQuery, draws)
    app.dependency_overrides[get_query_bus] = lambda: bus
    app.dependency_overrides[authenticated_caller] = lambda: _WHO
    previously_questions_enabled = app.state.questions_enabled
    app.state.questions_enabled = True
    try:
        client = TestClient(app)
        response = client.post("/v1/questions", json={"question": "todos"})
    finally:
        app.dependency_overrides.clear()
        app.state.questions_enabled = previously_questions_enabled

    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] != "VISUAL_INCOMPATIBLE"
    assert len(draws.calls) == 1


def test_f3_an_uncharted_why_is_answered_not_planned() -> None:
    """F-3 (fix wave, Important 6): «¿por qué este gráfico?» with no chart anywhere in context
    used to fall through to the planner (`asks_why` gated on `was.visual_contexts`) and come back
    a confident total for a question about a chart that was never drawn -- the exact
    wrong-but-confident shape the whole wave exists to close. Zero calls now, an honest answer."""
    draws = _DrawsOneChart()
    bus = QueryBus()
    bus.register(AskFinancialQuestionQuery, draws)
    app.dependency_overrides[get_query_bus] = lambda: bus
    app.dependency_overrides[authenticated_caller] = lambda: _WHO
    previously_questions_enabled = app.state.questions_enabled
    app.state.questions_enabled = True
    try:
        client = TestClient(app)
        response = client.post("/v1/questions", json={"question": "¿por qué este gráfico?"})
    finally:
        app.dependency_overrides.clear()
        app.state.questions_enabled = previously_questions_enabled

    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "VISUAL_NEEDS_TARGET"
    assert body["text"] == "No hay ningún gráfico en pantalla todavía; pregúntame algo y lo dibujo."
    assert len(draws.calls) == 0


def test_f3_a_bare_restyle_with_no_chart_at_all_is_refused_not_planned() -> None:
    """F-3 (fix wave, Important 6): a bare style instruction («ponlo en barras») with nothing on
    screen at all -- a KPI answer, or nothing yet -- used to fall through to the planner and
    invent a window for a total nobody asked for. `show_every_row` ("todos"/"sin límite") is
    untouched (C-3, above): only a genuine style/mark instruction is refused here."""
    draws = _DrawsOneChart()
    bus = QueryBus()
    bus.register(AskFinancialQuestionQuery, draws)
    app.dependency_overrides[get_query_bus] = lambda: bus
    app.dependency_overrides[authenticated_caller] = lambda: _WHO
    previously_questions_enabled = app.state.questions_enabled
    app.state.questions_enabled = True
    try:
        client = TestClient(app)
        response = client.post("/v1/questions", json={"question": "ponlo en barras"})
    finally:
        app.dependency_overrides.clear()
        app.state.questions_enabled = previously_questions_enabled

    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "VISUAL_INCOMPATIBLE"
    assert body["text"] == "Ese dato no tiene gráfico que cambiar."
    assert len(draws.calls) == 0


async def test_a_visual_word_during_an_open_payment_preview_stays_in_the_payment_branch() -> None:
    """C-6: a payment preview is open (`PaymentConfirmation`) and a chart is also on screen. A
    reply carrying only a visual word («ponlo en barras») used to be claimed by `interpret_visual`
    before the controller ever looked at `was.active`, so the restyle answered, the preview was
    left open, and the confirmation reader never saw the sentence. With `interpret_visual` moved
    below the `PaymentConfirmation`/`PaymentInProgress` branches, the same reply now stays in the
    payment branch (`payments_on=False` here makes `_as_confirmation` answer `UNSUPPORTED`
    immediately, without touching `commands`) and never reaches the query bus.
    """
    fake_queries = _UnreachableQueries()
    previewing = replace(
        _SEALED_CHARTED,
        active=PaymentConfirmation(
            intent_id=UUID(int=7), recipient="Marta Solis Vega", amount_minor=1_500
        ),
    )
    payload = conversation_mapper.to_payload(previewing).model_copy(
        update={"conversation_id": _CONVERSATION_ID}
    )
    request = QuestionRequest(question="ponlo en barras", conversation=payload)

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

    assert response.outcome == PaymentOutcome.UNSUPPORTED.value
    assert not response.outcome.startswith("VISUAL_")
    assert fake_queries.ran == []


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


# --- Task 8b (Wave 3 hardening): B-9 -- a size/shape word is refused honestly, and «tarta» joins
# «circular»/«donut»/«anillo» as a real restyle --------------------------------------------------


async def _streamed_answer(request: QuestionRequest, *, metrics_on: bool = False) -> dict:
    streamed = await ask_streaming(
        request,
        _WHO,
        asking=None,
        today=CS_TODAY,
        commands=None,
        payments_on=False,
        queries=_UnreachableQueries(),
        attempt="x",
        reads=None,
        metric_tokens=_TEST_TOKENS,
        metrics_on=metrics_on,
    )
    chunks = [chunk async for chunk in streamed.body_iterator]
    body = "".join(chunk.decode() if isinstance(chunk, bytes) else chunk for chunk in chunks)
    (line,) = [line for line in body.splitlines() if line.startswith("data: ")]
    return json.loads(line.removeprefix("data: "))


async def test_a_size_word_refuses_honestly_never_reaches_the_ledger() -> None:
    """B-9, item 1: "hazlo más grande" used to fall through unclaimed (no word in the visual
    vocabulary recognised it at all) and reach the model for an unrelated total. It now refuses
    honestly, zero calls, naming what CAN change instead of the chart's own size."""
    payload = await _streamed_answer(_charted_request("hazlo más grande"))

    assert payload["outcome"] == "VISUAL_INCOMPATIBLE"
    assert "tamaño lo decide la pantalla" in payload["text"].lower()
    assert payload["about_chart_id"] == "chart-1"


async def test_a_pequeno_size_word_refuses_the_same_way() -> None:
    payload = await _streamed_answer(_charted_request("hazlo más pequeño"))

    assert payload["outcome"] == "VISUAL_INCOMPATIBLE"
    assert "tamaño lo decide la pantalla" in payload["text"].lower()


@pytest.mark.parametrize("word", ["tarta", "pastel", "quesito"])
def test_tarta_and_its_synonyms_read_as_a_real_donut_mark(word: str) -> None:
    """ "Tarta"/"pastel"/"quesito" join "circular"/"donut"/"anillo" as the same mark change -- a
    real patch (`intent.patch.mark == "donut"`), never the B-9 refusal (`intent.explanation`).
    Whether a *particular* chart's profile survives the verifier's own donut eligibility rule
    (`_donut_claims`, `test_visualization_stage2_core.py`) is a separate question this reader
    does not decide -- it only proposes the mark, exactly like "circular"/"donut" already did."""
    intent = interpret_visual(f"ponlo en {word}", _SEALED_CHARTED.visual_contexts)

    assert intent is not None
    assert intent.explanation is None
    assert intent.patch is not None
    assert intent.patch.mark == "donut"


# --- Task 8b: item 2 -- "guarda este gráfico" is answered from the chart, never the ledger -----
#
# `wants_to_save_chart` is asserted through the streaming route (below); `_save_response` itself
# is exercised directly here, the same pattern `test_visualization_stage2_core.py` uses for
# `_visual_only_response` -- a chart whose `source_plan` a real turn would have stamped, which
# `_SEALED_CHARTED`'s bare fixture never claimed to (D-3b-9's own docstring: it is a spec and a
# profile, not a turn's whole provenance).


def test_guarda_este_grafico_is_answered_from_the_chart_metrics_on() -> None:
    context = replace(CHARTED.visual_contexts[0], source_plan=PREVIOUS)
    conversation = replace(CHARTED, visual_contexts=(context,))

    response = llm_controller._save_response(
        conversation,
        holder_reference=_WHO.holder_reference,
        metric_tokens=_TEST_TOKENS,
        metrics_on=True,
    )

    assert response.outcome == "VISUAL_UNCHANGED"
    assert "guardar" in response.text.lower()
    assert response.save_candidate is not None
    assert response.about_chart_id == "chart-1"


def test_guarda_este_grafico_refuses_honestly_when_metrics_are_off() -> None:
    context = replace(CHARTED.visual_contexts[0], source_plan=PREVIOUS)
    conversation = replace(CHARTED, visual_contexts=(context,))

    response = llm_controller._save_response(
        conversation,
        holder_reference=_WHO.holder_reference,
        metric_tokens=_TEST_TOKENS,
        metrics_on=False,
    )

    assert response.outcome == "VISUAL_INCOMPATIBLE"
    assert "guardar" in response.text.lower()
    assert response.save_candidate is None


async def test_guarda_este_grafico_reaches_the_controllers_short_circuit_over_the_stream() -> None:
    """The streaming route's own gate (`wants_to_save_chart(...) and was.visual_contexts`):
    zero ledger calls, and the sentence never reaches `interpret_visual`'s own restyle path."""
    payload = await _streamed_answer(_charted_request("guarda este grafico"), metrics_on=False)

    assert payload["outcome"] != "VISUAL_UPDATED"
    assert payload["outcome"] != "VISUAL_NEEDS_TARGET"
    assert "guardar" in payload["text"].lower()


# --- Task 8b: item 3 -- "vuelve al anterior"/"el de antes" over two or more charts asks rather
# than guessing which one was meant ---------------------------------------------------------------


async def test_vuelve_al_anterior_asks_over_two_charts() -> None:
    """Over `_SEALED_CHARTED` (one chart) alone, "vuelve al anterior" has nothing to go back to
    and falls through unclaimed -- the ordinary, untouched single-chart case."""
    payload = await _streamed_answer(_charted_request("vuelve al anterior"))

    assert payload["outcome"] != "VISUAL_AMBIGUOUS"
    assert payload["outcome"] != "VISUAL_INCOMPATIBLE"


async def test_vuelve_al_anterior_asks_with_two_charts_in_view() -> None:
    second = replace(_SEALED_CHARTED.visual_contexts[0], chart_id="chart-2")
    two_charts = _SEALED_CHARTED.with_visual_context(second)
    payload_conversation = conversation_mapper.to_payload(two_charts).model_copy(
        update={"conversation_id": _CONVERSATION_ID}
    )
    request = QuestionRequest(question="vuelve al anterior", conversation=payload_conversation)

    payload = await _streamed_answer(request)

    assert payload["outcome"] == "VISUAL_AMBIGUOUS"
    assert set(payload["suggestions"]) == {
        "Pon el gráfico anterior en barras",
        "Deshacer el último cambio",
    }


# --- Task 6: an AMBIGUOUS short-circuit asks instead of guessing -------------------------------
#
# Before this task, a claimed visual turn always went to `_visual_only_response`: the mixed
# sentence surfaced the interpreter's own refusal (`VISUAL_INCOMPATIBLE`), and the I-1 drill
# sentence was silently restyled (`VISUAL_UPDATED`) because `interpret_visual` alone had already
# claimed it. Both are the silent guess §5 says AMBIGUOUS exists to replace with a question.


class _UnreachableQueries:
    """A `Queries` double for the ask path: any call means a short-circuit fell through to the
    handler and its ledger. Two callers share it for the same reason -- the AMBIGUOUS branch must
    never reach the query bus (it decides nothing about the chart), and neither must a voice or
    text restyle's own short-circuit (zero banking calls is the whole point of a presentation-only
    change)."""

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
    # D-H-1: the drill lexicon resolves a target the way a restyle would; the interpreter's own
    # mixed-turn refusal (`presentation+reshape`) never does -- `VisualIntent(None, None, ...,
    # mixed=True)` carries no target at all, so there is nothing to name here either.
    expected_chart_id = None if claimant == "presentation+reshape" else "chart-1"
    assert response.about_chart_id == expected_chart_id
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
    assert payload["about_chart_id"] == "chart-1"  # D-H-1
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
    """I2, then A-1 (Wave 3 hardening): `_compound_read_reply`'s own
    `to_domain(rendered.conversation)` call now binds a real `verify=`, closing over
    `who.holder_reference` the same way `_conversation_of` does. Before this task, it passed none,
    and `to_domain`'s own default relaunder to `trusted=True` was inert only because nothing ever
    re-signed a context after this point -- a context that came in untrusted was also tokenless,
    and tokenless it stayed. Task 1's `reseal_visual_contexts` (this function's own final merge)
    reseals whatever `trusted` says by the time it runs, so leaving this call unverified would let
    a tampered chart carried into a compound turn walk out with a brand-new, genuinely valid seal
    -- exactly the gap this test now closes at the route. Proven end to end: a tampered chart
    carried into a compound turn comes out the other side of the round trip still tokenless, and
    restyling it alone on the next turn is still refused.
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


# --- Task 1 (Wave 3 hardening, A-1): the four tamper shapes, at the route ----------------------


def _draw_chart(client: TestClient, question: str, conversation: dict | None) -> dict:
    """One `/v1/questions` draw -- a sentence `interpret_visual` does not recognise, so the
    controller reaches the query bus and `_DrawsOneChart` appends a fresh, freshly-sealed chart
    rather than restyling whatever is already in view."""
    payload: dict = {"question": question}
    if conversation is not None:
        payload["conversation"] = conversation
    response = client.post("/v1/questions", json=payload)
    assert response.status_code == 200
    return response.json()["conversation"]


def _drive_chain(client: TestClient, months: tuple[str, ...]) -> dict:
    """`len(months)` charts, drawn one per turn into a single, growing conversation."""
    conversation: dict | None = None
    for month in months:
        conversation = _draw_chart(client, f"¿Cuánto gasté en {month}?", conversation)
    assert conversation is not None
    return conversation


@pytest.fixture
def _tamper_client():
    bus = QueryBus()
    bus.register(AskFinancialQuestionQuery, _DrawsOneChart())
    app.dependency_overrides[get_query_bus] = lambda: bus
    app.dependency_overrides[authenticated_caller] = lambda: _WHO
    previously_metrics_enabled = app.state.metrics_enabled
    previously_questions_enabled = app.state.questions_enabled
    app.state.metrics_enabled = True
    app.state.questions_enabled = True
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        app.state.metrics_enabled = previously_metrics_enabled
        app.state.questions_enabled = previously_questions_enabled


def test_a_replayed_context_from_another_conversation_is_untrusted(_tamper_client) -> None:
    """A-1: a genuinely-sealed context, minted by this very process for this very holder, but for
    a DIFFERENT conversation, spliced into this one -- the seal's own conversation id no longer
    matches, so it is refused with the "not this conversation" text rather than the tamper one."""
    client = _tamper_client
    conversation_a = _draw_chart(client, "¿Cuánto gasté en enero?", None)
    conversation_b = _draw_chart(client, "¿Cuánto gasté en febrero?", None)
    assert conversation_a["conversation_id"] != conversation_b["conversation_id"]

    foreign_context = conversation_a["visual_contexts"][0]
    spliced = copy.deepcopy(conversation_b)
    spliced["visual_contexts"].append(foreign_context)

    response = client.post(
        "/v1/questions",
        json={"question": "¿Por qué el segundo gráfico?", "conversation": spliced},
    )
    body = response.json()
    assert response.status_code == 200
    assert body["outcome"] == "VISUAL_UNTRUSTED"
    assert (
        body["text"] == "Ese gráfico no es de esta conversación; pide uno nuevo y podré cambiarlo."
    )
    # The surviving, genuinely-this-conversation's own context is untouched.
    own_context = next(
        c
        for c in body["conversation"]["visual_contexts"]
        if c["chart_id"] != foreign_context["chart_id"]
    )
    assert own_context["token"] is not None


def test_reordering_two_trusted_contexts_untrusts_both(_tamper_client) -> None:
    """A-1: two contexts this process sealed, each for its own position -- swapping their order in
    the echoed list leaves both seals pointing at a position that is no longer theirs, so both
    fail, not just one."""
    client = _tamper_client
    conversation = _drive_chain(client, ("enero", "febrero"))
    contexts = conversation["visual_contexts"]
    assert len(contexts) == 2
    reordered = copy.deepcopy(conversation)
    reordered["visual_contexts"] = [contexts[1], contexts[0]]

    response = client.post(
        "/v1/questions",
        json={"question": "¿Por qué el primer gráfico?", "conversation": reordered},
    )
    assert response.status_code == 200
    assert response.json()["outcome"] == "VISUAL_UNTRUSTED"
    survivors = response.json()["conversation"]["visual_contexts"]
    assert all(context["token"] is None for context in survivors)


def test_duplicating_the_newest_context_is_refused_before_the_seal_even_runs(
    _tamper_client,
) -> None:
    """A-1: literally duplicating an entry (the same `chart_id` twice) never reaches the trust
    gate at all -- `Conversation.__post_init__`'s own chart-id-uniqueness invariant (`domain/
    models/conversation.py`) refuses it at the edge as a 422, a stronger and earlier defence than
    an untrusted verdict. The shape the seal's position binding actually has to catch is a
    *distinct* context minted for one position and echoed at another -- proven directly by
    `test_reordering_two_trusted_contexts_untrusts_both` and `test_dropping_the_middle_context_
    shifts_and_untrusts_the_last` above and below: both move a genuinely-sealed context to an
    index its seal does not name, without needing two contexts to share an id to do it."""
    client = _tamper_client
    conversation = _drive_chain(client, ("enero", "febrero"))
    contexts = conversation["visual_contexts"]
    assert len(contexts) == 2
    duplicated = copy.deepcopy(conversation)
    duplicated["visual_contexts"] = [contexts[0], contexts[1], copy.deepcopy(contexts[1])]

    response = client.post(
        "/v1/questions",
        json={"question": "¿Por qué el primer gráfico?", "conversation": duplicated},
    )
    assert response.status_code == 422


def test_dropping_the_middle_context_shifts_and_untrusts_the_last(_tamper_client) -> None:
    """A-1: dropping a middle context shifts every later one down by a position its seal does not
    name -- exactly the shape A-1 calls out by name (a category drill's own middle-drop hazard).
    The untouched first survivor, whose position never moved, stays trusted."""
    client = _tamper_client
    conversation = _drive_chain(client, ("enero", "febrero", "marzo"))
    contexts = conversation["visual_contexts"]
    assert len(contexts) == 3
    dropped = copy.deepcopy(conversation)
    dropped["visual_contexts"] = [contexts[0], contexts[2]]

    response = client.post(
        "/v1/questions",
        json={"question": "¿Por qué el primer gráfico?", "conversation": dropped},
    )
    assert response.status_code == 200
    assert response.json()["outcome"] == "VISUAL_EXPLAINED"
    survivors = response.json()["conversation"]["visual_contexts"]
    assert survivors[0]["token"] is not None, (
        "the untouched first chart, still at index 0, stays trusted"
    )
    assert survivors[1]["token"] is None, "the shifted last chart, now at index 1, is not"


def test_an_old_pre_hardening_token_is_untrusted_not_a_500(_tamper_client) -> None:
    """A-1: a `c1.` seal -- the only shape a pre-hardening client could ever have held -- still
    matches the wire's widened token pattern (`c1` or `c2`), so it reaches `verify_context` rather
    than being refused as a malformed request; `verify_context` opens it, finds its signed `"t"`
    is `"c1"` and not `"c2"`, and returns `"format"`. The turn still completes and answers
    `VISUAL_UNTRUSTED`, never a 500."""
    client = _tamper_client
    conversation = _draw_chart(client, "¿Cuánto gasté en enero?", None)
    stale = copy.deepcopy(conversation)
    # A syntactically well-formed `c1.<body>.<sig>` token nothing here ever issued -- standing in
    # for a seal minted by this same process before the `c2.` prefix existed.
    stale["visual_contexts"][0]["token"] = "c1." + "A" * 20 + "." + "B" * 20

    response = client.post(
        "/v1/questions",
        json={"question": "¿Por qué este gráfico?", "conversation": stale},
    )
    assert response.status_code == 200
    assert response.json()["outcome"] == "VISUAL_UNTRUSTED"


def test_eviction_reseals_survivors_so_a_long_conversation_stays_trusted(_tamper_client) -> None:
    """A-1's own design decision: once `MAX_VISUAL_CONTEXTS` is reached, appending a new chart
    evicts the oldest and shifts every survivor's position down by one. Without a reseal at
    eviction, every survivor's seal would immediately fail its own position check on the very next
    turn -- not because of any replay or tampering, but because the list it was sealed against no
    longer exists. Driving `MAX_VISUAL_CONTEXTS + 2` charts through the fake route and then
    restyling the OLDEST surviving chart proves the reseal happened: a stale seal would answer
    `VISUAL_UNTRUSTED` here instead.
    """
    client = _tamper_client
    months = (
        "enero",
        "febrero",
        "marzo",
        "abril",
        "mayo",
        "junio",
        "julio",
    )[: MAX_VISUAL_CONTEXTS + 2]
    conversation = _drive_chain(client, months)
    contexts = conversation["visual_contexts"]
    assert len(contexts) == MAX_VISUAL_CONTEXTS
    assert all(context["token"] is not None for context in contexts), (
        "every survivor was resealed at eviction, not just the newest"
    )

    restyled = client.post(
        "/v1/questions",
        json={"question": "¿Por qué el primer gráfico?", "conversation": conversation},
    )
    assert restyled.status_code == 200
    assert restyled.json()["outcome"] == "VISUAL_EXPLAINED"


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


def test_a_clitic_and_preposition_still_split_by_two_named_categories() -> None:
    """Item 5 (Wave 3 hardening, T8b, "the eighth handed-over row"): "sepáralo en transporte
    público y gasolina" is six words where "Separa transporte público y gasolina" (the sibling
    test above) is five -- the ellipsis-length gate used to return `None` before the
    split-by-names branch, below it, ever ran. Moved ahead of that gate; the match itself is
    unchanged."""
    plan = read_contextual_follow_up(
        _query("sepáralo en transporte público y gasolina", conversation=CHARTED_SUMMER)
    )
    assert plan is not None
    assert plan.categories == ("TRANSPORT_PUBLIC", "TRANSPORT_FUEL")
    assert plan.group_by is GroupBy.CATEGORY


def test_ponme_solo_names_a_single_category_filter() -> None:
    """Item 4 (Wave 3 hardening, T8b): "ponme solo restaurantes" is the same category-only
    filter "solo restaurantes" already reads, said as an imperative to the assistant rather than
    as a bare fragment."""
    plan = read_contextual_follow_up(_query("ponme solo restaurantes", conversation=CHARTED_SUMMER))
    assert plan is not None
    assert plan.categories == ("RESTAURANTS",)
    assert plan.merchants == ()


# --- Wave 4.5, Task 4: «¿Por qué este gráfico?» is answered from the chart, before
# `interpret_visual` ever runs ---------------------------------------------------------------
#
# R4/R5/R6/R10: a claimed "why" turn with a chart to explain answers `VISUAL_EXPLAINED` -- zero
# ledger calls, zero model calls, the conversation unchanged. Two charts, sealed the same way
# `_SEALED_CHARTED` is, so «el primer gráfico» has something to disambiguate.
_FIRST_CONTEXT = replace(
    CHARTED.visual_contexts[0],
    chart_id="chart-first",
    window=AbsoluteRange(date(2026, 1, 1), date(2026, 1, 31)),
)
_SECOND_CONTEXT = replace(
    CHARTED.visual_contexts[0],
    chart_id="chart-second",
    window=AbsoluteRange(date(2026, 6, 1), date(2026, 6, 30)),
)
_TWO_CHARTED = (
    Conversation(last_plan=PREVIOUS)
    .with_visual_context(_FIRST_CONTEXT)
    .with_visual_context(_SECOND_CONTEXT)
)
_SEALED_TWO_CHARTED = replace(
    _TWO_CHARTED,
    visual_contexts=tuple(
        conversation_mapper.seal_context(
            context, _CONVERSATION_ID, position, _WHO.holder_reference, _TEST_TOKENS
        )
        for position, context in enumerate(_TWO_CHARTED.visual_contexts)
    ),
)


def _two_charted_request(sentence: str) -> QuestionRequest:
    payload = conversation_mapper.to_payload(_SEALED_TWO_CHARTED).model_copy(
        update={"conversation_id": _CONVERSATION_ID}
    )
    return QuestionRequest(question=sentence, conversation=payload)


def _decide_spy(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """Same shape as `test_performance_objectives.py`'s own spy: patches
    `VisualPlanningPolicy.decide` on the class so any call at all -- from any instance, including
    a handler's own default policy -- is recorded, while still running the real body."""
    calls: list[object] = []
    original = VisualPlanningPolicy.decide

    async def spy(self: object, *args: object, **kwargs: object) -> object:
        calls.append(self)
        return await original(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(VisualPlanningPolicy, "decide", spy)
    return calls


@pytest.mark.parametrize("channel", ["TEXT", "VOICE"])
async def test_the_question_route_explains_the_newest_chart(
    channel: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """«¿Por qué este gráfico?» answers `VISUAL_EXPLAINED` from `explain_chart` alone -- no ledger
    call, no model call, the conversation echoed back unchanged -- on both TEXT and VOICE."""
    fake_queries = _UnreachableQueries()
    calls = _decide_spy(monkeypatch)
    request = _charted_request("¿Por qué este gráfico?")
    request = request.model_copy(update={"channel": channel})

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

    # `row_count` never travels the wire (`conversation_mapper.to_domain`, deliberately) -- the
    # expected text is computed off the round-tripped context the server actually reconstructed,
    # not off the in-process fixture.
    newest = conversation_mapper.to_domain(response.conversation).visual_contexts[-1]
    assert response.outcome == "VISUAL_EXPLAINED"
    assert response.text == explain_chart(newest).as_text()
    assert response.explanation is not None
    assert response.explanation.text == response.text
    assert fake_queries.ran == []
    assert calls == []
    assert response.conversation == request.conversation
    assert response.spoken is None
    assert response.visualization is None
    assert response.visualization_derivation is None
    assert response.about_chart_id == newest.chart_id  # D-H-1


@pytest.mark.parametrize("channel", ["TEXT", "VOICE"])
async def test_the_streaming_route_explains_the_newest_chart_in_one_frame(
    channel: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_queries = _UnreachableQueries()
    calls = _decide_spy(monkeypatch)
    request = _charted_request("¿Por qué este gráfico?")
    request = request.model_copy(update={"channel": channel})

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
    frames = [
        json.loads(line.removeprefix("data: "))
        for line in body.splitlines()
        if line.startswith("data: ")
    ]

    assert len(frames) == 1
    assert frames[0]["type"] == "answer"
    assert frames[0]["outcome"] == "VISUAL_EXPLAINED"
    newest = conversation_mapper.to_domain(
        ConversationPayload.model_validate(frames[0]["conversation"])
    ).visual_contexts[-1]
    assert frames[0]["text"] == explain_chart(newest).as_text()
    assert fake_queries.ran == []
    assert calls == []
    assert frames[0]["about_chart_id"] == newest.chart_id  # D-H-1


async def test_por_que_el_primer_grafico_explains_the_first_of_two() -> None:
    """«¿Por qué el primer gráfico?» over two contexts explains `_FIRST_CONTEXT`, not the newest
    one -- a distinguishing line (the window it draws) says which."""
    fake_queries = _UnreachableQueries()
    response = await ask_question(
        _two_charted_request("¿Por qué el primer gráfico?"),
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

    assert response.outcome == "VISUAL_EXPLAINED"
    first_sealed = conversation_mapper.to_domain(response.conversation).visual_contexts[0]
    assert response.text == explain_chart(first_sealed).as_text()
    assert "enero" in response.text
    assert fake_queries.ran == []
    assert response.about_chart_id == first_sealed.chart_id  # D-H-1: the first, not the newest


async def test_a_forged_newest_context_answers_untrusted_not_explained() -> None:
    """An echoed context whose seal does not check out is refused before it is ever explained --
    the same `VISUAL_UNTRUSTED` shape a restyle gets, not a new one."""
    fake_queries = _UnreachableQueries()
    request = _charted_request("¿Por qué este gráfico?")
    context = request.conversation.visual_contexts[0]
    context.profile.comparison = not context.profile.comparison

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

    assert response.outcome == "VISUAL_UNTRUSTED"
    assert fake_queries.ran == []
    assert response.about_chart_id == context.chart_id  # D-H-1: named even though untrusted


async def test_a_target_that_resolves_to_nothing_answers_needs_target() -> None:
    """«¿Por qué el segundo gráfico?» over one chart names a chart that is not there -- the same
    `VISUAL_NEEDS_TARGET` shape a restyle gets."""
    fake_queries = _UnreachableQueries()
    response = await ask_question(
        _charted_request("¿Por qué el segundo gráfico?"),
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

    assert response.outcome == "VISUAL_NEEDS_TARGET"
    assert fake_queries.ran == []
    # M-8 (fix wave): the explanation path's own wording -- the holder asked why a chart looks
    # the way it does, never asked to change one, so the text must not claim otherwise (the
    # restyle path below keeps "quieres cambiar", which IS a change request).
    assert response.text == "Indica de qué gráfico hablas."
    assert response.about_chart_id is None  # D-H-1: no chart resolved -- that is the question


def test_no_chart_at_all_is_answered_honestly_not_planned() -> None:
    """F-3 (fix wave, Important 6) supersedes this row's old premise (task-4-report.md): with
    nothing charted, `asks_why` used to have nothing to explain and fall through to the planner --
    over `episodes.a_stack()`'s three accounts, an ordinary financial question asking which one
    first. That is the exact wrong-but-confident shape the wave exists to close for a question
    about a chart that was never drawn: it is now answered honestly, at zero ledger calls, before
    the planner ever sees it."""
    stack = episodes.a_stack()
    try:
        response = stack.client.post("/v1/questions", json={"question": "¿Por qué este gráfico?"})
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json()["outcome"] == "VISUAL_NEEDS_TARGET"
    assert (
        response.json()["text"]
        == "No hay ningún gráfico en pantalla todavía; pregúntame algo y lo dibujo."
    )
