"""A payment built over several turns.

The conversation from the report, with what each turn produced:

    "Vale y si hacemos un bizum a alguien?"       "¿A quién quieres hacerle el bizum?"
    "No sé, ¿a quién tengo en contactos?"          the contact list
    "Vale, ¿le hacemos un bizum a Marta?"          "¿A qué comercio o servicio...?"   ← the planner
    "Quiero hacerle bizum a marta"                 "¿De cuánto quieres hacerle el bizum a Marta?"
    "15 euros"                                     "¿De qué periodo te refieres?"     ← the planner

Two different turns fell into the financial planner, and both for the same reason: nothing kept the
half-built payment, so every reply was classified from scratch. "15 euros" has money in it and no
payment verb, which is not a payment; "¿le hacemos un bizum a Marta?" has a question word, which
reads as a question about facts.

With the draft kept, neither has to be classified at all. The reply to "¿de cuánto?" is an
amount and the reply to "¿a quién?" is a name in this account's directory — both closed
vocabularies, both read without a model.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from finguard_orchestrator.application.commands.pay import (
    PreparePaymentCommand,
    PreparePaymentHandler,
)
from finguard_orchestrator.domain.enums import PaymentOutcome, PlanningOutcome, Rail
from finguard_orchestrator.domain.models.contact import Contact
from finguard_orchestrator.domain.models.payment_reading import PaymentReading
from finguard_orchestrator.domain.models.payment_result import PaymentPreview
from finguard_orchestrator.domain.value_objects.guard_policy import EVERY_RULE, GuardPolicy
from finguard_orchestrator.domain.value_objects.payment_draft import PaymentDraft
from finguard_orchestrator.domain.value_objects.payment_request import PaymentRequest

ACCOUNT = UUID("11111111-1111-1111-1111-111111111111")
INTENT = UUID("33333333-3333-3333-3333-333333333333")

MARTA_RUIZ = Contact(UUID(int=1), "Marta Ruiz Gomez", "+34600111222")
MARTA_SOLIS = Contact(UUID(int=2), "Marta Solis Vega", "+34600333444")
AITOR = Contact(UUID(int=3), "Aitor Beltran Diaz", "+34600555666")
DIRECTORY = (AITOR, MARTA_RUIZ, MARTA_SOLIS)


def preview(amount_minor: int = 1_500) -> PaymentPreview:
    return PaymentPreview(
        intent_id=INTENT,
        recipient="Marta Solis Vega",
        recipient_phone="+34600333444",
        amount_minor=amount_minor,
        currency="EUR",
        balance_before_minor=1_037_042,
        balance_after_minor=1_037_042 - amount_minor,
        expires_at=datetime(2026, 8, 4, 10, 5, tzinfo=UTC),
    )


class FakeReader:
    """Records whether it was consulted at all, which is half of what these tests assert."""

    def __init__(self, reading: PaymentReading | None = None) -> None:
        self._reading = reading
        self.sentences: list[str] = []

    async def read(self, sentence: str, *, policy: GuardPolicy = EVERY_RULE) -> PaymentReading:
        self.sentences.append(sentence)
        if self._reading is None:
            raise AssertionError(f"the model was consulted about {sentence!r}")
        return self._reading


class FakeGateway:
    def __init__(self) -> None:
        self.prepared: list[PaymentRequest] = []

    async def prepare(self, request: PaymentRequest, *, account_id: UUID, key: str):
        self.prepared.append(request)
        return preview(request.amount_minor)

    async def confirm(self, *, account_id: UUID, intent_id: UUID, key: str):
        raise AssertionError("nothing here confirms")

    async def cancel(self, *, account_id: UUID, intent_id: UUID) -> None:
        raise AssertionError("nothing here cancels")


class FakeContacts:
    async def all_of(self, account_id: UUID) -> tuple[Contact, ...]:
        return DIRECTORY


def handler(reader: FakeReader, gateway: FakeGateway) -> PreparePaymentHandler:
    return PreparePaymentHandler(reader, gateway, contacts=FakeContacts())


async def prepare(
    sentence: str, *, pending: PaymentDraft | None = None, reader: FakeReader | None = None
):
    reader = reader or FakeReader()
    gateway = FakeGateway()
    answer = await handler(reader, gateway).handle(
        PreparePaymentCommand(
            sentence=sentence,
            account_id=ACCOUNT,
            idempotency_key=str(uuid4()),
            pending=pending,
        )
    )
    return answer, reader, gateway


# --- finishing a payment ------------------------------------------------------------


async def test_the_amount_completes_a_pending_payment_without_a_model() -> None:
    """The turn that broke the reported conversation. "15 euros" is an amount and nothing else, and
    the recipient it belongs to is in the draft."""
    answer, reader, gateway = await prepare(
        "15 euros", pending=PaymentDraft(recipient_name="Marta Solis Vega")
    )

    assert reader.sentences == [], "an amount from a closed grammar went to a model"
    assert [r.amount_minor for r in gateway.prepared] == [1_500]
    assert gateway.prepared[0].recipient_name == "Marta Solis Vega"
    assert answer.outcome == PaymentOutcome.AWAITING_CONFIRMATION.value


async def test_an_elliptical_second_payment_builds_a_fresh_request_without_a_model() -> None:
    """After a successful Bizum, the router supplies only its rail in a fresh draft.

    The new sentence must provide both mutable fields again: neither Marta nor the previous
    amount may leak into this payment.
    """
    answer, reader, gateway = await prepare(
        "y ahora otro de 14 a Aitor",
        pending=PaymentDraft(rail=Rail.BIZUM),
    )

    assert reader.sentences == []
    assert len(gateway.prepared) == 1
    assert gateway.prepared[0].recipient_name == "Aitor Beltran Diaz"
    assert gateway.prepared[0].amount_minor == 1_400
    assert gateway.prepared[0].concept is None
    assert answer.outcome == PaymentOutcome.AWAITING_CONFIRMATION.value


async def test_a_revised_amount_replaces_the_previewed_one_and_keeps_the_rest() -> None:
    answer, reader, gateway = await prepare(
        "mejor 20 euros",
        pending=PaymentDraft(
            recipient_name="Marta Solis Vega",
            amount_minor=1_500,
            concept="tapas",
        ),
    )

    assert reader.sentences == []
    assert gateway.prepared[0].amount_minor == 2_000
    assert gateway.prepared[0].recipient_name == "Marta Solis Vega"
    assert gateway.prepared[0].concept == "tapas"
    assert answer.outcome == PaymentOutcome.AWAITING_CONFIRMATION.value


async def test_a_revised_recipient_is_resolved_again_and_keeps_amount_and_concept() -> None:
    answer, reader, gateway = await prepare(
        "era para Aitor Beltran",
        pending=PaymentDraft(amount_minor=1_500, concept="tapas"),
    )

    assert reader.sentences == []
    assert gateway.prepared[0].recipient_name == "Aitor Beltran Diaz"
    assert gateway.prepared[0].amount_minor == 1_500
    assert gateway.prepared[0].concept == "tapas"
    assert answer.outcome == PaymentOutcome.AWAITING_CONFIRMATION.value


async def test_a_revised_concept_replaces_only_the_note() -> None:
    answer, reader, gateway = await prepare(
        "cambia el concepto a cena del viernes",
        pending=PaymentDraft(
            recipient_name="Marta Solis Vega",
            amount_minor=1_500,
            concept="tapas",
        ),
    )

    assert reader.sentences == []
    assert gateway.prepared[0].recipient_name == "Marta Solis Vega"
    assert gateway.prepared[0].amount_minor == 1_500
    assert gateway.prepared[0].concept == "cena del viernes"
    assert "cena del viernes" in answer.text


async def test_a_concept_can_be_removed_explicitly() -> None:
    _, reader, gateway = await prepare(
        "quita el concepto",
        pending=PaymentDraft(
            recipient_name="Marta Solis Vega",
            amount_minor=1_500,
            concept="tapas",
        ),
    )

    assert reader.sentences == []
    assert gateway.prepared[0].concept is None


async def test_a_name_completes_a_payment_that_only_needed_one() -> None:
    answer, reader, gateway = await prepare(
        "Marta Solis Vega", pending=PaymentDraft(amount_minor=1_500)
    )

    assert reader.sentences == []
    assert [r.recipient_name for r in gateway.prepared] == ["Marta Solis Vega"]
    assert answer.outcome == PaymentOutcome.AWAITING_CONFIRMATION.value


async def test_a_name_that_still_leaves_the_amount_missing_asks_for_it() -> None:
    """And carries the draft, or the next turn is a fresh sentence again."""
    answer, reader, gateway = await prepare("a Marta Solis Vega", pending=PaymentDraft())

    assert reader.sentences == []
    assert gateway.prepared == []
    assert answer.outcome == PaymentOutcome.NEEDS_CLARIFICATION.value
    assert answer.draft is not None
    assert answer.draft.recipient_name == "Marta Solis Vega"
    assert "cuánto" in answer.text.lower()


async def test_an_ambiguous_name_is_offered_as_a_choice() -> None:
    """ "Marta" names two people in this directory. Banking refuses to choose and so does this: the
    names come back so the user can pick, and the amount already given is not lost."""
    answer, reader, gateway = await prepare(
        "le hacemos un bizum a Marta", pending=PaymentDraft(amount_minor=1_500)
    )

    assert reader.sentences == []
    assert gateway.prepared == [], "an ambiguous recipient never reaches the bank"
    assert answer.outcome == PaymentOutcome.NEEDS_CLARIFICATION.value
    assert [c.display_name for c in answer.candidates] == ["Marta Ruiz Gomez", "Marta Solis Vega"]
    assert answer.draft is not None
    assert answer.draft.amount_minor == 1_500, "the amount survived the ambiguity"


async def test_the_choice_after_an_ambiguity_finishes_it() -> None:
    answer, reader, gateway = await prepare(
        "Marta Solis Vega", pending=PaymentDraft(amount_minor=1_500)
    )

    assert gateway.prepared[0].recipient_name == "Marta Solis Vega"
    assert answer.outcome == PaymentOutcome.AWAITING_CONFIRMATION.value


async def test_a_reply_that_answers_nothing_goes_to_the_model() -> None:
    """ "No sé" fills no slot. The draft cannot advance from it, so the sentence takes the ordinary
    path — which is where a sentence nobody can read deterministically belongs."""
    reader = FakeReader(PaymentReading.needs_clarification("¿De cuánto quieres el bizum?"))

    answer, reader, _ = await prepare(
        "no sé", pending=PaymentDraft(recipient_name="Marta"), reader=reader
    )

    assert reader.sentences == ["no sé"]
    assert answer.outcome == PaymentOutcome.NEEDS_CLARIFICATION.value


# --- what the model's own clarification keeps ---------------------------------------


async def test_a_question_from_the_model_carries_what_it_had_read() -> None:
    """ "Quiero hacerle bizum a marta" read the recipient and asked for the amount, then dropped the
    recipient. This is the half of the round trip the model path owes."""
    reader = FakeReader(
        PaymentReading.needs_clarification(
            "¿De cuánto quieres hacerle el bizum a Marta?",
            draft=PaymentDraft(recipient_name="Marta"),
        )
    )

    answer, _, gateway = await prepare("Quiero hacerle bizum a marta", reader=reader)

    assert gateway.prepared == []
    assert answer.draft is not None
    assert answer.draft.recipient_name == "Marta"


async def test_a_complete_reading_still_pays_the_ordinary_way() -> None:
    """The single-sentence payment is untouched by any of this."""
    reader = FakeReader(
        PaymentReading.read(
            PaymentRequest(recipient_name="Marta", amount_minor=2_000, currency="EUR")
        )
    )

    answer, _, gateway = await prepare("hazle un bizum de 20 euros a Marta", reader=reader)

    assert [r.amount_minor for r in gateway.prepared] == [2_000]
    assert answer.outcome == PaymentOutcome.AWAITING_CONFIRMATION.value


async def test_the_concept_reaches_the_request() -> None:
    """The words the user wrote reached nothing: PaymentRequest had no concept field. Now the
    draft carries it into the payment and into the prompt they confirm."""
    answer, _, gateway = await prepare(
        "15 euros", pending=PaymentDraft(recipient_name="Marta Solis Vega", concept="tapas")
    )

    assert gateway.prepared[0].concept == "tapas"
    assert "tapas" in answer.text


async def test_an_outcome_that_is_not_a_question_carries_no_draft() -> None:
    """A refusal is the end of this payment, so echoing a draft would invite the client to keep
    answering a question nobody asked."""
    reader = FakeReader(PaymentReading.unsupported("not a payment"))

    answer, _, _ = await prepare("¿cuánto gasté?", reader=reader)

    assert answer.draft is None
    assert answer.outcome != PlanningOutcome.NEEDS_CLARIFICATION.value


async def test_the_concept_arrives_on_the_same_turn_as_the_amount() -> None:
    """ "Pues 15 euros con concepto de comida a Marta" answers the amount question and carries the
    note in the same breath. Reading one without the other lost it exactly where it was written."""
    answer, reader, gateway = await prepare(
        "pues 15 euros con concepto de comida a Marta Solis Vega", pending=PaymentDraft()
    )

    assert reader.sentences == []
    assert gateway.prepared[0].concept == "comida"
    assert "comida" in answer.text


async def test_a_place_and_instruction_suffix_are_kept_out_of_each_others_slots() -> None:
    _, reader, gateway = await prepare(
        "50 euros con concepto viaje a Málaga por bizum ahora mismo",
        pending=PaymentDraft(recipient_name="Marta Solis Vega"),
    )

    assert reader.sentences == []
    assert gateway.prepared[0].concept == "viaje a Málaga"


async def test_the_concept_survives_an_ambiguous_name() -> None:
    answer, _, gateway = await prepare(
        "pues 15 euros con concepto de comida a Marta", pending=PaymentDraft()
    )

    assert gateway.prepared == []
    assert answer.draft is not None
    assert answer.draft.concept == "comida"
    assert answer.draft.amount_minor == 1_500
