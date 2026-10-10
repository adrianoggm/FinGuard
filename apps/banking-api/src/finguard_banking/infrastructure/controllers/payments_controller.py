"""HTTP surface of the payments (§4.1). Two steps, and only the second one moves money.

Preparing is a read of the consequences: it writes an intent and answers with what the user is
being asked to approve. Confirming executes it. They are separate requests rather than one with a
`confirm: true` flag, because a flag can be set by the same code that filled in the amount — and
the whole point of §4.1 is that a person, not a program, closes that gap.

Where each piece of a payment comes from is deliberate:

    the account          the path, like the balance endpoint's
    the recipient        a name in the body, resolved against that account's contacts
    the idempotency key  an `Idempotency-Key` header, required

None of the three can be forged from the body. A body that could name its own account would let a
caller pay out of somebody else's, and a contact identifier would let them pay somebody who is not
in their list.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Response

from finguard_banking.application import CancelPaymentCommand, CommandBus, ConfirmPaymentCommand
from finguard_banking.bootstrap.dependencies import get_command_bus
from finguard_banking.infrastructure.dto.banking_dto import (
    PaymentPreviewResponse,
    PaymentReceiptResponse,
    PreparePaymentRequest,
)
from finguard_banking.infrastructure.mappers import http_mapper

Commands = Annotated[CommandBus, Depends(get_command_bus)]

# Required, and never defaulted. A key this service invents per request differs between a request
# and its retry, which is the same as having no key at all: the retry becomes a second payment.
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=200)]

payments_router = APIRouter(prefix="/v1/accounts/{account_id}/payments", tags=["payments"])


@payments_router.post("", summary="Prepare a payment (moves nothing)")
async def prepare_payment(
    account_id: UUID,
    body: PreparePaymentRequest,
    commands: Commands,
    idempotency_key: IdempotencyKey,
) -> PaymentPreviewResponse:
    """Answers with the preview the user has to approve, including the balance it would leave.

    Repeating this with the same key returns the same intent rather than making a second one.
    """
    command = http_mapper.payment_from_dto(body, account_id, idempotency_key)
    return http_mapper.preview_to_dto(await commands.dispatch(command))


@payments_router.post("/{intent_id}/confirm", summary="Confirm it (moves the money, once)")
async def confirm_payment(
    account_id: UUID, intent_id: UUID, commands: Commands
) -> PaymentReceiptResponse:
    """The receipt points at the ledger movement, so the figure on it can be checked rather than
    believed. Repeating a confirmed intent returns its original receipt without booking again;
    cancelled or expired intents remain terminal refusals."""
    command = ConfirmPaymentCommand(account_id=account_id, intent_id=intent_id)
    return http_mapper.receipt_to_dto(await commands.dispatch(command))


@payments_router.post("/{intent_id}/cancel", status_code=204, summary="Cancel it")
async def cancel_payment(account_id: UUID, intent_id: UUID, commands: Commands) -> Response:
    """204 because there is nothing to say: no money moved and the intent is closed. Cancelling is
    terminal — preparing again is cheap, and an intent that can be revived is an agreement with no
    expiry."""
    await commands.dispatch(CancelPaymentCommand(account_id=account_id, intent_id=intent_id))
    return Response(status_code=204)
