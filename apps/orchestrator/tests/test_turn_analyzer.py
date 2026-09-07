"""Intent, conversational relation and multiple objectives are separate facts."""

from datetime import date

import pytest

from finguard_orchestrator.domain.enums import Direction, Period
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
