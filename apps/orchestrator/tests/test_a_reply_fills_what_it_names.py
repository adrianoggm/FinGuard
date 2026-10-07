"""A reply to the question left open fills every slot it names, whichever one was asked.

Measured live on 2026-10-07: «en todas mis cuentas» given to «¿De qué periodo?» was answered with
the balance, and «este año» given to «¿En qué cuenta?» was asked about again with the period lost;
after an answered question, «este año» to a new question's «¿De qué periodo?» completed the
*previous* plan. The arbitration now reads what the reply fills before any reader of the query in
view sees it, and the open question absorbs all of it — the account and the window, in either
order — asking only for what is still missing (spec §3.1, §3.2).

Driven over HTTP on the episodes stack, with the planner scripted to ask for the window the way the
real one does (`support.pending`).
"""

from collections.abc import Iterator

import pytest
from support import episodes
from support.pending import SETTLED, account_question_stack, ask, asking_for_the_window_stack

from finguard_orchestrator.bootstrap.main import app
from finguard_orchestrator.domain.enums import Period


@pytest.fixture(autouse=True)
def _restore() -> Iterator[None]:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def test_the_account_given_to_a_period_question_is_kept_and_the_period_asked_again() -> None:
    stack = asking_for_the_window_stack(settled=False)
    asked = ask(stack, "cuánto he gastado en el gimnasio últimamente")  # «¿De qué periodo?»
    told = ask(stack, "en todas mis cuentas", asked["conversation"])
    assert told["outcome"] == "NEEDS_CLARIFICATION" and told["outcome"] != "BALANCE"
    assert told["conversation"]["account_selection_mode"] == "ALL"
    answered = ask(stack, "este año", told["conversation"])
    assert answered["outcome"] == "PLANNED" and stack.ledger.plans[-1].across_accounts


def test_the_period_given_to_an_account_question_is_kept() -> None:
    stack = account_question_stack()  # «¿En qué cuenta?»
    asked = ask(stack, "¿Cuánto llevo gastado en Spotify?")
    told = ask(stack, "este año", asked["conversation"])
    assert told["question"] == "¿En qué cuenta quieres consultarlo?"
    ask(stack, "Todas mis cuentas", told["conversation"])
    assert stack.ledger.plans[-1].period.period is Period.THIS_YEAR  # not today..today


def test_a_reply_to_a_new_question_does_not_complete_the_previous_answer() -> None:
    stack = asking_for_the_window_stack()
    first = ask(stack, "cuanto gaste en el super el mes pasado", SETTLED)
    asked = ask(stack, "¿Cuánto gasté en el gimnasio?", first["conversation"])  # a window question
    assert asked["outcome"] == "NEEDS_CLARIFICATION"
    ask(stack, "este año", asked["conversation"])
    assert stack.ledger.plans[-1].categories != ("GROCERIES",)


def test_a_reply_that_fills_the_slot_and_names_a_subject_keeps_both() -> None:
    stack = asking_for_the_window_stack()
    asked = ask(stack, "¿cuánto gasté en restaurantes?")
    ask(stack, "este año pero en gasolina", asked["conversation"])
    plan = stack.ledger.plans[-1]
    assert plan.period.period is Period.THIS_YEAR and plan.categories == ("TRANSPORT_FUEL",)


# --- beyond the brief's four: the other spellings of the same property --------------------------


def test_a_kind_of_account_given_to_a_period_question_is_kept_for_the_answer() -> None:
    """«En la hucha» chooses as surely as «en todas mis cuentas»: kept in the draft and as the
    conversation's choice, so the reply that brings the window runs the question there."""
    stack = asking_for_the_window_stack(settled=False)
    asked = ask(stack, "¿cuánto gasté en restaurantes?")
    told = ask(stack, "en la hucha", asked["conversation"])
    assert told["outcome"] == "NEEDS_CLARIFICATION", told["text"]
    assert told["conversation"]["workflow"] == "FINANCIAL_CLARIFICATION"
    assert told["conversation"]["account_selection_mode"] == "SELECTED"
    assert told["conversation"]["draft"]["only_account_id"] == str(episodes.PIGGY)
    answered = ask(stack, "este año", told["conversation"])
    assert answered["outcome"] == "PLANNED", answered["text"]
    plan = stack.ledger.plans[-1]
    assert plan.only_account_id == episodes.PIGGY and plan.categories == ("RESTAURANTS",)
    assert plan.period.period is Period.THIS_YEAR


def test_the_window_and_the_account_in_one_reply_answer_the_account_question() -> None:
    stack = account_question_stack()
    asked = ask(stack, "¿Cuánto llevo gastado en Spotify?")
    answered = ask(stack, "la hucha este año", asked["conversation"])
    assert answered["outcome"] == "PLANNED", answered["text"]
    plan = stack.ledger.plans[-1]
    assert plan.only_account_id == episodes.PIGGY and plan.period.period is Period.THIS_YEAR


def test_the_kept_window_travels_with_the_account_question() -> None:
    """Kept in the plan that waits, on the wire, and asked about with the question's own chips —
    so the next reply is a chip, and the chip is all it needs to be."""
    stack = account_question_stack()
    asked = ask(stack, "¿Cuánto llevo gastado en Spotify?")
    told = ask(stack, "este año", asked["conversation"])
    assert told["conversation"]["workflow"] == "ACCOUNT_SCOPE"
    assert told["conversation"]["pending_plan"]["period"] == "THIS_YEAR"
    assert told["suggestions"] == [
        "Cuenta corriente",
        "Hucha",
        "Fondo indexado",
        "Todas mis cuentas",
    ]
    assert len(stack.ledger.plans) == 0, "nothing ran before the account was chosen"


def test_a_question_of_its_own_is_not_the_period_the_open_one_was_missing() -> None:
    """A whole question names a window too. It is answered as itself, not as the restaurants
    question's missing period (the fourth condition `_finish_pending` already kept)."""
    stack = asking_for_the_window_stack()
    asked = ask(stack, "¿cuánto gasté en restaurantes?")
    ask(stack, "¿cuánto gasté en gasolina el mes pasado?", asked["conversation"])
    plan = stack.ledger.plans[-1]
    assert plan.categories == ("TRANSPORT_FUEL",) and plan.period.period is Period.LAST_MONTH
