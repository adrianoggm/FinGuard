"""Every visual follow-up is one of five kinds, and a deterministic reader names which."""

from __future__ import annotations

from datetime import date
from typing import get_args
from uuid import UUID

import pytest
from support.visual_planners import BREAKDOWN_PROFILE

from finguard_orchestrator.application.queries.financial_turn import AskFinancialQuestionQuery
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
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan, RelativePeriod
from finguard_orchestrator.infrastructure.visualization.interpreter import VisualIntent
from finguard_orchestrator.observability import recording_turn

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
    """`classify` must not route through the `@_recogniser` seam: a read-only classifier that
    records a RECOGNISER/CLAIMED decision, or memoises the *outer* reader's own name onto the
    frozen query, is not pure, and the module docstring says it is.

    `_internal_movement_request` is a nested consultation `read_contextual_follow_up` has always
    made, regardless of this task, and is not what this test is about; `contextual_follow_up_plan`
    and its precondition key are — they are the seam this classifier must never enter, and were
    both present (along with a recorded decision) before the split into an undecorated body.
    """
    reshape_query = _query("Solo restaurantes")
    with recording_turn() as turn:
        result = classify(reshape_query, FOLLOW_UP, CHARTED)
    assert result == VisualFollowUp("ANALYTICAL_RESHAPE", "follow_up_plan")
    assert turn.sealed().decisions == ()
    reshape_memo = getattr(reshape_query, "_finguard_reader_memo", {})
    assert "contextual_follow_up_plan" not in reshape_memo
    assert "when:contextual_follow_up_plan" not in reshape_memo

    presentation_query = _query("Ponlo apilado")
    with recording_turn() as turn:
        result = classify(presentation_query, FOLLOW_UP, CHARTED)
    assert result == VisualFollowUp("PRESENTATION_ONLY", "interpret_visual")
    assert turn.sealed().decisions == ()
    presentation_memo = getattr(presentation_query, "_finguard_reader_memo", {})
    assert "contextual_follow_up_plan" not in presentation_memo
    assert "when:contextual_follow_up_plan" not in presentation_memo


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
