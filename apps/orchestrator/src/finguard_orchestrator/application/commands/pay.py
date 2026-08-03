"""The payment conversation, in two commands: prepare, then confirm.

§4.1 as code. Preparing reads the sentence, asks banking to hold an intent and answers with the
preview; confirming carries an explicit reply and either pays or does not. They are two commands
rather than one with a flag, because the gap between them is the whole point — a flag can be set by
the same code that filled in the amount.

Commands rather than queries even though preparing looks like a read: it writes an intent, and it
goes through the command bus so it is audited beside the confirmation rather than in a different
book.

**The one property to hold on to while reading this:** nothing calls `confirm` except an unambiguous
yes. Every other path — a question, an ungrounded reading, a "sí pero 30", a bank refusal —
ends in a sentence and no movement.
"""

from dataclasses import dataclass
from uuid import UUID

from finguard_orchestrator.application.bus.command_bus import Command
from finguard_orchestrator.domain.enums import PaymentOutcome, PlanningOutcome
from finguard_orchestrator.domain.errors import (
    AmbiguousRecipientError,
    BankingUnavailableError,
    IntentGoneError,
    InvalidValueError,
    PaymentRefusedError,
    RecipientNotFoundError,
)
from finguard_orchestrator.domain.models.payment_result import PaymentPreview, PaymentReceipt
from finguard_orchestrator.domain.ports.payment_gateway import PaymentGatewayPort
from finguard_orchestrator.domain.ports.payment_reader import PaymentReaderPort
from finguard_orchestrator.domain.services import confirmation, payment_writer
from finguard_orchestrator.domain.value_objects.guard_policy import EVERY_RULE, GuardPolicy
from finguard_orchestrator.domain.value_objects.payment_request import PaymentRequest

# Banking's code for a balance that does not cover the payment. Matched on the code, never on the
# sentence: prose across a service boundary is how two services start disagreeing.
INSUFFICIENT_FUNDS = "insufficient_funds"


@dataclass(frozen=True)
class PaymentAnswer:
    """Flat, like every Result: no domain object leaves the application layer.

    `intent_id` is what a client echoes back to confirm, and it is the only piece of state the
    conversation keeps. It is held by whoever is having the conversation rather than by this
    service — the same decision as the follow-up plan on the question path.
    """

    outcome: str
    text: str
    intent_id: UUID | None = None
    recipient: str | None = None
    recipient_phone: str | None = None
    amount_minor: int | None = None
    currency: str | None = None
    balance_after_minor: int | None = None
    expires_at: str | None = None
    transaction_id: UUID | None = None
    # Why it was refused, for the log and the evaluation. Never the sentence the user reads.
    reason: str | None = None


@dataclass(frozen=True)
class PreparePaymentCommand(Command[PaymentAnswer]):
    """A sentence that asks to pay somebody. Moves nothing.

    `idempotency_key` comes from the edge rather than being made here, for the same reason `today`
    does on the question path: a handler that invents one cannot be replayed.
    """

    sentence: str
    account_id: UUID
    idempotency_key: str


@dataclass(frozen=True)
class PrepareChosenPaymentCommand(Command[PaymentAnswer]):
    """A payment somebody built in a form: a contact picked from a list, an amount typed in a field.

    Separate from the sentence version because there is nothing to read. No model, and therefore no
    grounding check — grounding exists to catch a figure a model invented, and here the figure was
    typed and the recipient was chosen. What does *not* change is the rest of §4.1: the preview and
    the explicit confirmation are the same ones, because they protect against a mistake rather than
    against a misreading.
    """

    account_id: UUID
    recipient_name: str
    amount_minor: int
    currency: str
    idempotency_key: str


@dataclass(frozen=True)
class ConfirmPaymentCommand(Command[PaymentAnswer]):
    """A reply to a preview. `reply` is the user's own words, read deterministically."""

    reply: str
    account_id: UUID
    intent_id: UUID
    # The preview being answered, if the caller echoed it back. Only used to repeat the question
    # when the reply is neither yes nor no — the decision to pay never depends on it.
    preview: PaymentPreview | None = None


async def _previewed(
    bank: PaymentGatewayPort, request: PaymentRequest, *, account_id: UUID, key: str
) -> PaymentAnswer:
    """Asks the bank to hold the intent and turns every refusal into something sayable.

    Shared by the two preparation paths. A second copy of this ladder would be a second opinion
    about what "the bank said no" means, and the sentence path and the form path must not answer
    a balance refusal differently.
    """
    try:
        preview = await bank.prepare(request, account_id=account_id, key=key)
    except AmbiguousRecipientError as several:
        # A question, not an error: the user picks, and they cannot without the names.
        return PaymentAnswer(
            outcome=PaymentOutcome.NEEDS_CLARIFICATION.value,
            text=payment_writer.ambiguous(several.name, several.candidates),
            reason=several.code,
        )
    except RecipientNotFoundError as nobody:
        return PaymentAnswer(
            outcome=PaymentOutcome.REFUSED.value,
            text=payment_writer.not_a_contact(nobody.name),
            reason=nobody.code,
        )
    except PaymentRefusedError as refused:
        return _refusal(refused.reason)
    except BankingUnavailableError as down:
        return PaymentAnswer(
            outcome=PaymentOutcome.BANKING_UNAVAILABLE.value,
            text=payment_writer.bank_unavailable(),
            reason=str(down),
        )

    return PaymentAnswer(
        outcome=PaymentOutcome.AWAITING_CONFIRMATION.value,
        text=payment_writer.confirm_question(preview),
        intent_id=preview.intent_id,
        recipient=preview.recipient,
        recipient_phone=preview.recipient_phone,
        amount_minor=preview.amount_minor,
        currency=preview.currency,
        balance_after_minor=preview.balance_after_minor,
        expires_at=preview.expires_at.isoformat(),
    )


class PrepareChosenPaymentHandler:
    """The form path. It builds the request and stops there — no reader, no policy."""

    def __init__(self, bank: PaymentGatewayPort) -> None:
        self._bank = bank

    async def handle(self, command: PrepareChosenPaymentCommand) -> PaymentAnswer:
        try:
            request = PaymentRequest(
                recipient_name=command.recipient_name.strip(),
                amount_minor=command.amount_minor,
                currency=command.currency,
            )
        except InvalidValueError as refused:
            # A payment of nothing, a negative one, or one beyond the ceiling. The same value
            # object the sentence path builds, so a form cannot ask for what a sentence cannot.
            return PaymentAnswer(
                outcome=PaymentOutcome.NOT_UNDERSTOOD.value,
                text=payment_writer.not_understood(),
                reason=str(refused),
            )
        return await _previewed(
            self._bank, request, account_id=command.account_id, key=command.idempotency_key
        )


class PreparePaymentHandler:
    def __init__(
        self,
        reader: PaymentReaderPort,
        bank: PaymentGatewayPort,
        policy: GuardPolicy = EVERY_RULE,
    ) -> None:
        self._reader = reader
        self._bank = bank
        self._policy = policy

    async def handle(self, command: PreparePaymentCommand) -> PaymentAnswer:
        reading = await self._reader.read(command.sentence, policy=self._policy)

        if not reading.is_payable:
            return _declined(reading.outcome, reading.question, reading.reason)

        assert reading.request is not None  # is_payable is exactly this condition

        return await _previewed(
            self._bank,
            reading.request,
            account_id=command.account_id,
            key=command.idempotency_key,
        )


class ConfirmPaymentHandler:
    """The only place that calls `confirm`, and it does so for one input: an unambiguous yes."""

    def __init__(self, bank: PaymentGatewayPort) -> None:
        self._bank = bank

    async def handle(self, command: ConfirmPaymentCommand) -> PaymentAnswer:
        if confirmation.is_refusal(command.reply):
            await self._bank.cancel(account_id=command.account_id, intent_id=command.intent_id)
            return PaymentAnswer(
                outcome=PaymentOutcome.CANCELLED.value,
                text=payment_writer.cancelled(),
                intent_id=command.intent_id,
            )

        if not confirmation.is_confirmation(command.reply):
            # Neither yes nor no. The intent is left exactly where it was and the question is asked
            # again: a reply that added a condition or a different amount is about a payment other
            # than the one on the table, and guessing which is the one thing that must not happen.
            return PaymentAnswer(
                outcome=PaymentOutcome.AWAITING_CONFIRMATION.value,
                text=payment_writer.unclear(command.preview),
                intent_id=command.intent_id,
            )

        try:
            paid = await self._bank.confirm(
                account_id=command.account_id, intent_id=command.intent_id
            )
        except PaymentRefusedError as refused:
            return _refusal(refused.reason)
        except IntentGoneError as gone:
            return PaymentAnswer(
                outcome=PaymentOutcome.REFUSED.value,
                text=payment_writer.no_longer_live(),
                intent_id=command.intent_id,
                reason=gone.reason,
            )
        except BankingUnavailableError as down:
            # The dangerous case, and the honest sentence for it. A confirmation that timed out may
            # have been executed, so the reply does not claim nothing happened — retrying is
            # safe because banking keys the confirmation on the intent, and the second attempt
            # is refused.
            return PaymentAnswer(
                outcome=PaymentOutcome.BANKING_UNAVAILABLE.value,
                text=payment_writer.bank_unavailable(),
                intent_id=command.intent_id,
                reason=str(down),
            )

        return _paid(paid)


def _paid(paid: PaymentReceipt) -> PaymentAnswer:
    return PaymentAnswer(
        outcome=PaymentOutcome.PAID.value,
        text=payment_writer.receipt(paid),
        intent_id=paid.intent_id,
        recipient=paid.recipient,
        amount_minor=paid.amount_minor,
        currency=paid.currency,
        balance_after_minor=paid.balance_after_minor,
        transaction_id=paid.transaction_id,
    )


def _refusal(reason: str) -> PaymentAnswer:
    text = (
        payment_writer.insufficient_funds()
        if reason == INSUFFICIENT_FUNDS
        else payment_writer.no_longer_live()
    )
    return PaymentAnswer(outcome=PaymentOutcome.REFUSED.value, text=text, reason=reason)


def _declined(outcome: PlanningOutcome, question: str | None, reason: str | None) -> PaymentAnswer:
    """The readings that are not a payment. Each gets its own sentence: collapsing them would leave
    the user unable to tell whether to rephrase, wait, or give up."""
    match outcome:
        case PlanningOutcome.NEEDS_CLARIFICATION:
            return PaymentAnswer(
                outcome=PaymentOutcome.NEEDS_CLARIFICATION.value,
                text=question or payment_writer.not_understood(),
                reason=reason,
            )
        case PlanningOutcome.UNSUPPORTED:
            return PaymentAnswer(
                outcome=PaymentOutcome.UNSUPPORTED.value,
                text=payment_writer.unsupported(),
                reason=reason,
            )
        case PlanningOutcome.PROVIDER_ERROR:
            return PaymentAnswer(
                outcome=PaymentOutcome.PROVIDER_ERROR.value,
                text=payment_writer.provider_error(),
                reason=reason,
            )
        case _:
            # INVALID_STRUCTURE and INVALID_DOMAIN. `reason` carries which rule refused it, for the
            # log and the evaluation; the user gets one sentence, because the difference between a
            # malformed answer and an ungrounded one is ours, not theirs.
            return PaymentAnswer(
                outcome=PaymentOutcome.NOT_UNDERSTOOD.value,
                text=payment_writer.not_understood(),
                reason=reason,
            )
