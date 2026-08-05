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

from dataclasses import dataclass, field, replace
from uuid import UUID

from finguard_orchestrator.application.bus.command_bus import Command
from finguard_orchestrator.domain.enums import PaymentOutcome, PlanningOutcome, Rail
from finguard_orchestrator.domain.errors import (
    AmbiguousRecipientError,
    BankingUnavailableError,
    IntentGoneError,
    InvalidValueError,
    PaymentRefusedError,
    RecipientHasNoAccountError,
    RecipientNotFoundError,
)
from finguard_orchestrator.domain.models.contact import Contact
from finguard_orchestrator.domain.models.payment_result import PaymentPreview, PaymentReceipt
from finguard_orchestrator.domain.ports.contact_directory import ContactDirectoryPort
from finguard_orchestrator.domain.ports.payment_gateway import PaymentGatewayPort
from finguard_orchestrator.domain.ports.payment_reader import PaymentReaderPort
from finguard_orchestrator.domain.services import (
    confirmation,
    payment_router,
    payment_words,
    payment_writer,
)
from finguard_orchestrator.domain.value_objects.guard_policy import EVERY_RULE, GuardPolicy
from finguard_orchestrator.domain.value_objects.payment_draft import PaymentDraft, PaymentField
from finguard_orchestrator.domain.value_objects.payment_request import PaymentRequest

# Banking's code for a balance that does not cover the payment. Matched on the code, never on the
# sentence: prose across a service boundary is how two services start disagreeing.
INSUFFICIENT_FUNDS = "insufficient_funds"


@dataclass(frozen=True)
class SpokenPaymentDraft:
    """The payment so far, flat like every Result piece.

    The client echoes it on the next turn. Without it the reply to "¿de cuánto?" is a sentence with
    no history — which is how "15 euros" reached the financial planner and came back asking which
    month it was about.
    """

    recipient_name: str | None = None
    # As a string, like every identifier that leaves this layer. The client sends back the one it
    # drew a button for.
    recipient_contact_id: str | None = None
    amount_minor: int | None = None
    currency: str = "EUR"
    concept: str | None = None


@dataclass(frozen=True)
class SpokenCandidate:
    """One of several people the recipient could be. The identifier is what lets the next turn name
    a contact instead of a string to be interpreted again."""

    contact_id: str
    display_name: str
    phone: str


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
    # Where a transfer went, and how it went. Flat like every other field here, and both
    # absent for a Bizum — the rail is what says which identifier to read.
    recipient_iban: str | None = None
    rail: str = Rail.BIZUM.value
    amount_minor: int | None = None
    currency: str | None = None
    concept: str | None = None
    balance_after_minor: int | None = None
    expires_at: str | None = None
    transaction_id: UUID | None = None
    # Why it was refused, for the log and the evaluation. Never the sentence the user reads.
    reason: str | None = None
    # Present while the payment is still being built: what the client echoes back so the next reply
    # completes it. Absent once there is an intent to confirm, or once the payment is refused.
    draft: SpokenPaymentDraft | None = None
    # Present when the recipient names several contacts. Structured, so a client can offer buttons
    # and send back which one — rather than the user retyping a name to be re-interpreted.
    candidates: list[SpokenCandidate] = field(default_factory=list)


@dataclass(frozen=True)
class PreparePaymentCommand(Command[PaymentAnswer]):
    """A sentence that asks to pay somebody. Moves nothing.

    `idempotency_key` comes from the edge rather than being made here, for the same reason `today`
    does on the question path: a handler that invents one cannot be replayed.
    """

    sentence: str
    account_id: UUID
    idempotency_key: str
    # The payment already under way, echoed by the caller. This service keeps no conversation, so a
    # half-built payment lives in the client's hands exactly like a half-built query does.
    pending: PaymentDraft | None = None


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
    rail: Rail = Rail.BIZUM


@dataclass(frozen=True)
class ConfirmPaymentCommand(Command[PaymentAnswer]):
    """A reply to a preview. `reply` is the user's own words, read deterministically."""

    reply: str
    account_id: UUID
    intent_id: UUID
    # The preview being answered, if the caller echoed it back. Only used to repeat the question
    # when the reply is neither yes nor no — the decision to pay never depends on it.
    preview: PaymentPreview | None = None


def _as_draft(request: PaymentRequest) -> PaymentDraft:
    """A request read back as the draft it came from.

    Used when banking refuses a transfer for want of an account number: everything else about the
    payment is settled and asking for it again would make the holder retype what they just said.
    """
    return PaymentDraft(
        recipient_name=request.recipient_name,
        recipient_contact_id=request.recipient_contact_id,
        amount_minor=request.amount_minor,
        currency=request.currency,
        concept=request.concept,
        rail=request.rail,
    )


def _drafted(draft: PaymentDraft) -> SpokenPaymentDraft:
    """Flattened, so no domain object leaves this layer."""
    return SpokenPaymentDraft(
        recipient_name=draft.recipient_name,
        recipient_contact_id=(
            str(draft.recipient_contact_id) if draft.recipient_contact_id else None
        ),
        amount_minor=draft.amount_minor,
        currency=draft.currency,
        concept=draft.concept,
    )


def _asking_for(draft: PaymentDraft) -> PaymentAnswer:
    """The question a half-built payment still needs answered, carrying itself.

    Which slot is missing decides the question, and the draft travels with it — a question the
    client cannot answer in context is the defect this whole path exists to fix.
    """
    return PaymentAnswer(
        outcome=PaymentOutcome.NEEDS_CLARIFICATION.value,
        text=payment_writer.still_missing(draft),
        draft=_drafted(draft),
    )


async def _previewed(
    bank: PaymentGatewayPort,
    request: PaymentRequest,
    *,
    account_id: UUID,
    key: str,
    directory: tuple[Contact, ...] = (),
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
        #
        # It used to return the names and nothing else, so the amount and the concept the user had
        # already given were lost — "quiero hacerle 15 euros de bizum a Marta con el concepto tapas"
        # came back asking which Marta, and the reply then reached the financial planner. The
        # recipient is cleared because it is the question; everything else survives it.
        names = {name.casefold() for name in several.candidates}
        return PaymentAnswer(
            outcome=PaymentOutcome.NEEDS_CLARIFICATION.value,
            text=payment_writer.ambiguous(several.name, several.candidates),
            reason=several.code,
            draft=_drafted(
                PaymentDraft(amount_minor=request.amount_minor, concept=request.concept)
            ),
            candidates=[
                SpokenCandidate(str(c.contact_id), c.display_name, c.phone)
                for c in directory
                if c.display_name.casefold() in names
            ],
        )
    except RecipientNotFoundError as nobody:
        return PaymentAnswer(
            outcome=PaymentOutcome.REFUSED.value,
            text=payment_writer.not_a_contact(nobody.name),
            reason=nobody.code,
        )
    except RecipientHasNoAccountError as unreachable:
        # Not a refusal and not a not-found. The person exists, the holder chose them, and what
        # is missing is a number they can supply — so the conversation stays open on the draft
        # rather than ending on an apology.
        return PaymentAnswer(
            outcome=PaymentOutcome.NEEDS_CLARIFICATION.value,
            text=payment_writer.no_account_for(unreachable.name),
            draft=_drafted(_as_draft(request)),
            reason=unreachable.code,
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
        text=payment_writer.confirm_question(preview, request.concept),
        intent_id=preview.intent_id,
        recipient=preview.recipient,
        recipient_phone=preview.recipient_phone,
        recipient_iban=preview.recipient_iban,
        rail=preview.rail.value,
        amount_minor=preview.amount_minor,
        currency=preview.currency,
        concept=request.concept,
        balance_after_minor=preview.balance_after_minor,
        expires_at=preview.expires_at.isoformat(),
    )


@dataclass(frozen=True)
class PrepareOwnTransferCommand(Command[PaymentAnswer]):
    """Money from one of the holder's accounts into another.

    Its own command rather than a flag on the chosen payment, for the reason the rail is its own
    rail: there is no recipient to name and no contact to resolve, so a shared shape would carry
    three fields that must all be empty and one that must not.

    The destination is an id the caller was shown. Whether it is *theirs* is banking's to check,
    against the same view that listed it — this service holds no rule about whose account is whose.
    """

    account_id: UUID
    to_account_id: UUID
    amount_minor: int
    currency: str
    idempotency_key: str


class PrepareOwnTransferHandler:
    """No model and no reader: an account was picked from a list and an amount was typed."""

    def __init__(self, bank: PaymentGatewayPort) -> None:
        self._bank = bank

    async def handle(self, command: PrepareOwnTransferCommand) -> PaymentAnswer:
        try:
            request = PaymentRequest(
                # No recipient. The preview comes back naming the account, because banking snapshots
                # the holder's own label onto the intent — this end never invents a name for it.
                recipient_name="",
                amount_minor=command.amount_minor,
                currency=command.currency,
                rail=Rail.INTERNAL,
                to_account_id=command.to_account_id,
            )
        except InvalidValueError as refused:
            return PaymentAnswer(
                outcome=PaymentOutcome.NOT_UNDERSTOOD.value,
                text=payment_writer.not_understood(),
                reason=str(refused),
            )
        return await _previewed(
            self._bank, request, account_id=command.account_id, key=command.idempotency_key
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
                rail=command.rail,
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
    """Reads a sentence into a payment, or continues one that is already under way.

    The second half is what the reported conversation needed. "¿De cuánto quieres hacerle el bizum a
    Marta?" followed by "15 euros" used to start again from nothing: the recipient was thrown away
    with the question, and a sentence with money and no payment verb is not a payment, so it reached
    the financial planner.

    A continuation never consults the model. Both slots a payment can be missing are closed
    vocabularies — an amount is a grammar, and a recipient is this account's own contact list — so
    there is nothing to infer, and the turn that answers a question should not be able to be read as
    a different intent.
    """

    def __init__(
        self,
        reader: PaymentReaderPort,
        bank: PaymentGatewayPort,
        policy: GuardPolicy = EVERY_RULE,
        *,
        contacts: ContactDirectoryPort,
    ) -> None:
        self._reader = reader
        self._bank = bank
        self._policy = policy
        self._contacts = contacts

    async def handle(self, command: PreparePaymentCommand) -> PaymentAnswer:
        if command.pending is not None:
            continued = await self._continue(command)
            if continued is not None:
                return continued

        reading = await self._reader.read(command.sentence, policy=self._policy)

        # Stamped here, from the sentence, on whatever the reader produced. The rail is not
        # in the prompt and not in the schema: "bizum" and "transferencia" are a closed
        # vocabulary of two, and letting a decoder choose between them would be letting it
        # choose how money leaves an account. The reader reads who and how much.
        rail = payment_words.rail_in(command.sentence)

        if not reading.is_payable:
            # On the draft too, so the rail survives the turn that asks for the amount:
            # "hazme una transferencia a Elena" then "200 euros" is still a transfer, and
            # the second sentence names no rail at all.
            drafted = replace(reading.draft, rail=rail) if reading.draft else None
            return _declined(reading.outcome, reading.question, reading.reason, drafted)

        assert reading.request is not None  # is_payable is exactly this condition

        return await _previewed(
            self._bank,
            replace(reading.request, rail=rail),
            account_id=command.account_id,
            key=command.idempotency_key,
            # So an ambiguous name comes back with identifiers rather than as prose to retype.
            directory=await self._contacts.all_of(command.account_id),
        )

    async def _chosen(
        self, pending: PaymentDraft, account_id: UUID
    ) -> "PaymentDraft | PaymentAnswer":
        """The draft with the picked contact's name on it, or a refusal.

        The identifier came back from a client, so it is looked up in this account's own
        directory rather than believed. Banking checks it again — that is the boundary that
        matters — and doing it here answers an unknown contact with a sentence, not a round trip.

        The name is filled in from the directory because everything downstream says it: the
        confirmation prompt, the grounding check, the question that asks for the amount. What the
        identifier changes is who gets paid, not what the assistant calls them.
        """
        directory = await self._contacts.all_of(account_id)
        found = next((c for c in directory if c.contact_id == pending.recipient_contact_id), None)
        if found is None:
            return PaymentAnswer(
                outcome=PaymentOutcome.REFUSED.value,
                text=payment_writer.not_a_contact_chosen(),
                reason="contact_not_found",
            )
        return replace(pending, recipient_name=found.display_name)

    async def _continue(self, command: PreparePaymentCommand) -> PaymentAnswer | None:
        """The pending payment plus whatever this sentence adds, or None if it adds nothing.

        None is the useful answer as often as an answer is: "no sé" fills no slot, and a sentence
        nobody can read deterministically belongs on the ordinary path.
        """
        assert command.pending is not None
        pending = command.pending
        # An identifier with no name beside it is a choice that has not been applied yet: this
        # service fills the name in from its own directory, so a draft carrying both has been
        # through here already. That is what makes the choice *this turn's* new information without
        # anything having to remember which turn it arrived on — and it is why a later sentence
        # about something else still falls through to the ordinary path.
        chose = pending.recipient_contact_id is not None and pending.recipient_name is None
        if chose:
            chosen = await self._chosen(pending, command.account_id)
            if isinstance(chosen, PaymentAnswer):
                return chosen
            pending = chosen
        missing = pending.missing

        amount = payment_words.amount_in(command.sentence)
        concept = payment_words.concept_in(command.sentence)
        clears_concept = payment_words.clears_concept(command.sentence)
        named: tuple[Contact, ...] = ()
        if PaymentField.RECIPIENT in missing:
            # Only when the recipient is what is missing. Otherwise a sentence that happens to
            # mention a contact — "los 15 de la cena con Marta" — would silently change who is
            # being paid.
            named = payment_words.contacts_in(
                command.sentence, await self._contacts.all_of(command.account_id)
            )

        if amount is None and not named and not chose and concept is None and not clears_concept:
            if payment_router.resumes(command.sentence):
                # An explicit resumption adds no field and is not nothing: it is the turn that
                # brings the payment back. Falling through here read the sentence afresh, and
                # "continuar con el bizum" names nobody — so the reader found no recipient and the
                # Bizum started over, having been suspended precisely to avoid that.
                #
                # It asks for whatever is still missing, which is the question the payment was
                # holding when the subject changed.
                return _asking_for(pending)
            return None

        if clears_concept:
            # `filled_with` treats None as "not supplied", correctly for ordinary continuations.
            # Here absence is the explicit new value, so apply it before merging the other fields.
            pending = replace(pending, concept=None)

        if len(named) > 1:
            # Ambiguous, and this is the one place that can say so with the names in hand. Banking
            # refuses to choose too, and reaching it would spend a round trip to be told the same
            # thing without the candidates.
            drafted = pending.filled_with(
                PaymentDraft(
                    amount_minor=amount, concept=payment_words.concept_in(command.sentence)
                )
            )
            return PaymentAnswer(
                outcome=PaymentOutcome.NEEDS_CLARIFICATION.value,
                text=payment_writer.several_candidates(tuple(c.display_name for c in named)),
                draft=_drafted(drafted),
                candidates=[
                    SpokenCandidate(str(c.contact_id), c.display_name, c.phone) for c in named
                ],
            )

        turn = PaymentDraft(
            recipient_name=named[0].display_name if named else None,
            amount_minor=amount,
            # Read on the same turn as the amount, because it arrives in the same breath: "pues 15
            # euros con concepto de comida a Marta" answered the amount question and carried the
            # note, and reading one without the other lost it exactly where it was written.
            concept=concept,
        )
        drafted = pending.filled_with(turn)
        request = drafted.into_request()
        if request is None:
            return _asking_for(drafted)

        return await _previewed(
            self._bank, request, account_id=command.account_id, key=command.idempotency_key
        )


class ConfirmPaymentHandler:
    """The only place that calls `confirm`, and it does so for one input: an unambiguous yes."""

    def __init__(self, bank: PaymentGatewayPort) -> None:
        self._bank = bank

    async def handle(self, command: ConfirmPaymentCommand) -> PaymentAnswer:
        if confirmation.is_refusal(command.reply):
            try:
                await self._bank.cancel(account_id=command.account_id, intent_id=command.intent_id)
            except (IntentGoneError, PaymentRefusedError) as gone:
                return PaymentAnswer(
                    outcome=PaymentOutcome.REFUSED.value,
                    text=payment_writer.no_longer_live(),
                    intent_id=command.intent_id,
                    reason=gone.reason,
                )
            except BankingUnavailableError as down:
                # Just like a confirmation timeout, the result is unknown. Keep the intent in the
                # conversation so the same cancellation can be retried instead of starting over.
                return PaymentAnswer(
                    outcome=PaymentOutcome.BANKING_UNAVAILABLE.value,
                    text=payment_writer.cancellation_unavailable(),
                    intent_id=command.intent_id,
                    reason=str(down),
                )
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
                text=payment_writer.confirmation_unavailable(),
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
    """A refusal of the *request*, which is never a payment that already happened.

    The fall-through used to be `no_longer_live()` — "ese pago ya no está pendiente, comprueba tus
    movimientos" — and a `PaymentRefusedError` cannot mean that: it is raised for a balance that
    did not cover the payment, or for a 4xx this service has no name for. Nothing was ever pending
    in either case, so the sentence sent somebody looking through their movements for a payment
    that was never made. An expired or already-paid intent arrives as `IntentGoneError`, which has
    its own handlers and keeps that sentence.
    """
    text = (
        payment_writer.insufficient_funds()
        if reason == INSUFFICIENT_FUNDS
        else payment_writer.nothing_was_sent()
    )
    return PaymentAnswer(outcome=PaymentOutcome.REFUSED.value, text=text, reason=reason)


def _declined(
    outcome: PlanningOutcome,
    question: str | None,
    reason: str | None,
    draft: PaymentDraft | None = None,
) -> PaymentAnswer:
    """The readings that are not a payment. Each gets its own sentence: collapsing them would leave
    the user unable to tell whether to rephrase, wait, or give up."""
    match outcome:
        case PlanningOutcome.NEEDS_CLARIFICATION:
            return PaymentAnswer(
                outcome=PaymentOutcome.NEEDS_CLARIFICATION.value,
                text=question or payment_writer.not_understood(),
                reason=reason,
                # Only a question carries one. A refusal that echoed a draft would invite the client
                # to keep answering a question nobody asked.
                draft=_drafted(draft) if draft else None,
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
