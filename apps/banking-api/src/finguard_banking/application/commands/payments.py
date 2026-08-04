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
from finguard_banking.domain.errors import InvalidValueError
from finguard_banking.domain.models.payment import PaymentPreview, PaymentReceipt
from finguard_banking.domain.ports.contact_directory import ContactDirectoryPort
from finguard_banking.domain.ports.payment_gateway import PaymentGatewayPort
from finguard_banking.domain.value_objects.money import Money


@dataclass(frozen=True)
class PreparePaymentCommand(Command[PaymentPreview]):
    """A payment somebody intends to make. Nothing moves.

    The recipient arrives as a name or as the identifier of a contact the caller was shown, and
    exactly one of the two. Either way it is resolved against this account's own contacts: an
    identifier taken on faith would let a caller pay somebody who is not in their list, and one
    read back through the account-scoped view cannot.

    Both set would be two answers to "who gets paid" with no rule about which wins, and neither
    set is a payment to nobody. Refused on construction, because a command that can be built
    wrong defers the decision to whichever handler reads the fields in which order.
    """

    account_id: UUID
    amount: Money
    idempotency_key: str
    recipient_name: str | None = None
    recipient_contact_id: UUID | None = None

    def __post_init__(self) -> None:
        if (self.recipient_name is None) == (self.recipient_contact_id is None):
            raise InvalidValueError(
                "a payment names its recipient either by name or by contact identifier"
            )


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
        if command.recipient_contact_id is not None:
            contact = await self._contacts.one(command.account_id, command.recipient_contact_id)
        else:
            assert command.recipient_name is not None  # the command's own invariant
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
