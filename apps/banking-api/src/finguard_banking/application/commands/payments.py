"""The write side of a payment: prepare, confirm, cancel.

Three commands rather than one with a mode, because they are three different risks. Preparing is
free and can be repeated. Confirming moves money and must happen at most once. Cancelling closes
the intent and cannot be undone.

Resolving the recipient lives in `PreparePaymentHandler` and not in the gateway: turning "Marta"
into a person is a decision about who gets paid, and it can fail in a way the user has to answer
(`AmbiguousContactError`). The gateway is then given an identifier and no room to interpret.
"""

from dataclasses import dataclass
from uuid import UUID

from finguard_banking.application.bus.command_bus import Command
from finguard_banking.domain.models.payment import PaymentPreview, PaymentReceipt
from finguard_banking.domain.ports.contact_directory import ContactDirectoryPort
from finguard_banking.domain.ports.payment_gateway import PaymentGatewayPort
from finguard_banking.domain.value_objects.money import Money


@dataclass(frozen=True)
class PreparePaymentCommand(Command[PaymentPreview]):
    """A payment somebody intends to make. Nothing moves.

    The recipient arrives as a name, resolved against this account's own contacts. An identifier
    would let a caller pay somebody who is not in their list.
    """

    account_id: UUID
    recipient_name: str
    amount: Money
    idempotency_key: str


@dataclass(frozen=True)
class ConfirmPaymentCommand(Command[PaymentReceipt]):
    """The step that moves the money. Safe to retry: the second attempt is refused, not repeated."""

    account_id: UUID
    intent_id: UUID


@dataclass(frozen=True)
class CancelPaymentCommand(Command[None]):
    account_id: UUID
    intent_id: UUID


class PreparePaymentHandler:
    def __init__(self, contacts: ContactDirectoryPort, payments: PaymentGatewayPort) -> None:
        self._contacts = contacts
        self._payments = payments

    async def handle(self, command: PreparePaymentCommand) -> PaymentPreview:
        contact = await self._contacts.resolve(command.account_id, command.recipient_name)
        return await self._payments.prepare(
            account_id=command.account_id,
            contact_id=contact.contact_id,
            amount=command.amount,
            key=command.idempotency_key,
        )


class ConfirmPaymentHandler:
    def __init__(self, payments: PaymentGatewayPort) -> None:
        self._payments = payments

    async def handle(self, command: ConfirmPaymentCommand) -> PaymentReceipt:
        return await self._payments.confirm(
            account_id=command.account_id, intent_id=command.intent_id
        )


class CancelPaymentHandler:
    def __init__(self, payments: PaymentGatewayPort) -> None:
        self._payments = payments

    async def handle(self, command: CancelPaymentCommand) -> None:
        await self._payments.cancel(account_id=command.account_id, intent_id=command.intent_id)
