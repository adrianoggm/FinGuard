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

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends

from finguard_orchestrator.application import CommandBus
from finguard_orchestrator.application.commands.pay import (
    ConfirmPaymentCommand,
    PaymentAnswer,
    PrepareChosenPaymentCommand,
    PrepareOwnTransferCommand,
    PreparePaymentCommand,
)
from finguard_orchestrator.bootstrap.dependencies import (
    attempt_key,
    get_command_bus,
    get_payment_workflow,
    payments_enabled,
    requires,
)
from finguard_orchestrator.domain.enums import PaymentOutcome
from finguard_orchestrator.domain.services.payment_writer import no_longer_live
from finguard_orchestrator.domain.value_objects.caller import Caller
from finguard_orchestrator.domain.value_objects.scope import Scope
from finguard_orchestrator.infrastructure.dto.payment_dto import (
    ChosenPaymentRequest,
    ConfirmPaymentRequest,
    OwnTransferRequest,
    PaymentRequestBody,
    PaymentResponse,
)
from finguard_orchestrator.infrastructure.mappers import payment_mapper

Commands = Annotated[CommandBus, Depends(get_command_bus)]
# Keep the graph optional at the HTTP boundary without importing LangGraph on deployments
# where the feature is off. The dependency returns the concrete adapter only when wired.
Workflow = Annotated[Any, Depends(get_payment_workflow)]
MayPrepare = Annotated[Caller, Depends(requires(Scope.PAYMENTS_PREPARE))]
MayConfirm = Annotated[Caller, Depends(requires(Scope.PAYMENTS_CONFIRM))]

# The caller's name for this attempt, defaulted to a fresh one. See `attempt_key`.
Attempt = Annotated[str, Depends(attempt_key)]

payments_router = APIRouter(
    prefix="/v1/payments", tags=["payments"], dependencies=[Depends(payments_enabled)]
)


@payments_router.post("", summary="Read a payment from a sentence and preview it")
async def prepare(
    request: PaymentRequestBody,
    who: MayPrepare,
    commands: Commands,
    attempt: Attempt,
    workflow: Workflow,
) -> PaymentResponse:
    """Answers with what the user is being asked to approve, and moves nothing.

    Send `Idempotency-Key` and a resend of this attempt is the same preview rather than a second
    one. It is the header banking requires, and it is optional here only so a client written before
    it keeps working — `attempt_key` says what that costs.
    """
    command = PreparePaymentCommand(
        sentence=request.sentence,
        account_id=who.account_id,
        idempotency_key=attempt,
    )
    answer = (
        await workflow.prepare(command, account_id=who.account_id)
        if workflow is not None
        else await commands.dispatch(command)
    )
    return payment_mapper.answer_to_dto(answer)


@payments_router.post("/chosen", summary="Prepare a payment built in a form")
async def prepare_chosen(
    request: ChosenPaymentRequest,
    who: MayPrepare,
    commands: Commands,
    attempt: Attempt,
    workflow: Workflow,
) -> PaymentResponse:
    """The form path: a contact picked from the list, an amount typed in a field.

    No model and no grounding check, because there is nothing to read — grounding catches a figure a
    model invented, and here the figure was typed and the recipient was chosen from a list this
    account was allowed to see. What does not change is the rest of §4.1: the answer is a preview,
    and it is confirmed through the same route as any other.

    Declared before `/{intent_id}/confirm` would not matter — "chosen" is not a UUID, so the two
    cannot collide — but it reads in the order the flow happens.
    """
    command = PrepareChosenPaymentCommand(
        account_id=who.account_id,
        recipient_name=request.recipient_name,
        amount_minor=request.amount_minor,
        currency=request.currency,
        idempotency_key=attempt,
    )
    answer = (
        await workflow.prepare(command, account_id=who.account_id)
        if workflow is not None
        else await commands.dispatch(command)
    )
    return payment_mapper.answer_to_dto(answer)


@payments_router.post("/between-accounts", summary="Move money between your own accounts")
async def prepare_own_transfer(
    request: OwnTransferRequest,
    who: MayPrepare,
    commands: Commands,
    attempt: Attempt,
    workflow: Workflow,
) -> PaymentResponse:
    """A preview, like every other payment, and confirmed through the same route.

    Nothing is read and no model is involved: an account was picked from a list this holder was
    shown and an amount was typed into a field.

    It keeps the preview and the explicit confirmation even though the money never leaves the
    household, because those protect against a mistake rather than against a misreading — and 200 €
    in the wrong direction between two of your own accounts is a mistake worth being asked about.

    Declared before `/{intent_id}/confirm` for readability only: "between-accounts" is not a UUID,
    so the two cannot collide.
    """
    command = PrepareOwnTransferCommand(
        account_id=who.account_id,
        to_account_id=request.to_account_id,
        amount_minor=request.amount_minor,
        currency=request.currency,
        idempotency_key=attempt,
    )
    answer = (
        await workflow.prepare(command, account_id=who.account_id)
        if workflow is not None
        else await commands.dispatch(command)
    )
    return payment_mapper.answer_to_dto(answer)


@payments_router.post("/{intent_id}/confirm", summary="Answer a preview")
async def confirm(
    intent_id: UUID,
    request: ConfirmPaymentRequest,
    who: MayConfirm,
    commands: Commands,
    workflow: Workflow,
) -> PaymentResponse:
    """The reply is the user's own words and is read deterministically — a model never decides that
    somebody agreed.

    A reply that is neither yes nor no comes back as AWAITING_CONFIRMATION with the question again.
    That is the same outcome the preview had, on purpose: the state of the world did not change.
    """
    if workflow is not None:
        if request.workflow_id is None:
            answer = PaymentAnswer(
                outcome=PaymentOutcome.REFUSED.value,
                text=no_longer_live(),
                intent_id=intent_id,
            )
        else:
            answer = await workflow.confirm(
                workflow_id=request.workflow_id,
                account_id=who.account_id,
                intent_id=intent_id,
                reply=request.reply,
            )
    else:
        answer = await commands.dispatch(
            ConfirmPaymentCommand(
                reply=request.reply,
                account_id=who.account_id,
                intent_id=intent_id,
                preview=(
                    payment_mapper.preview_to_domain(request.preview) if request.preview else None
                ),
            )
        )
    return payment_mapper.answer_to_dto(answer)
