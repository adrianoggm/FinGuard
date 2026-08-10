"""The payment conversation: preview, then a person says yes.

§4.1 in two turns. Preparing shows what would happen and moves nothing; confirming is a second
request carrying an explicit reply. The tests are written around one property:

    **nothing here reaches `confirm` except an unambiguous yes to an intent that was previewed.**

Everything else — a question, an ungrounded reading, a "sí pero 30", banking refusing — has to end
somewhere that is not a payment, and each of those has its own answer to the user.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from finguard_orchestrator.application.commands.pay import (
    ConfirmPaymentCommand,
    ConfirmPaymentHandler,
    PreparePaymentCommand,
    PreparePaymentHandler,
)
from finguard_orchestrator.domain.enums import PaymentOutcome, Rail
from finguard_orchestrator.domain.errors import (
    AmbiguousRecipientError,
    BankingUnavailableError,
    IntentGoneError,
    PaymentRefusedError,
    RecipientNotFoundError,
)
from finguard_orchestrator.domain.models.payment_reading import PaymentReading
from finguard_orchestrator.domain.models.payment_result import PaymentPreview, PaymentReceipt
from finguard_orchestrator.domain.value_objects.guard_policy import EVERY_RULE, GuardPolicy
from finguard_orchestrator.domain.value_objects.payment_request import PaymentRequest

ACCOUNT = UUID("11111111-1111-1111-1111-111111111111")
INTENT = UUID("33333333-3333-3333-3333-333333333333")
TRANSACTION = UUID("44444444-4444-4444-4444-444444444444")
SENTENCE = "hazle un bizum de 20 euros a Marta"

REQUEST = PaymentRequest(recipient_name="Marta", amount_minor=2_000, currency="EUR")
PREVIEW = PaymentPreview(
    intent_id=INTENT,
    recipient="Marta Solis Vega",
    recipient_phone="+34600333444",
    amount_minor=2_000,
    currency="EUR",
    balance_before_minor=1_037_042,
    balance_after_minor=1_035_042,
    expires_at=datetime(2026, 8, 3, 10, 5, tzinfo=UTC),
)
RECEIPT = PaymentReceipt(
    intent_id=INTENT,
    transaction_id=TRANSACTION,
    recipient="Marta Solis Vega",
    amount_minor=2_000,
    currency="EUR",
    balance_after_minor=1_035_042,
    confirmed_at=datetime(2026, 8, 3, 10, 1, tzinfo=UTC),
)


class FakeReader:
    def __init__(self, reading: PaymentReading) -> None:
        self._reading = reading
        self.sentences: list[str] = []

    async def read(self, sentence: str, *, policy: GuardPolicy = EVERY_RULE) -> PaymentReading:
        self.sentences.append(sentence)
        return self._reading


class FakeGateway:
    """Counts what it was asked to do. The counts are the assertions that matter: a test that only
    checked the answer text could pass while money moved."""

    def __init__(self, error: Exception | None = None) -> None:
        self._error = error
        self.prepared = 0
        self.confirmed = 0
        self.cancelled = 0
        self.keys: list[str] = []
        self.requests: list[PaymentRequest] = []

    async def prepare(
        self, request: PaymentRequest, *, account_id: UUID, key: str
    ) -> PaymentPreview:
        if self._error is not None:
            raise self._error
        self.prepared += 1
        self.keys.append(key)
        self.requests.append(request)
        return PREVIEW

    async def confirm(self, *, account_id: UUID, intent_id: UUID) -> PaymentReceipt:
        if self._error is not None:
            raise self._error
        self.confirmed += 1
        return RECEIPT

    async def cancel(self, *, account_id: UUID, intent_id: UUID) -> None:
        self.cancelled += 1
        if self._error is not None:
            raise self._error


class NoContacts:
    """An empty directory. These tests are about reading a whole sentence, where the recipient comes
    from the model rather than from the address book — the continuation path has its own file."""

    async def all_of(self, account_id: UUID) -> tuple:
        return ()


def preparing(reading: PaymentReading, gateway: FakeGateway | None = None):
    bank = gateway or FakeGateway()
    return PreparePaymentHandler(FakeReader(reading), bank, contacts=NoContacts()), bank


def prepare(sentence: str = SENTENCE) -> PreparePaymentCommand:
    return PreparePaymentCommand(
        sentence=sentence, account_id=ACCOUNT, idempotency_key=str(uuid4())
    )


def confirming(gateway: FakeGateway | None = None):
    bank = gateway or FakeGateway()
    return ConfirmPaymentHandler(bank), bank


def confirm(reply: str) -> ConfirmPaymentCommand:
    return ConfirmPaymentCommand(reply=reply, account_id=ACCOUNT, intent_id=INTENT)


async def test_a_payment_is_previewed_and_nothing_moves() -> None:
    handler, gateway = preparing(PaymentReading.read(REQUEST))

    answer = await handler.handle(prepare())

    assert answer.outcome == PaymentOutcome.AWAITING_CONFIRMATION.value
    assert gateway.prepared == 1
    assert gateway.confirmed == 0
    assert answer.intent_id == INTENT


async def test_an_explicit_concept_is_read_from_user_text_not_model_drift() -> None:
    misread = PaymentRequest(
        recipient_name="Marta", amount_minor=2_000, currency="EUR", concept="viaje"
    )
    handler, gateway = preparing(PaymentReading.read(misread))

    await handler.handle(prepare("envía 20 euros a Marta con el concepto viaje a Málaga por bizum"))

    assert gateway.requests[0].concept == "viaje a Málaga"


async def test_the_preview_says_who_how_much_and_what_it_leaves() -> None:
    """All three, in the sentence the user reads. A confirmation prompt that omits the balance asks
    somebody to approve a payment without telling them what it leaves."""
    handler, _ = preparing(PaymentReading.read(REQUEST))

    answer = await handler.handle(prepare())

    assert "Marta Solis Vega" in answer.text
    assert "20,00" in answer.text
    assert "10.350,42" in answer.text


@pytest.mark.parametrize(
    ("reading", "outcome"),
    [
        (PaymentReading.needs_clarification("¿De cuánto?"), PaymentOutcome.NEEDS_CLARIFICATION),
        (PaymentReading.unsupported("not a payment"), PaymentOutcome.UNSUPPORTED),
        (PaymentReading.invalid_domain("AMOUNT_INVENTED"), PaymentOutcome.NOT_UNDERSTOOD),
        (PaymentReading.invalid_structure("not JSON"), PaymentOutcome.NOT_UNDERSTOOD),
        (PaymentReading.provider_error("connection refused"), PaymentOutcome.PROVIDER_ERROR),
    ],
)
async def test_a_reading_that_is_not_a_payment_never_reaches_the_bank(
    reading: PaymentReading, outcome: PaymentOutcome
) -> None:
    handler, gateway = preparing(reading)

    answer = await handler.handle(prepare())

    assert answer.outcome == outcome.value
    assert gateway.prepared == 0
    assert answer.intent_id is None


async def test_a_question_from_the_reader_is_asked_verbatim() -> None:
    handler, _ = preparing(PaymentReading.needs_clarification("¿De cuánto a Marta?"))

    assert (await handler.handle(prepare())).text == "¿De cuánto a Marta?"


async def test_two_people_with_the_same_name_becomes_a_question_naming_both() -> None:
    """The one refusal the user can answer, and they cannot answer it without the names. Choosing
    between two Martas would be the assistant deciding who gets the money."""
    error = AmbiguousRecipientError("Marta", ("Marta Ruiz Gomez", "Marta Solis Vega"))
    handler, gateway = preparing(PaymentReading.read(REQUEST), FakeGateway(error))

    answer = await handler.handle(prepare())

    assert answer.outcome == PaymentOutcome.NEEDS_CLARIFICATION.value
    assert "Marta Ruiz Gomez" in answer.text
    assert "Marta Solis Vega" in answer.text
    assert gateway.confirmed == 0


async def test_somebody_who_is_not_a_contact_is_said_to_not_be_one() -> None:
    handler, _ = preparing(
        PaymentReading.read(REQUEST), FakeGateway(RecipientNotFoundError("Pepe"))
    )

    answer = await handler.handle(prepare())

    assert answer.outcome == PaymentOutcome.NEEDS_CLARIFICATION.value
    assert "Pepe" in answer.text
    assert answer.draft is not None
    assert answer.draft.recipient_name is None
    assert answer.draft.amount_minor == 2_000


async def test_an_unknown_recipient_discards_only_the_name() -> None:
    request = PaymentRequest(
        recipient_name="Custo",
        amount_minor=250_000,
        currency="EUR",
        concept="pa la rule",
        rail=Rail.TRANSFER,
    )
    handler, _ = preparing(
        PaymentReading.read(request), FakeGateway(RecipientNotFoundError("Custo"))
    )

    answer = await handler.handle(prepare("transfiere 2500 euros a Custo concepto pa la rule"))

    assert answer.outcome == PaymentOutcome.NEEDS_CLARIFICATION.value
    assert answer.draft is not None
    assert answer.draft.recipient_name is None
    assert answer.draft.amount_minor == 250_000
    assert answer.draft.concept == "pa la rule"
    assert answer.draft.rail == Rail.TRANSFER.value
    assert "2.500,00" in answer.text


async def test_the_bank_being_down_is_said_plainly() -> None:
    handler, _ = preparing(PaymentReading.read(REQUEST), FakeGateway(BankingUnavailableError("no")))

    assert (await handler.handle(prepare())).outcome == PaymentOutcome.BANKING_UNAVAILABLE.value


async def test_an_explicit_yes_pays_once() -> None:
    handler, gateway = confirming()

    answer = await handler.handle(confirm("sí"))

    assert answer.outcome == PaymentOutcome.PAID.value
    assert gateway.confirmed == 1
    assert answer.transaction_id == TRANSACTION
    assert "20,00" in answer.text
    assert "Marta Solis Vega" in answer.text


async def test_a_no_cancels_and_pays_nothing() -> None:
    handler, gateway = confirming()

    answer = await handler.handle(confirm("no, cancela"))

    assert answer.outcome == PaymentOutcome.CANCELLED.value
    assert gateway.confirmed == 0
    assert gateway.cancelled == 1


async def test_cancelling_an_intent_that_is_gone_is_a_conversational_refusal() -> None:
    handler, gateway = confirming(FakeGateway(IntentGoneError("intent_not_confirmable")))

    answer = await handler.handle(confirm("no"))

    assert answer.outcome == PaymentOutcome.REFUSED.value
    assert answer.intent_id == INTENT
    assert "movimientos" in answer.text
    assert gateway.confirmed == 0


async def test_a_bank_failure_while_cancelling_can_be_retried() -> None:
    handler, gateway = confirming(FakeGateway(BankingUnavailableError("timeout")))

    answer = await handler.handle(confirm("no"))

    assert answer.outcome == PaymentOutcome.BANKING_UNAVAILABLE.value
    assert answer.intent_id == INTENT
    assert "responder no" in answer.text.lower()
    assert gateway.confirmed == 0


@pytest.mark.parametrize(
    "reply",
    [
        "sí pero mándale 30",
        "sí, pero a Aitor",
        "¿cuánto era?",
        "vale, y también 20 a Aitor",
        "",
    ],
)
async def test_anything_that_is_not_a_plain_yes_pays_nothing(reply: str) -> None:
    """The property this whole file exists for. A reply that contains a yes and something else is
    not a confirmation of the payment on the table — it is a different payment, or a question."""
    handler, gateway = confirming()

    answer = await handler.handle(confirm(reply))

    assert answer.outcome == PaymentOutcome.AWAITING_CONFIRMATION.value
    assert gateway.confirmed == 0
    assert gateway.cancelled == 0


async def test_an_unclear_reply_asks_again_rather_than_closing_the_payment() -> None:
    handler, _ = confirming()

    answer = await handler.handle(confirm("¿cuánto era?"))

    assert "sí" in answer.text.lower()


async def test_a_balance_that_no_longer_covers_it_is_a_refusal_not_a_crash() -> None:
    handler, _ = confirming(FakeGateway(PaymentRefusedError("insufficient_funds")))

    answer = await handler.handle(confirm("sí"))

    assert answer.outcome == PaymentOutcome.REFUSED.value
    assert "saldo" in answer.text.lower()


async def test_an_intent_that_is_gone_says_to_check_movements() -> None:
    """Expired, cancelled, or already paid. All three mean the same thing to the user: this
    preview is no longer a live offer."""
    handler, _ = confirming(FakeGateway(IntentGoneError("intent_expired")))

    answer = await handler.handle(confirm("sí"))

    assert answer.outcome == PaymentOutcome.REFUSED.value
    assert answer.transaction_id is None
    assert "movimientos" in answer.text


async def test_a_bank_failure_during_confirmation_does_not_claim_nothing_happened() -> None:
    handler, _ = confirming(FakeGateway(BankingUnavailableError("timeout")))

    answer = await handler.handle(confirm("sí"))

    assert answer.outcome == PaymentOutcome.BANKING_UNAVAILABLE.value
    assert answer.intent_id == INTENT
    assert "no he enviado nada" not in answer.text.lower()
    assert "reintentar" in answer.text.lower()
