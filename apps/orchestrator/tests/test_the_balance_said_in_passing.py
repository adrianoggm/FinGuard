"""«¿Qué tengo en total entre todo?» is the balance, and a new question for it is not a reply.

Validation fix 3, G2. On 2026-10-08 «bueno da igual. q tengo en total entre todo?» was said while
«¿De qué periodo…?» waited (aitor-mandado-recibido 4) and was absorbed as its answer: a Bizum to
"Aitor" this month. Sent again with nothing open (aitor-mandado-recibido 5), it reached the model
and came back −1.030,42 € «en lo que va de mes», the net of seven movements, unexplained. Neither
is how much money the holder has.

«(Qué|q) tengo en total», «entre todo» and «en total entre todas» are the balance across every
account. A balance is a NEW_OPERATION, which outranks the open question and drops it (R12). The
leading «bueno da igual.» is courtesy: content follows, so the turn is no reset.

Driven over HTTP on the episodes stack, with the least helpful model a reply can meet
(`KeepsTheBase`): told to patch the draft, it changes nothing.
"""

from collections.abc import Iterator

import pytest
from support import episodes
from support.pending import SETTLED, KeepsTheBase, ask, asking_for_the_window_stack

from finguard_orchestrator.bootstrap.main import app
from finguard_orchestrator.domain.services import intent_router, turn_arbiter
from finguard_orchestrator.domain.services.intent_router import Intent

THE_SENTENCE = "bueno da igual. q tengo en total entre todo?"


@pytest.fixture(autouse=True)
def _restore() -> Iterator[None]:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def _every_account() -> int:
    """The figure «¿cuánto tengo?» leads with on the episodes stack: every account, «entre todo»."""
    figure = ask(episodes.a_stack(), "¿cuánto tengo?")["total_minor"]
    assert isinstance(figure, int)
    return figure


@pytest.mark.parametrize(
    "sentence",
    [
        THE_SENTENCE,
        "q tengo en total entre todo?",
        "¿Qué tengo en total?",
        "que tengo entre todo",
        "¿y qué tengo en total entre todas?",
        "¿qué tengo en total entre todas mis cuentas?",
    ],
)
def test_said_while_the_period_waits_it_is_the_balance_and_the_question_goes(
    sentence: str,
) -> None:
    """aitor-mandado-recibido 4: the balance, never a reply that completes the open draft, and the
    window question dropped behind it (R12)."""
    stack = asking_for_the_window_stack(planner=KeepsTheBase())
    asked = ask(stack, "¿cuánto gasté en restaurantes?")
    assert asked["conversation"]["awaiting"] == "WINDOW"
    stack.planner.asked.clear()

    answered = ask(stack, sentence, asked["conversation"])

    assert episodes.tool_of(answered) == episodes.BALANCE, answered["text"]
    assert answered["total_minor"] == _every_account()
    assert answered["conversation"]["workflow"] is None
    assert answered["conversation"]["awaiting"] is None
    assert answered["conversation"]["last_asked"] is None
    assert stack.ledger.ran == [], "no movement was summed for it"
    assert stack.planner.asked == [], "no model read it"


def test_said_again_with_nothing_open_it_is_still_the_balance() -> None:
    """aitor-mandado-recibido 5: the same sentence after an answer, never the net of the month."""
    stack = episodes.a_stack()
    answered = ask(stack, "¿cuánto gasté en restaurantes este mes?", SETTLED)
    stack.ledger.plans.clear()

    again = ask(stack, THE_SENTENCE, answered["conversation"])

    assert episodes.tool_of(again) == episodes.BALANCE, again["text"]
    assert again["total_minor"] == _every_account()
    assert stack.ledger.plans == []


def test_the_courtesy_in_front_is_no_reset() -> None:
    """«Bueno da igual.» wraps content, so the whole-turn reset rule leaves it alone."""
    assert not turn_arbiter.is_a_reset(THE_SENTENCE)


@pytest.mark.parametrize(
    "sentence",
    [
        "¿cuánto gasté en total entre todo?",
        "¿qué tengo que pagar este mes?",
        "¿qué gastos tengo en total este mes?",
        # Fix round 1, I-2: «en total» followed by a subject is that subject's total, not money
        # held. Each of these became the balance when the phrasing was not anchored at the end.
        "q tengo en total de nómina este año",
        "¿qué tengo en total en suscripciones?",
        "qué tengo en total pendiente de pagar",
        "que tengo en total de deuda",
        "¿qué tengo en total en recibos este mes?",
        "que tengo en total entre todos los bizums",
    ],
)
def test_spending_and_what_is_owed_are_not_the_balance(sentence: str) -> None:
    """Money that went out, and a question about what is due, are not money held."""
    assert intent_router.route(sentence, has_previous=False) is not Intent.ACCOUNT_BALANCE


@pytest.mark.parametrize(
    "sentence",
    [
        "¿qué tengo en total en recibos este mes?",
        "¿qué tengo en total de gastos este mes?",
        "qué tengo en total este mes",
    ],
)
def test_what_there_is_in_total_with_more_said_is_a_question_of_its_own(sentence: str) -> None:
    """Fix round 2, item 4: after restaurants this month, a question that opens «qué tengo en total»
    and says more is a question of its own — never «este mes» patched onto the restaurants."""
    stack = episodes.a_stack()
    answered = ask(stack, "¿cuánto gasté en restaurantes este mes?", SETTLED)
    stack.ledger.plans.clear()
    stack.planner.asked.clear()

    ask(stack, sentence, answered["conversation"])

    assert stack.planner.asked == [sentence]
    assert not any(plan.categories == ("RESTAURANTS",) for plan in stack.ledger.plans)


@pytest.mark.parametrize(
    "sentence",
    [
        "¿qué tengo en total en recibos este mes?",
        "q tengo en total de gastos este mes",
        "qué tengo en total",
    ],
)
def test_what_there_is_in_total_is_a_whole_question(sentence: str) -> None:
    """A question of its own for the arbitration (never a reply to another question's period) and
    for the readers (never a patch on the answer in view, nor a base handed to the planner)."""
    assert turn_arbiter.asks_its_own(sentence)
    assert turn_arbiter.states_a_question_of_its_own(sentence, today=episodes.TODAY)
