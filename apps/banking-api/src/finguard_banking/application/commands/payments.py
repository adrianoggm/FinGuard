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
from finguard_banking.domain.enums import Rail
from finguard_banking.domain.errors import (
    AccountNotFoundError,
    ContactHasNoAccountError,
    InvalidValueError,
)
from finguard_banking.domain.models.payment import PaymentPreview, PaymentReceipt
from finguard_banking.domain.ports.account_directory import AccountDirectoryPort
from finguard_banking.domain.ports.contact_directory import ContactDirectoryPort
from finguard_banking.domain.ports.payment_gateway import PaymentGatewayPort
from finguard_banking.domain.value_objects.destination import (
    Destination,
    ToAccount,
    ToOwnAccount,
    ToPhone,
)
from finguard_banking.domain.value_objects.iban import Iban
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
    rail: Rail = Rail.BIZUM
    # An account the holder typed rather than one this service had. Already checked by the time it
    # arrives — `Iban` refuses anything that fails its check digits — so what is left here is which
    # combinations name a destination at all.
    recipient_iban: Iban | None = None
    # Another account of this holder. An id and not an IBAN: an id the caller was shown cannot
    # resolve to a different account than the one they picked, and a number can.
    to_account_id: UUID | None = None

    def __post_init__(self) -> None:
        if self.to_account_id is not None:
            if self.rail is not Rail.INTERNAL:
                raise InvalidValueError("one of your own accounts is only an internal destination")
            if self.recipient_iban is not None or self.recipient_contact_id is not None:
                raise InvalidValueError("an internal transfer goes to one of your accounts, only")
            if self.to_account_id == self.account_id:
                # A transaction debiting and crediting one account balances perfectly, so every
                # integrity check in this ledger would accept it and the balance would not move.
                raise InvalidValueError("an account cannot transfer to itself")
            return
        if self.rail is Rail.INTERNAL:
            raise InvalidValueError("an internal transfer needs one of your own accounts")
        if self.recipient_iban is not None:
            if self.rail is not Rail.TRANSFER:
                raise InvalidValueError("an account number is only a destination for a transfer")
            if self.recipient_contact_id is not None:
                raise InvalidValueError(
                    "a transfer goes to a saved contact or to a typed account, not both"
                )
            if self.recipient_name is None:
                raise InvalidValueError("a transfer to a typed account needs a name")
            return
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
    def __init__(
        self,
        contacts: ContactDirectoryPort,
        payments: PaymentGatewayPort,
        accounts: AccountDirectoryPort | None = None,
    ) -> None:
        self._contacts = contacts
        self._payments = payments
        # Only the internal rail needs it, and a deployment that has not wired it refuses that rail
        # rather than skipping the check. Optional here and not in the signature of the check: an
        # ownership test that can be absent is an ownership test.
        self._accounts = accounts

    async def handle(self, command: PreparePaymentCommand) -> PaymentPreview:
        return await self._payments.prepare(
            account_id=command.account_id,
            to=await self._destination(command),
            amount=command.amount,
            key=command.idempotency_key,
        )

    async def _destination(self, command: PreparePaymentCommand) -> Destination:
        """Who or what gets the money, resolved before anything is written.

        An account the holder typed needs no lookup and gets none: there is no contact to find, and
        inventing one to satisfy a shape would put a row in the directory that nobody asked to save.

        Everything else goes through this account's own contacts, by identifier or by name, and a
        transfer additionally needs that contact to *have* an account. Saying "no tengo la cuenta de
        Marta" is a better answer than a transfer that fails at the gateway with somebody's money
        already in flight.
        """
        if command.to_account_id is not None:
            return await self._own_account(command)

        if command.recipient_iban is not None:
            assert command.recipient_name is not None  # the command's own invariant
            return ToAccount(name=command.recipient_name, iban=command.recipient_iban)

        if command.recipient_contact_id is not None:
            contact = await self._contacts.one(command.account_id, command.recipient_contact_id)
        else:
            assert command.recipient_name is not None  # the command's own invariant
            contact = await self._contacts.resolve(command.account_id, command.recipient_name)

        if command.rail is Rail.BIZUM:
            return ToPhone(
                contact_id=contact.contact_id, name=contact.display_name, phone=contact.phone
            )
        if contact.iban is None:
            raise ContactHasNoAccountError(contact.display_name)
        return ToAccount(
            name=contact.display_name,
            iban=Iban(contact.iban),
            contact_id=contact.contact_id,
        )

    async def _own_account(self, command: PreparePaymentCommand) -> Destination:
        """The holder's other account, checked to be theirs before anything is written.

        The foreign key says the account exists. It does not say it is yours, and this is the only
        place that asks — an internal transfer to a stranger's account would be a transfer with no
        preview naming a stranger, which is the worst shape a payment can have.

        Read through `all_of`, which goes through the same declaration and the same view every
        other read does: an account of another holder is not "not authorised", it is not there.
        """
        assert command.to_account_id is not None  # the command's own invariant
        if self._accounts is None:  # pragma: no cover - wiring, asserted at startup
            raise InvalidValueError("this deployment cannot move money between accounts")

        holdings = await self._accounts.all_of(command.account_id)
        mine = {holding.account_id: holding for holding in holdings}
        target = mine.get(command.to_account_id)
        if target is None:
            raise AccountNotFoundError(command.to_account_id)
        return ToOwnAccount(
            account_id=target.account_id,
            # The holder's own label, or the kind when they never gave one. Snapshotted onto the
            # intent for the reason a transfer snapshots its IBAN: the preview is what was agreed
            # to, and a name edited in between must not change what somebody approved.
            name=target.display_name or target.kind.value,
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
