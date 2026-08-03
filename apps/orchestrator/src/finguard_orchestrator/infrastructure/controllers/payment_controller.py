"""HTTP surface of the payment conversation. Two turns, two routes, two scopes.

    POST /v1/payments                     read the sentence, answer with a preview. Moves nothing.
    POST /v1/payments/{intent_id}/confirm  the user's reply. Pays only for an unambiguous yes.

The scopes are separate and that is the §4.1 requirement in one line: `payments:prepare` drafts the
operation and `payments:confirm` completes it. A credential that could do both makes the human
confirmation step a formality — the same code path would draft and approve.

The account is never in the body. It comes from the credential, so there is nowhere to say whose
money you mean, exactly like the questions route.

Both routes answer 200 with an outcome. A bank that refused, a name that matched two people, a reply
that was neither yes nor no: those are turns in a conversation, not HTTP failures, and a client that
had to read status codes to continue talking would be a client that stops talking.
"""

import uuid
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from finguard_orchestrator.application import CommandBus
from finguard_orchestrator.application.commands.pay import (
    ConfirmPaymentCommand,
    PrepareChosenPaymentCommand,
    PreparePaymentCommand,
)
from finguard_orchestrator.bootstrap.dependencies import get_command_bus, payments_enabled, requires
from finguard_orchestrator.domain.value_objects.caller import Caller
from finguard_orchestrator.domain.value_objects.scope import Scope
from finguard_orchestrator.infrastructure.dto.payment_dto import (
    ChosenPaymentRequest,
    ConfirmPaymentRequest,
    PaymentRequestBody,
    PaymentResponse,
)
from finguard_orchestrator.infrastructure.mappers import payment_mapper

Commands = Annotated[CommandBus, Depends(get_command_bus)]
MayPrepare = Annotated[Caller, Depends(requires(Scope.PAYMENTS_PREPARE))]
MayConfirm = Annotated[Caller, Depends(requires(Scope.PAYMENTS_CONFIRM))]

payments_router = APIRouter(
    prefix="/v1/payments", tags=["payments"], dependencies=[Depends(payments_enabled)]
)


@payments_router.post("", summary="Read a payment from a sentence and preview it")
async def prepare(
    request: PaymentRequestBody, who: MayPrepare, commands: Commands
) -> PaymentResponse:
    """Answers with what the user is being asked to approve, and moves nothing.

    The idempotency key is generated here rather than required from the caller, unlike banking's own
    endpoint. Preparing is free and expires on its own, so a duplicate intent costs nothing and no
    chat client has to manage keys; what must never happen twice is the confirmation, and that is
    keyed on the intent itself.
    """
    answer = await commands.dispatch(
        PreparePaymentCommand(
            sentence=request.sentence,
            account_id=who.account_id,
            idempotency_key=str(uuid.uuid4()),
        )
    )
    return payment_mapper.answer_to_dto(answer)


@payments_router.post("/chosen", summary="Prepare a payment built in a form")
async def prepare_chosen(
    request: ChosenPaymentRequest, who: MayPrepare, commands: Commands
) -> PaymentResponse:
    """The form path: a contact picked from the list, an amount typed in a field.

    No model and no grounding check, because there is nothing to read — grounding catches a figure a
    model invented, and here the figure was typed and the recipient was chosen from a list this
    account was allowed to see. What does not change is the rest of §4.1: the answer is a preview,
    and it is confirmed through the same route as any other.

    Declared before `/{intent_id}/confirm` would not matter — "chosen" is not a UUID, so the two
    cannot collide — but it reads in the order the flow happens.
    """
    answer = await commands.dispatch(
        PrepareChosenPaymentCommand(
            account_id=who.account_id,
            recipient_name=request.recipient_name,
            amount_minor=request.amount_minor,
            currency=request.currency,
            idempotency_key=str(uuid.uuid4()),
        )
    )
    return payment_mapper.answer_to_dto(answer)


@payments_router.post("/{intent_id}/confirm", summary="Answer a preview")
async def confirm(
    intent_id: UUID, request: ConfirmPaymentRequest, who: MayConfirm, commands: Commands
) -> PaymentResponse:
    """The reply is the user's own words and is read deterministically — a model never decides that
    somebody agreed.

    A reply that is neither yes nor no comes back as AWAITING_CONFIRMATION with the question again.
    That is the same outcome the preview had, on purpose: the state of the world did not change.
    """
    answer = await commands.dispatch(
        ConfirmPaymentCommand(
            reply=request.reply,
            account_id=who.account_id,
            intent_id=intent_id,
            preview=payment_mapper.preview_to_domain(request.preview) if request.preview else None,
        )
    )
    return payment_mapper.answer_to_dto(answer)
