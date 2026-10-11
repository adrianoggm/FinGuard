"""A screen's typed action as the application's form action: fields only, nothing decided here."""

from finguard_orchestrator.application.engine.form_actions import (
    AnswerPreview,
    FormAction,
    MoveBetweenAccounts,
    PayContact,
)
from finguard_orchestrator.infrastructure.dto.action_dto import (
    AnswerPreviewAction,
    MoveBetweenAccountsAction,
    PayContactAction,
)


def to_application(
    body: PayContactAction | MoveBetweenAccountsAction | AnswerPreviewAction,
) -> FormAction:
    match body:
        case PayContactAction():
            return PayContact(body.recipient_name, body.amount_minor, body.rail)
        case MoveBetweenAccountsAction():
            return MoveBetweenAccounts(body.to_account_id, body.amount_minor)
        case AnswerPreviewAction():
            return AnswerPreview(confirm=body.decision == "confirm")
