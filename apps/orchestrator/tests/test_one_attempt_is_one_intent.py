"""A resent request is one payment, not two.

The idempotency key a caller sends is the one banking is given, so a retry replays the intent it
already made. What is left to pin here is what the user is told when banking refuses a key that was
already used for something else.
"""

from finguard_orchestrator.application.commands.pay import _refusal
from finguard_orchestrator.domain.enums import PaymentOutcome
from finguard_orchestrator.domain.services import payment_writer


def test_a_refused_request_does_not_claim_a_payment_already_happened() -> None:
    """Found by sending a reused key at the running stack.

    Every `PaymentRefusedError` this service does not have a name for fell through to "ese pago ya
    no está pendiente, comprueba tus movimientos" — a claim that a payment existed and was dealt
    with. Nothing had been sent, so it sent somebody looking through their movements for a payment
    that was never made: the alarming direction of wrong, about money.

    An intent that really is spent arrives as `IntentGoneError` and keeps that sentence.
    """
    answer = _refusal("HTTP 409: key_reused_for_another_payment")

    assert answer.outcome == PaymentOutcome.REFUSED.value
    assert answer.text == payment_writer.nothing_was_sent()
    assert "movimientos" not in answer.text


def test_a_balance_that_did_not_cover_it_still_says_so() -> None:
    """The one refusal with a cause worth naming: the user can act on it, and "no he enviado nada"
    alone would leave them guessing why."""
    assert _refusal("insufficient_funds").text == payment_writer.insufficient_funds()
