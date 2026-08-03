"""PaymentAnswer ↔ DTO. The only place that knows the payment endpoints' wire shape.

`reason` deliberately does not cross: it names the guard rule that refused a reading or the code the
bank answered with, which belongs in the log and in the evaluation. On the wire it would be an
invitation to branch on our internals, and a client that branched on AMOUNT_INVENTED would break the
day the rule is renamed.
"""

from finguard_orchestrator.application.commands.pay import PaymentAnswer
from finguard_orchestrator.domain.models.payment_result import PaymentPreview
from finguard_orchestrator.infrastructure.dto.payment_dto import PaymentResponse, PreviewPayload

# Not a real intent: the preview echoed back by a client is only used to repeat a question, so the
# identity of the intent comes from the path and never from the body.
_ECHOED = "00000000-0000-0000-0000-000000000000"


def answer_to_dto(answer: PaymentAnswer) -> PaymentResponse:
    return PaymentResponse(
        outcome=answer.outcome,
        text=answer.text,
        intent_id=str(answer.intent_id) if answer.intent_id else None,
        recipient=answer.recipient,
        recipient_phone=answer.recipient_phone,
        amount_minor=answer.amount_minor,
        currency=answer.currency,
        balance_after_minor=answer.balance_after_minor,
        expires_at=answer.expires_at,
        transaction_id=str(answer.transaction_id) if answer.transaction_id else None,
    )


def preview_to_domain(payload: PreviewPayload) -> PaymentPreview:
    """The echoed preview, for repeating the question. Nothing here decides a payment.

    The fields it does not carry are filled with values that cannot be mistaken for real ones: the
    intent comes from the path, and `balance_before` and `expires_at` are not part of the sentence
    this rebuilds. A client cannot restate the amount that gets paid — that lives in banking's own
    table — so what this can affect at worst is a sentence the user reads before answering again.
    """
    from datetime import UTC, datetime
    from uuid import UUID

    return PaymentPreview(
        intent_id=UUID(_ECHOED),
        recipient=payload.recipient,
        recipient_phone=payload.recipient_phone,
        amount_minor=payload.amount_minor,
        currency=payload.currency,
        balance_before_minor=payload.balance_after_minor + payload.amount_minor,
        balance_after_minor=payload.balance_after_minor,
        expires_at=datetime.min.replace(tzinfo=UTC),
    )
