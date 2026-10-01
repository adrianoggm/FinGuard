"""Intent, conversational relation and multiple objectives are separate facts."""

from datetime import date

import pytest
from support.visual_planners import BREAKDOWN_PROFILE
from test_visual_follow_up import SPEC

from finguard_orchestrator.domain.enums import Direction, Period
from finguard_orchestrator.domain.models.chart import VisualContext
from finguard_orchestrator.domain.models.conversation import Conversation
from finguard_orchestrator.domain.models.turn_analysis import TurnFocus, TurnRelation
from finguard_orchestrator.domain.services import turn_analyzer, turn_interpreters
from finguard_orchestrator.domain.services.intent_router import Intent
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan, RelativePeriod

TODAY = date(2026, 8, 10)
WITH_LAST_PLAN = Conversation(
    last_plan=FinancialPlan(
        direction=Direction.EXPENSE, period=RelativePeriod(period=Period.LAST_MONTH)
    )
)
IDLE = Conversation()


async def test_two_different_requests_are_kept_instead_of_silently_choosing_one() -> None:
    analysis = await turn_analyzer.analyze(
        "¿Cuánto tengo y cuánto gasté este mes?",
        conversation=Conversation(),
        interpreter=turn_interpreters.Deterministic(),
    )

    assert analysis.relation is TurnRelation.COMPOUND
    assert analysis.focus is TurnFocus.MULTIPLE_REQUESTS
    assert analysis.clauses == ("Cuánto tengo", "cuánto gasté este mes")


async def test_a_list_of_filters_is_not_mistaken_for_two_requests() -> None:
    analysis = await turn_analyzer.analyze(
        "¿Cuánto gasté en gasolina y supermercado este mes?",
        conversation=Conversation(),
        interpreter=turn_interpreters.Deterministic(),
    )

    assert not analysis.is_compound
    assert analysis.intent is Intent.FINANCIAL_QUERY


async def test_two_financial_questions_are_two_requests_even_with_the_same_intent() -> None:
    analysis = await turn_analyzer.analyze(
        "¿Cuánto gasté este mes y cuánto ingresé este año?",
        conversation=Conversation(),
        interpreter=turn_interpreters.Deterministic(),
    )

    assert analysis.is_compound
    assert analysis.clauses == ("Cuánto gasté este mes", "cuánto ingresé este año")


async def test_income_and_expenses_are_one_net_cash_flow_request() -> None:
    analysis = await turn_analyzer.analyze(
        "¿Cuál fue el balance entre ingresos y gastos este mes?",
        conversation=Conversation(),
        interpreter=turn_interpreters.Deterministic(),
    )

    assert not analysis.is_compound
    assert analysis.intent is Intent.FINANCIAL_QUERY


async def test_a_net_cash_flow_and_a_balance_are_two_complete_requests() -> None:
    analysis = await turn_analyzer.analyze(
        "¿Cuál fue mi flujo neto este mes y cuánto tengo?",
        conversation=Conversation(),
        interpreter=turn_interpreters.Deterministic(),
        today=TODAY,
    )

    assert analysis.is_compound
    assert analysis.clauses == ("Cuál fue mi flujo neto este mes", "cuánto tengo")


async def test_the_and_inside_a_comparison_is_not_split_from_its_two_windows() -> None:
    analysis = await turn_analyzer.analyze(
        "Compara mis gastos de junio y julio y dime cuánto tengo",
        conversation=Conversation(),
        interpreter=turn_interpreters.Deterministic(),
        today=TODAY,
    )

    assert analysis.is_compound
    assert analysis.clauses == (
        "Compara mis gastos de junio y julio",
        "dime cuánto tengo",
    )


async def test_a_savings_rate_can_share_a_turn_with_an_independent_read() -> None:
    analysis = await turn_analyzer.analyze(
        "¿Cuál fue mi tasa de ahorro este mes y cuánto gasté en supermercados?",
        conversation=Conversation(),
        interpreter=turn_interpreters.Deterministic(),
        today=TODAY,
    )

    assert analysis.is_compound
    assert analysis.clauses == (
        "Cuál fue mi tasa de ahorro este mes",
        "cuánto gasté en supermercados",
    )


async def test_manners_plus_a_request_are_not_two_requests() -> None:
    analysis = await turn_analyzer.analyze(
        "Hola y dime cuánto tengo",
        conversation=Conversation(),
        interpreter=turn_interpreters.Deterministic(),
    )

    assert not analysis.is_compound
    assert analysis.intent is Intent.ACCOUNT_BALANCE


async def test_an_ellipsis_is_marked_as_focused_on_the_last_answer() -> None:
    conversation = Conversation(
        last_plan=FinancialPlan(
            direction=Direction.EXPENSE, period=RelativePeriod(period=Period.LAST_MONTH)
        )
    )

    analysis = await turn_analyzer.analyze(
        "¿Y en gasolina?",
        conversation=conversation,
        interpreter=turn_interpreters.Deterministic(),
    )

    assert analysis.relation is TurnRelation.FOLLOW_UP
    assert analysis.focus is TurnFocus.LAST_ANSWER


@pytest.mark.parametrize(
    "sentence",
    [
        "Separa transporte público y gasolina.",
        "Ahora compáralo por meses.",
        "Desglósalo por comercio.",
        # M-9 (Wave 4.2b fix wave): this analyser claims it, but no reader does -- it is six
        # words, and `follow_up.py`'s ellipsis guard only inherits filters through five. The
        # assertion below is correct about this half of the pair; it is not a claim that some
        # reader also answers it as a follow-up.
        "Sepáralo en transporte público y gasolina",
    ],
)
async def test_a_charted_conversation_reads_a_reshape_as_a_follow_up(sentence: str) -> None:
    analysis = await turn_analyzer.analyze(
        sentence,
        conversation=WITH_LAST_PLAN,
        interpreter=turn_interpreters.Deterministic(),
        today=TODAY,
    )
    assert (analysis.relation, analysis.focus) == (TurnRelation.FOLLOW_UP, TurnFocus.LAST_ANSWER)


@pytest.mark.parametrize(
    "sentence",
    [
        "Separa transporte público y gasolina.",  # no plan in view: nothing to follow
        "Ahora compáralo por meses.",
    ],
)
async def test_without_a_plan_in_view_the_same_sentence_is_a_new_request(sentence: str) -> None:
    analysis = await turn_analyzer.analyze(
        sentence, conversation=IDLE, interpreter=turn_interpreters.Deterministic(), today=TODAY
    )
    assert analysis.relation is TurnRelation.NEW_REQUEST


async def test_a_split_that_names_one_category_is_not_a_follow_up() -> None:
    analysis = await turn_analyzer.analyze(
        "Separa la gasolina.",
        conversation=WITH_LAST_PLAN,
        interpreter=turn_interpreters.Deterministic(),
        today=TODAY,
    )
    assert analysis.relation is TurnRelation.NEW_REQUEST  # one name is a filter, not a split


# --- Task 3 (Wave 4.3): a drill-down naming a bar of the newest trusted chart is a follow-up ----

_MONTH_LABELS = ("2026-08", "2026-07", "2026-06")

# Newest (and only) chart, ranked -- «agosto» is the tallest bar, «junio» the shortest.
CHARTED_MONTHS = WITH_LAST_PLAN.with_visual_context(
    VisualContext("chart-1", SPEC, BREAKDOWN_PROFILE, labels=_MONTH_LABELS, ranked=True)
)
# The same chart, but the drawn ranking is not strict -- a tie at either end -- so «ese pico»
# resolves Ambiguous rather than Resolved; an ambiguous pointing still counts as a follow-up.
CHARTED_MONTHS_UNRANKED = WITH_LAST_PLAN.with_visual_context(
    VisualContext("chart-1", SPEC, BREAKDOWN_PROFILE, labels=_MONTH_LABELS, ranked=False)
)
# Untrusted: the labels are drawn, but nobody signed for them (a tampered or unsealed context).
UNTRUSTED_CHARTED_MONTHS = WITH_LAST_PLAN.with_visual_context(
    VisualContext(
        "chart-1",
        SPEC,
        BREAKDOWN_PROFILE,
        labels=_MONTH_LABELS,
        ranked=True,
        trusted=False,
        trust_reason="content",
    )
)
# A chart in context, but no plan behind it -- `analyze()`'s own `elif` never reaches the drill
# reader at all in this shape, same as every other follow-up shape in this module.
CHARTED_MONTHS_NO_PLAN = Conversation().with_visual_context(
    VisualContext("chart-1", SPEC, BREAKDOWN_PROFILE, labels=_MONTH_LABELS, ranked=True)
)


@pytest.mark.parametrize(
    ("sentence", "conversation"),
    [
        ("¿Por qué agosto es tan alto?", CHARTED_MONTHS),
        # A shape ("ese pico"), read on a ranked chart -- resolves Resolved, "rank".
        ("¿Qué movimientos explican ese pico?", CHARTED_MONTHS),
        # The same shape over a chart whose rank is not strict -- resolves Ambiguous, and an
        # ambiguous pointing still counts: the handler's answer to it is a question about the
        # chart, not a new request (§3.3, docstring of `_drills_into_the_chart`).
        ("¿Qué movimientos explican ese pico?", CHARTED_MONTHS_UNRANKED),
    ],
)
async def test_a_drill_down_naming_a_bar_of_the_last_chart_is_a_follow_up(
    sentence: str, conversation: Conversation
) -> None:
    analysis = await turn_analyzer.analyze(
        sentence,
        conversation=conversation,
        interpreter=turn_interpreters.Deterministic(),
        today=TODAY,
    )
    assert (analysis.relation, analysis.focus) == (TurnRelation.FOLLOW_UP, TurnFocus.LAST_ANSWER)


@pytest.mark.parametrize(
    ("sentence", "conversation"),
    [
        # No such bar was drawn: the chart is June-August, not September.
        ("¿Por qué septiembre es tan alto?", CHARTED_MONTHS),
        # A drill sentence naming no month and no shape ("tan caro" is not "más caro"/"el pico").
        ("¿Por qué es tan caro?", CHARTED_MONTHS),
        # A real bar is named, but over an untrusted chart -- whose labels nobody signed for.
        ("¿Por qué agosto es tan alto?", UNTRUSTED_CHARTED_MONTHS),
        # No chart in context at all.
        ("¿Por qué agosto es tan alto?", WITH_LAST_PLAN),
        # A chart in context, but no plan behind it.
        ("¿Por qué agosto es tan alto?", CHARTED_MONTHS_NO_PLAN),
    ],
)
async def test_a_drill_down_naming_nothing_the_chart_drew_is_a_new_request(
    sentence: str, conversation: Conversation
) -> None:
    analysis = await turn_analyzer.analyze(
        sentence,
        conversation=conversation,
        interpreter=turn_interpreters.Deterministic(),
        today=TODAY,
    )
    assert analysis.relation is TurnRelation.NEW_REQUEST


async def test_the_evidence_sentence_names_no_bar_and_stays_a_new_request() -> None:
    """R17: «Enséñame los movimientos responsables.» matches the drill lexicon (it names a
    movement set), but `resolve_reference` finds no month, category or shape it names in
    `CHARTED_MONTHS` -- so `_drills_into_the_chart` returns False and this analyser reads it
    exactly as today, NEW_REQUEST. It still reaches an answer: `_MOVEMENT_SET_REFERENCE`
    (`ask_financial_question.py`, Commit 3 of this task) recognises this exact sentence directly,
    without going through this analyser's chart relation at all -- the evidence path answers it,
    the relation never has to."""
    analysis = await turn_analyzer.analyze(
        "Enséñame los movimientos responsables.",
        conversation=CHARTED_MONTHS,
        interpreter=turn_interpreters.Deterministic(),
        today=TODAY,
    )
    assert analysis.relation is TurnRelation.NEW_REQUEST
