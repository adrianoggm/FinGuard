"""The question turned round on the person it was about, in the shapes people actually used.

Validation fix 1, F4. After an answer about money sent to a person, the real conversations of
2026-10-08 turned it round in words Task 7's flip reading did not take, and each one asked the
period and named nobody:

- «y ella a mi cuanto me ha pasado?» (impro-mandado-recibido 3): the turn trails «cuánto me ha
  pasado» after the pronoun, and «pasar» was not a verb of sending;
- «no, lo que ella me ha pasado a mi» (mandado-recibido 6): a correction leads it, «pasar» again,
  and «a mí» trails it;
- «no, lo que me ha mandado carmen a mi» (impro-mandado-recibido 6): the person named after the
  verb, with «a mí» after the name — read as a person called «carmen a mi»;
- «y ella a mi?» (mandado-recibido 4) is Task 7's own shape; it failed on a base F1 had broken.

What must not change is pinned beside them: a name narrows to that person (R33), a pronoun or an
unsaid person means only the answer just given (R34), and after a payment it means nobody (R38).

Driven over HTTP on the episodes stack.
"""

from collections.abc import Iterator
from typing import Any

import pytest
from support import episodes
from support.pending import SETTLED, ask

from finguard_orchestrator.bootstrap.dependencies import turn_interpreter
from finguard_orchestrator.bootstrap.main import app
from finguard_orchestrator.domain.enums import Direction, Period
from finguard_orchestrator.domain.models.conversation import Conversation
from finguard_orchestrator.domain.services import spanish
from finguard_orchestrator.domain.services.intent_router import Intent
from finguard_orchestrator.domain.services.turn_interpreters import Deterministic
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan

MARTA_RUIZ_THIS_MONTH = "cuanto le e mandado a marta ruiz este mes?"


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


def _after(*turns: str, interpreter: Any = None) -> tuple[episodes.Stack, dict[str, Any]]:
    """The last of `turns` answered after the others, with only its own reads and model calls
    left to read."""
    stack = episodes.a_stack()
    if interpreter is not None:
        app.dependency_overrides[turn_interpreter] = lambda: interpreter
    conversation: dict[str, Any] | None = SETTLED
    for turn in turns[:-1]:
        conversation = ask(stack, turn, conversation)["conversation"]
    stack.ledger.plans.clear()
    stack.planner.asked.clear()
    return stack, ask(stack, turns[-1], conversation)


def _received_from(stack: episodes.Stack, person: str) -> FinancialPlan:
    (plan,) = stack.ledger.plans
    assert plan.direction is Direction.INCOME, plan
    assert plan.counterparties == (person,), plan
    assert stack.planner.asked == [], "no model reads the turn"
    return plan


@pytest.mark.parametrize(
    "sentence",
    [
        "y ella a mi cuanto me ha pasado?",
        "¿y ella a mí cuánto me ha mandado?",
        "y ella a mi cuanto me ha enviado",
        "no, lo que ella me ha pasado a mi",
        "no, lo que me ha pasado a mi",
        "y lo que me pasó",
        "y ella a mi?",
    ],
)
@pytest.mark.parametrize("interpreter", [None, ReadsACorrection()], ids=["router", "correction"])
def test_the_shapes_people_used_turn_the_answer_round(sentence: str, interpreter: Any) -> None:
    """The person and the window of the answer, the money the other way."""
    stack, answer = _after(MARTA_RUIZ_THIS_MONTH, sentence, interpreter=interpreter)

    assert answer["outcome"] == "PLANNED", answer["text"]
    plan = _received_from(stack, "Marta Ruiz")
    assert plan.period is not None and plan.period.period is Period.THIS_MONTH


@pytest.mark.parametrize(
    "sentence",
    ["no, lo que me ha mandado marta ruiz a mi", "no, lo que me ha pasado Marta Ruiz a mí"],
)
@pytest.mark.parametrize("interpreter", [None, ReadsACorrection()], ids=["router", "correction"])
def test_a_named_person_after_the_verb_is_still_that_person(
    sentence: str, interpreter: Any
) -> None:
    """impro-mandado-recibido 6: «carmen a mi» is Carmen, and «a mí» is who received it."""
    stack, answer = _after(MARTA_RUIZ_THIS_MONTH, sentence, interpreter=interpreter)

    assert answer["outcome"] == "PLANNED", answer["text"]
    _received_from(stack, "Marta Ruiz")


def test_the_real_conversation_turns_round_over_the_three_months() -> None:
    """mandado-recibido 2 to 4, as said: «Todas mis cuentas», «Ampliar a tres meses», «y ella a
    mi?» — what Marta Ruiz sent over those three months."""
    stack, answer = _after(
        MARTA_RUIZ_THIS_MONTH, "Todas mis cuentas", "Ampliar a tres meses", "y ella a mi?"
    )

    assert answer["outcome"] == "PLANNED", answer["text"]
    plan = _received_from(stack, "Marta Ruiz")
    assert plan.period is not None and plan.period.period is Period.LAST_N_MONTHS


# --- what must not change -----------------------------------------------------------------------


def test_a_name_that_is_not_the_answers_person_turns_nothing_round() -> None:
    """R33: Javi is never Marta, whatever the shape around him says."""
    stack, _ = _after(MARTA_RUIZ_THIS_MONTH, "no, lo que me ha pasado javi a mi")

    assert not any(
        plan.direction is Direction.INCOME and plan.counterparties == ("Marta Ruiz",)
        for plan in stack.ledger.plans
    )


@pytest.mark.parametrize(
    "sentence", ["y ella a mi cuanto me ha pasado?", "no, lo que me ha pasado a mi"]
)
def test_after_an_aside_the_pronoun_points_at_nobody(sentence: str) -> None:
    """R34: after «gracias» the answer is no longer the last thing said."""
    stack, _ = _after(MARTA_RUIZ_THIS_MONTH, "gracias", sentence)

    assert stack.planner.asked == [sentence]


@pytest.mark.parametrize(
    "sentence", ["y ella a mi cuanto me ha pasado?", "no, lo que ella me ha pasado a mi"]
)
def test_after_a_payment_the_pronoun_points_at_nobody(sentence: str) -> None:
    """R38: a Bizum to Aitor since the answer; «ella» is not the answer's person."""
    stack, _ = _after("cuanto le mande a Javi este mes", "mándale 20 euros a Aitor", "sí", sentence)

    assert stack.planner.asked == [sentence]
    assert not any(plan.direction is Direction.INCOME for plan in stack.ledger.plans)


def test_what_happened_to_somebody_else_is_no_turn_round() -> None:
    """«Pasar» is sending only in the whole-turn shapes: a sentence that says more is its own."""
    stack, _ = _after(MARTA_RUIZ_THIS_MONTH, "y ella a mi cuanto me ha pasado este año?")

    assert stack.planner.asked == ["y ella a mi cuanto me ha pasado este año?"]
