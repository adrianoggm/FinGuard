"""Every visual follow-up is one of five kinds, and a deterministic reader names which."""

from __future__ import annotations

import json
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
from finguard_orchestrator.application.queries.readers.visual_follow_up import (
    classify,
    classify_claimed_visual,
)
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
from finguard_orchestrator.domain.services.visual_interpreter import VisualIntent, interpret_visual
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


async def test_the_result_carries_the_follow_up_when_a_chart_was_in_context() -> None:
    """Task 4: `_record_visual_follow_up`'s return travels onto the result the handler yields --
    the same seam `test_a_charted_turn_records_its_kind_and_claimant` proves is recorded."""
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
    ("muéstrame los movimientos ordenados de mayor a menor", "presentation+drill"),
    # The interpreter's own mixed refusal -- a data change and a format change in one turn.
    ("Ponlo en barras y solo restaurantes", "presentation+reshape"),
]


@pytest.mark.parametrize(("sentence", "claimant"), ASK_ROWS)
async def test_an_ambiguous_short_circuit_asks_instead_of_restyling(sentence, claimant) -> None:
    """§5's row: a claimed turn two readers agree on gets a question, not a guess -- zero ledger
    calls, the conversation returned exactly as it arrived (nothing merged), and the one
    VISUAL_FOLLOW_UP row the recording seam always writes for a claimed turn."""
    fake_queries = _UnreachableQueries()
    request = _charted_request(sentence)
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
            metric_tokens=None,
            metrics_on=False,
        )

    assert response.outcome == "VISUAL_AMBIGUOUS"
    assert response.text == llm_controller._ASK[claimant]
    assert response.conversation == request.conversation
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
            metric_tokens=None,
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
    the same branch point that now also has to recognise AMBIGUOUS."""
    response = await _drive_question_route("Ordénalo de mayor a menor")
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
