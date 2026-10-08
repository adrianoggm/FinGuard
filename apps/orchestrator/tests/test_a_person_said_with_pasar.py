"""Money passed to a person is money sent to that person, whatever verb carried it.

Validation fix 3, G1. On 2026-10-08 «¿cuánto le he pasado a aitor este mes?» (aitor-mandado-recibido
1) and «cuanto le he pasao a aitor este mes?» (impro-mandado-recibido 1) asked the period although
the sentence said «este mes», and called it «gastos en Aitor», as if Aitor were a shop. Everything
after it fell with it: with no person in the base, «y el a mi cuanto me ha mandado?» had nobody to
turn the money round on, and «no, digo a aitor» got «Sigo sin tener claro el periodo».

«Mandar», «enviar» and «pagar» in the past already read as money sent to the person the
directory knows (`_past_person_payment_plan`); «pasar» did not. It is read here the same way, in
the shapes that point it at somebody — «le he pasado», «le pasé», «he pasado a …» — and «pasao» is
its colloquial participle, in this verb only. What happened to somebody («¿qué le ha pasado a
Aitor?») and «el mes pasado» are not money.

Driven over HTTP on the episodes stack, whose directory has Aitor Beltran Diaz, with a planner
scripted as the real model behaved (`ReadsAitorAsAShop`): only the deterministic reading can pass.
"""

from collections.abc import Iterator
from typing import Any

import pytest
from support import episodes
from support.pending import SETTLED, AsksForTheWindow, ask, asking_for_the_window_stack

from finguard_orchestrator.bootstrap.dependencies import turn_interpreter
from finguard_orchestrator.bootstrap.main import app
from finguard_orchestrator.domain.enums import CounterpartyType, Direction, Period
from finguard_orchestrator.domain.models.conversation import Conversation, Slot
from finguard_orchestrator.domain.models.planning_context import PlanningContext
from finguard_orchestrator.domain.models.planning_result import PlanningResult
from finguard_orchestrator.domain.services import payment_router, spanish
from finguard_orchestrator.domain.services.intent_router import Intent
from finguard_orchestrator.domain.services.turn_interpreters import Deterministic
from finguard_orchestrator.domain.value_objects.financial_plan import (
    FinancialPlan,
    FinancialPlanDraft,
)
from finguard_orchestrator.infrastructure.planning.llm_query_planner import WINDOW_QUESTION


@pytest.fixture(autouse=True)
def _restore() -> Iterator[None]:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


class ReadsACorrection:
    """An interpreter that reads a turn opening with «no» as a correction, as a model may."""

    async def interpret(self, sentence: str, *, conversation: Conversation) -> Intent:
        if spanish.folded(sentence).startswith(("no ", "no,")):
            return Intent.CORRECTION
        return await Deterministic().interpret(sentence, conversation=conversation)


class ReadsAitorAsAShop(AsksForTheWindow):
    """What the local model did with «cuánto le he pasado a aitor este mes?» on 2026-10-08: Aitor
    a shop, and the window asked for although the sentence said it («¿De qué periodo quieres ver
    tus gastos en Aitor?»). Any other sentence is `AsksForTheWindow`'s."""

    async def plan(self, sentence: str, *, context: PlanningContext) -> PlanningResult:
        continuation = context.continuation
        if "aitor" not in spanish.folded(sentence) or (
            continuation is not None and continuation.force_follow_up
        ):
            return await super().plan(sentence, context=context)
        self.asked.append(sentence)
        return PlanningResult.needs_clarification(
            WINDOW_QUESTION,
            FinancialPlanDraft(direction=Direction.EXPENSE, merchants=("Aitor",)),
            Slot.WINDOW,
        )


def _after(*turns: str, interpreter: Any = None) -> tuple[episodes.Stack, dict[str, Any]]:
    """The last of `turns` answered after the others, with only its own reads and model calls
    left to read."""
    stack = asking_for_the_window_stack(planner=ReadsAitorAsAShop())
    if interpreter is not None:
        app.dependency_overrides[turn_interpreter] = lambda: interpreter
    conversation: dict[str, Any] | None = SETTLED
    for turn in turns[:-1]:
        conversation = ask(stack, turn, conversation)["conversation"]
    stack.ledger.plans.clear()
    stack.planner.asked.clear()
    return stack, ask(stack, turns[-1], conversation)


def _between_the_holder_and(
    stack: episodes.Stack, person: str, direction: Direction
) -> FinancialPlan:
    """The one plan the turn ran: `person`, as a person, this month, with no model asked."""
    (plan,) = stack.ledger.plans
    assert plan.direction is direction, plan
    assert plan.counterparties == (person,), plan
    assert plan.counterparty_types == (CounterpartyType.PERSON,), plan
    assert plan.merchants == (), "a person is never a shop"
    assert plan.period is not None and plan.period.period is Period.THIS_MONTH, plan
    assert stack.planner.asked == [], "no model reads the turn"
    return plan


@pytest.mark.parametrize(
    "sentence",
    [
        "cuanto le he pasado a aitor este mes?",
        "cuanto le he pasao a aitor este mes?",
        "¿Cuánto le pasé a Aitor este mes?",
        "cuanto he pasado a aitor este mes",
        "lo que le he pasado a Aitor este mes",
    ],
)
def test_money_passed_to_a_contact_is_money_sent_to_them(sentence: str) -> None:
    """aitor-mandado-recibido 1, impro-mandado-recibido 1: the person, and the window said."""
    stack, answer = _after(sentence)

    assert answer["outcome"] == "PLANNED", answer["text"]
    _between_the_holder_and(stack, "Aitor", Direction.EXPENSE)
    assert "gastos en Aitor" not in answer["text"]


def test_the_person_said_with_pasar_can_be_turned_round() -> None:
    """impro-mandado-recibido 1 and 3: what Aitor passed back, the same month."""
    stack, answer = _after("cuanto le he pasao a aitor este mes?", "y el a mi cuanto me ha pasado?")

    assert answer["outcome"] == "PLANNED", answer["text"]
    _between_the_holder_and(stack, "Aitor", Direction.INCOME)


@pytest.mark.parametrize("interpreter", [None, ReadsACorrection()], ids=["router", "correction"])
def test_the_real_conversation_corrects_and_turns_round(interpreter: Any) -> None:
    """aitor-mandado-recibido 1 to 3, as said: the correction names the same person, and the flip
    is what Aitor sent this month."""
    first = "cuanto le he pasado a aitor este mes?"
    stack, corrected = _after(first, "no, digo a aitor", interpreter=interpreter)

    assert corrected["outcome"] == "PLANNED", corrected["text"]
    _between_the_holder_and(stack, "Aitor", Direction.EXPENSE)
    assert "Sigo sin tener claro" not in corrected["text"]

    stack, turned = _after(
        first, "no, digo a aitor", "y el a mi cuanto me ha mandado?", interpreter=interpreter
    )
    assert turned["outcome"] == "PLANNED", turned["text"]
    _between_the_holder_and(stack, "Aitor", Direction.INCOME)


@pytest.mark.parametrize("interpreter", [None, ReadsACorrection()], ids=["router", "correction"])
@pytest.mark.parametrize("correction", ["no, digo a aitor", "no, quería decir a Aitor"])
def test_saying_whom_corrects_the_person_and_keeps_the_rest(
    correction: str, interpreter: Any
) -> None:
    """«No, digo a <nombre>» after an answer about somebody else: the same question, about the
    person now named, who has to be in the directory."""
    stack, answer = _after("cuanto le mande a Javi este mes", correction, interpreter=interpreter)

    assert answer["outcome"] == "PLANNED", answer["text"]
    _between_the_holder_and(stack, "Aitor", Direction.EXPENSE)


@pytest.mark.parametrize("interpreter", [None, ReadsACorrection()], ids=["router", "correction"])
def test_a_person_misread_as_a_shop_is_corrected_to_the_person(interpreter: Any) -> None:
    """impro-mandado-recibido 1 and 2 before this fix: the model read Aitor as a shop and «este
    mes» completed it («No he encontrado movimientos en Aitor»). «No, digo a aitor» is the person,
    sent this month, and never «buscar sin comercio»."""
    stack, answer = _after(
        "¿cuánto llevo en aitor este mes?", "este mes", "no, digo a aitor", interpreter=interpreter
    )

    assert answer["outcome"] == "PLANNED", answer["text"]
    _between_the_holder_and(stack, "Aitor", Direction.EXPENSE)
    assert "sin comercio" not in answer["text"]


def test_a_correction_to_nobody_the_directory_knows_is_not_a_person() -> None:
    """«Pepe» is no contact: the turn is read as it was before, never as a person called Pepe."""
    stack, _ = _after("cuanto le mande a Javi este mes", "no, digo a pepe")

    assert not any(plan.counterparties == ("pepe",) for plan in stack.ledger.plans)
    assert not any(plan.counterparties == ("Pepe",) for plan in stack.ledger.plans)


# --- what «pasar» does not mean -----------------------------------------------------------------


@pytest.mark.parametrize(
    "sentence",
    [
        "cuanto le he pasado a aitor este mes?",
        "cuanto le he pasao a aitor este mes?",
        "le pasé 20 a Aitor",
        "cuanto he pasado a aitor",
    ],
)
def test_passing_money_to_somebody_is_a_payment_that_happened(sentence: str) -> None:
    assert payment_router.describes_a_past_payment(sentence)


@pytest.mark.parametrize(
    "sentence",
    [
        "¿qué le ha pasado a Aitor?",
        "¿cuánto gasté el mes pasado?",
        "y el a mi cuanto me ha pasado?",
        "el mes pasao",
        "le he pasado el recibo de la luz",
        "pasale 20 a Aitor",
    ],
)
def test_what_happened_and_last_month_are_not_money_passed(sentence: str) -> None:
    """Something that happened to Aitor, a month, money that came the other way, a thing handed
    to nobody and an order to pay are not a payment the holder made."""
    assert not payment_router.describes_a_past_payment(sentence)


def test_what_happened_to_somebody_is_not_a_question_about_their_money() -> None:
    stack, _ = _after("¿qué le ha pasado a Aitor?")

    assert not any(plan.counterparties for plan in stack.ledger.plans)


@pytest.mark.parametrize(
    "sentence", ["cuanto le he pasado a aitor este mes?", "cuanto le he mandado a aitor este mes?"]
)
def test_money_passed_is_a_question_of_its_own_while_another_waits(sentence: str) -> None:
    """Final review C-1, for «pasar» as for «mandar»: asked while «¿De qué periodo?» waits on
    restaurants, it names the window that question wanted and is still about Aitor, not the
    restaurants this month."""
    stack, answer = _after("¿cuánto gasté en restaurantes?", sentence)

    assert answer["outcome"] == "PLANNED", answer["text"]
    _between_the_holder_and(stack, "Aitor", Direction.EXPENSE)
