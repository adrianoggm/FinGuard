"""Which sentences are an instruction to pay somebody.

The first step of §4.1 — "Intent" — and it is deterministic on purpose. Asking the model "is this a
payment?" would put the decision to move money inside the thing that is not a source of truth, and
it would answer differently between two models we have measured 60 points apart on disposition.

What each mistake costs is not symmetric, which is what the rules are shaped around:

    a question read as a payment      the extraction finds no grounded amount and it is refused
    a payment read as a question      the capability guard refuses it as an action
    another action read as a payment  refused the same way as a question read as one

None of the three moves money. What would is a payment that skips the confirmation, and no routing
decision can cause that: the confirmation lives on the other side of a second request.
"""

import pytest

from finguard_orchestrator.domain.services import payment_router


@pytest.mark.parametrize(
    "sentence",
    [
        # The demo's sentence, and the shapes of it people actually type.
        "hazle un bizum de 20 euros a Marta",
        "haz un bizum de 20€ a Marta",
        "mándale 20 euros a Marta",
        "manda 20 euros a Marta",
        "envía 20 euros a Marta",
        "envíale 20 euros a Marta",
        "transfiere 20 euros a Marta",
        "hazle una transferencia de 20 euros a Marta",
        "págale 20 euros a Marta",
        "devuélvele los 20 euros a Marta",
        # No amount, but unmistakably a payment. It becomes a question about the amount, not a
        # refusal about capabilities — which is the answer somebody who said this wants.
        "hazle un bizum a Marta",
        "quiero hacerle un bizum a Marta",
    ],
)
def test_an_instruction_to_pay_is_recognised(sentence: str) -> None:
    assert payment_router.is_payment(sentence)


@pytest.mark.parametrize(
    "sentence",
    [
        # Questions, including the ones that name money and a person.
        "¿cuánto gasté en gasolina en junio?",
        "¿cuánto le he mandado a Marta este año?",
        "¿cuántos bizums le he hecho a Marta?",
        "¿me quedan 20 euros?",
        "dime mis gastos de 20 euros",
        "¿qué transferencias hice en julio?",
        # Actions that are not payments. Still unsupported, and refused by the capability guard
        # rather than sent down a path that ends in a payment prompt.
        "cancela mi suscripción a Netflix",
        "cámbiame el límite de la tarjeta",
        "dame de baja del gimnasio",
    ],
)
def test_everything_else_is_not_a_payment(sentence: str) -> None:
    assert not payment_router.is_payment(sentence)


def test_a_past_payment_is_a_question_not_an_order() -> None:
    """The tense is the whole difference, and getting it wrong is the one costly direction: an
    account holder asking what they sent last month must not be shown a prompt to send it again."""
    assert not payment_router.is_payment("le mandé 20 euros a Marta la semana pasada")
    assert not payment_router.is_payment("le he mandado 20 euros a Marta")
    assert not payment_router.is_payment("le envié 20 euros a Marta el martes")
