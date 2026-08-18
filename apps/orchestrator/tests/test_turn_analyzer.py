"""Intent, conversational relation and multiple objectives are separate facts."""

from datetime import date

from finguard_orchestrator.domain.enums import Direction, Period
from finguard_orchestrator.domain.models.conversation import Conversation
from finguard_orchestrator.domain.models.turn_analysis import TurnFocus, TurnRelation
from finguard_orchestrator.domain.services import turn_analyzer, turn_interpreters
from finguard_orchestrator.domain.services.intent_router import Intent
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan, RelativePeriod

TODAY = date(2026, 8, 10)


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
