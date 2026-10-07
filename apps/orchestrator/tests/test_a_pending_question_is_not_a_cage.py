"""A question left open is one possibility for the next turn, never a cage.

Measured with the offline battery and live on 2026-10-07: «olvidalo» after an answer was read as
the name of a shop and ran the last question again filtered by it, and «olvídalo» answering
«¿De qué periodo?» was asked about again — «Sigo sin tener claro el periodo» — so the only way out
of an open question was to answer it. The arbitration now decides first: a turn that is only
reset words drops what was open and what an ellipsis could continue, and an order to move money
outranks the question it interrupts.

Driven over HTTP on the episodes stack, with the planner scripted to ask for the window the way the
real one does (`support.pending`).
"""

import json
from collections.abc import Iterator

import pytest
from support import episodes
from support.pending import SETTLED, ask, asking_for_the_window_stack

from finguard_orchestrator.bootstrap.main import app

RESET_TEXT = "Vale, lo dejamos. ¿Qué quieres consultar?"


@pytest.fixture(autouse=True)
def _restore() -> Iterator[None]:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def test_pending_interrupted_by_new_intent() -> None:
    stack = asking_for_the_window_stack()
    asked = ask(stack, "¿cuánto gasté en restaurantes?")
    assert asked["outcome"] == "NEEDS_CLARIFICATION"
    paid = ask(stack, "mándale 20 € a Marta Solis Vega", asked["conversation"])
    assert paid["outcome"] == "AWAITING_CONFIRMATION"
    assert paid["conversation"]["amount_minor"] == 2000
    assert paid["conversation"]["recipient"].startswith("Marta Solis")


def test_an_order_said_the_way_people_say_it_interrupts_the_question_too() -> None:
    """The spec's own sentence, the infinitive with a clitic that `payment_router` learned to read:
    it was the measured failure, «¿De qué periodo?» answered again instead of the preview."""
    stack = asking_for_the_window_stack()
    asked = ask(stack, "¿cuánto gasté en restaurantes?")
    paid = ask(stack, "ahora quiero mandarle 20 euros a Marta Solis Vega", asked["conversation"])
    assert paid["outcome"] == "AWAITING_CONFIRMATION", paid["text"]
    assert paid["conversation"]["amount_minor"] == 2000
    assert paid["conversation"]["workflow"] == "PAYMENT_CONFIRMATION", "the question is dropped"


def test_reset_pending() -> None:
    stack = asking_for_the_window_stack()
    asked = ask(stack, "¿cuánto gasté?")
    forgotten = ask(stack, "olvídalo", asked["conversation"])
    assert forgotten["conversation"].get("workflow") in (None, "IDLE")
    assert forgotten["conversation"].get("last_plan") is None
    balance = ask(stack, "¿cuánto tengo?", forgotten["conversation"])
    assert balance["outcome"] == "BALANCE"


def test_a_reset_says_so_and_keeps_what_was_chosen() -> None:
    """A short acknowledgement, no figure, and only the open question and the continuation base go:
    the account the holder chose is a choice, not a question, and survives."""
    stack = asking_for_the_window_stack()
    asked = ask(stack, "¿cuánto gasté en restaurantes?")
    assert asked["conversation"]["last_asked"] == "WINDOW"

    forgotten = ask(stack, "no importa", asked["conversation"])

    assert forgotten["outcome"] == "GUIDANCE"
    assert forgotten["text"] == RESET_TEXT
    assert forgotten["conversation"]["last_asked"] is None
    assert forgotten["conversation"]["account_selection_mode"] == "ALL"
    assert stack.ledger.ran == [], "nothing was read to say it"


def test_an_order_while_a_payment_waits_for_confirmation_goes_to_that_payment() -> None:
    """The preview owns the turn: its gates answer before the arbitration is asked.

    The brief asserted `len(stack.bank.prepared) == 1`, which the confirmation gate this task keeps
    exactly as it is does not do — an amount said to an open preview is a revision there, so the
    held intent is cancelled at the bank *before* the replacement preview is prepared. What must
    hold is the property behind it: the open payment's own gate answered, and there is never a
    second live payment beside it.
    """
    stack = episodes.a_stack()
    started = ask(stack, "hagamos un bizum a Marta Solis Vega")
    preview = ask(stack, "15 euros con concepto de comida", started["conversation"])
    assert preview["conversation"]["workflow"] == "PAYMENT_CONFIRMATION"

    answered = ask(stack, "mándale 20 € a Javi Nuñez", preview["conversation"])

    assert answered["text"].startswith("He descartado el Bizum anterior."), "the preview's gate"
    assert stack.bank.cancelled == [episodes.INTENT], "the held intent went first"
    assert len(stack.bank.prepared) - len(stack.bank.cancelled) == 1, "one live payment, never two"


def test_an_order_to_somebody_else_never_revises_the_old_recipients_preview() -> None:
    """Ruling R14. With Marta's 15 € preview open, «mándale 20 € a Javi Nuñez» was read as a
    revision of the amount alone: the replacement said «Vas a enviar 20,00 € a Marta Solis Vega»,
    and the next «sí» paid Marta for an order that named Javi. The order names a different person,
    so it takes the change-of-recipient path: Marta's intent is cancelled at the bank and the new
    preview is Javi's."""
    stack = episodes.a_stack()
    started = ask(stack, "hagamos un bizum a Marta Solis Vega")
    preview = ask(stack, "15 euros con concepto de comida", started["conversation"])

    answered = ask(stack, "mándale 20 € a Javi Nuñez", preview["conversation"])

    assert stack.bank.cancelled == [episodes.INTENT], "Marta's preview is cancelled"
    assert not [
        request
        for request in stack.bank.prepared
        if request.recipient_name.startswith("Marta") and request.amount_minor == 2_000
    ], "no payment to Marta for the 20 € Javi was named for"
    assert answered["outcome"] == "AWAITING_CONFIRMATION", answered["text"]
    assert answered["conversation"]["recipient"] == "Javi Nuñez"
    assert answered["conversation"]["amount_minor"] == 2_000
    assert "Javi Nuñez" in answered["text"] and "Marta" not in answered["text"]


def test_an_order_to_the_same_person_revises_the_preview() -> None:
    """The other side of R14: naming the preview's own recipient, in part, is a revision — the
    amount changes and the person does not, and «Marta» is not asked about again."""
    stack = episodes.a_stack()
    started = ask(stack, "hagamos un bizum a Marta Solis Vega")
    preview = ask(stack, "15 euros con concepto de comida", started["conversation"])

    answered = ask(stack, "mándale 20 € a Marta", preview["conversation"])

    assert answered["text"].startswith("He descartado el Bizum anterior."), answered["text"]
    assert answered["conversation"]["recipient"] == "Marta Solis Vega"
    assert answered["conversation"]["amount_minor"] == 2_000


def test_olvidalo_after_an_answer_is_not_a_shop() -> None:
    stack = episodes.a_stack()
    first = ask(stack, "cuanto gaste en el super el mes pasado", SETTLED)
    forgotten = ask(stack, "olvidalo", first["conversation"])
    assert forgotten["outcome"] != "PLANNED" and len(stack.ledger.ran) == 1  # no second query


def test_olvidalo_after_an_answer_drops_the_base_an_ellipsis_would_continue() -> None:
    """«Olvídalo» is about the last answer too, so the base an ellipsis («¿y en gasolina?») would
    continue goes with it — and the account the holder chose stays, because it was a choice."""
    stack = episodes.a_stack()
    first = ask(stack, "cuanto gaste en el super el mes pasado", SETTLED)
    assert first["conversation"]["last_plan"] is not None

    forgotten = ask(stack, "olvidalo", first["conversation"])

    assert forgotten["text"] == RESET_TEXT
    assert forgotten["conversation"]["last_plan"] is None
    assert forgotten["conversation"]["account_selection_mode"] == "ALL"


def test_a_reset_reaches_the_stream_too() -> None:
    """The browser types into the streaming route, so a rule only the other route had would be a
    rule the conversation never got."""
    stack = asking_for_the_window_stack()
    asked = ask(stack, "¿cuánto gasté?")
    response = stack.client.post(
        "/v1/questions/stream",
        json={"question": "déjalo", "conversation": asked["conversation"]},
    )
    assert response.status_code == 200, response.text
    frames = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    answer = frames[-1]
    assert answer["type"] == "answer"
    assert answer["text"] == RESET_TEXT
    assert answer["conversation"]["workflow"] is None
    assert answer["conversation"]["last_asked"] is None
