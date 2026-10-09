"""The second rail, from the sentence to the wire.

A Bizum names a person and the bank finds the account. A transfer names the account, and the whole
of this file is what follows from that in the orchestrator: which rail a sentence asks for, that
the rail survives the turns it takes to build a payment, and what happens when the person the
holder named has no account saved.

**The rail is never the model's to choose.** "Bizum" and "transferencia" are a closed vocabulary of
two, so the reader reads who and how much and the rail is stamped from the sentence — the payment
prompt and its schema do not mention rails at all. A decoder choosing between them would be a
decoder choosing how money leaves an account.
"""

import uuid
from dataclasses import replace

import pytest

from finguard_orchestrator.application.commands.pay import (
    PaymentAnswer,
    PreparePaymentCommand,
    PreparePaymentHandler,
)
from finguard_orchestrator.domain.enums import PaymentOutcome, Rail
from finguard_orchestrator.domain.errors import InvalidValueError, RecipientHasNoAccountError
from finguard_orchestrator.domain.models.contact import Contact
from finguard_orchestrator.domain.models.payment_reading import PaymentReading
from finguard_orchestrator.domain.services import payment_words
from finguard_orchestrator.domain.value_objects.payment_draft import PaymentDraft
from finguard_orchestrator.domain.value_objects.payment_request import PaymentRequest
from finguard_orchestrator.infrastructure.banking.http_payments import refusal_for

ACCOUNT = uuid.UUID("11111111-1111-1111-1111-111111111111")
ELENA = Contact(uuid.UUID(int=7), "Elena Marin", "+34600999888")


# --- which rail a sentence asks for ---------------------------------------------------


@pytest.mark.parametrize(
    "sentence",
    [
        "hazme una transferencia de 200 euros a Elena",
        "transfiere 200 euros a Elena",
        "transfiérele 200 a Elena",
        "mándale 20 por banco a Elena",
    ],
)
def test_a_sentence_that_names_a_transfer_is_a_transfer(sentence: str) -> None:
    assert payment_words.rail_in(sentence) is Rail.TRANSFER


@pytest.mark.parametrize(
    "sentence",
    [
        "hazle un bizum de 15 euros a Marta",
        "mándale 15 euros a Marta",
        # The turn that answers "¿de cuánto?". It names no rail, and reading its silence as an
        # answer is how a transfer would quietly become a Bizum.
        "200 euros",
    ],
)
def test_anything_else_is_a_bizum(sentence: str) -> None:
    assert payment_words.rail_in(sentence) is Rail.BIZUM


# --- and survives the turns it takes to build a payment --------------------------------


def test_a_rail_survives_a_turn_that_does_not_mention_one() -> None:
    """ "Hazme una transferencia a Elena" then "200 euros". The second sentence is about the amount
    and says nothing about how the money leaves; merging its default over the draft's rail would
    turn the transfer into a Bizum between two turns."""
    started = PaymentDraft(recipient_name="Elena Marin", rail=Rail.TRANSFER)

    continued = started.filled_with(PaymentDraft(amount_minor=20_000))

    assert continued.rail is Rail.TRANSFER
    request = continued.into_request()
    assert request is not None
    assert request.rail is Rail.TRANSFER


def test_naming_a_rail_later_still_changes_it() -> None:
    """The other direction. Silence is not an answer; naming one is."""
    started = PaymentDraft(recipient_name="Elena Marin", rail=Rail.BIZUM)

    assert started.filled_with(PaymentDraft(rail=Rail.TRANSFER)).rail is Rail.TRANSFER


def test_typed_iban_is_only_a_transfer_destination() -> None:
    request = PaymentRequest(
        recipient_name="Destinatario indicado",
        amount_minor=2_000,
        currency="EUR",
        rail=Rail.TRANSFER,
        recipient_iban="ES9121000418450200051332",
    )
    assert request.recipient_iban == "ES9121000418450200051332"

    with pytest.raises(InvalidValueError):
        PaymentRequest(
            recipient_name="Destinatario indicado",
            amount_minor=2_000,
            currency="EUR",
            rail=Rail.BIZUM,
            recipient_iban="ES9121000418450200051332",
        )

    with pytest.raises(InvalidValueError):
        PaymentRequest(
            recipient_name="Destinatario indicado",
            amount_minor=2_000,
            currency="EUR",
            rail=Rail.TRANSFER,
            recipient_iban="invalid",
        )

    assert payment_words.iban_in("IBAN ES91 2100 0418 4502 0005 1332") == (
        "ES9121000418450200051332"
    )
    assert payment_words.amount_in("ES9121000418450200051332") is None
    assert payment_words.amount_in("IBAN ES91 2100 0418 4502 0005 1333") is None
    assert payment_words.iban_in("IBAN ES91 2100 0418 4502 0005 1332 20 euros") == (
        "ES9121000418450200051332"
    )
    assert payment_words.amount_in("IBAN ES91 2100 0418 4502 0005 1332 20 euros") == 2_000


# --- a contact who cannot be transferred to --------------------------------------------


def test_banking_refusing_for_want_of_an_account_is_its_own_error() -> None:
    """Not a not-found. The contact exists and the holder chose them, so "no tengo a Elena en tus
    contactos" would be a wrong answer about somebody in their own list."""
    refusal = refusal_for(
        409, {"code": "contact_has_no_account", "detail": "no account for 'Elena Marin'"}
    )

    assert isinstance(refusal, RecipientHasNoAccountError)
    assert refusal.name == "Elena Marin"


class FakeReader:
    def __init__(self, reading: PaymentReading) -> None:
        self._reading = reading

    async def read(self, sentence: str, *, policy=None) -> PaymentReading:
        return self._reading


class RefusingBank:
    def __init__(self, error: Exception) -> None:
        self._error = error
        self.asked: PaymentRequest | None = None

    async def prepare(self, request, *, account_id, key):
        self.asked = request
        raise self._error

    async def confirm(self, *, account_id, intent_id):
        raise AssertionError("nothing to confirm")

    async def cancel(self, *, account_id, intent_id):
        raise AssertionError("nothing to cancel")


class FakeContacts:
    async def all_of(self, account_id):
        return (ELENA,)


async def prepared(sentence: str, bank: RefusingBank) -> PaymentAnswer:
    reading = PaymentReading.read(
        PaymentRequest(recipient_name="Elena Marin", amount_minor=20_000, currency="EUR")
    )
    handler = PreparePaymentHandler(FakeReader(reading), bank, contacts=FakeContacts())
    return await handler.handle(
        PreparePaymentCommand(sentence=sentence, account_id=ACCOUNT, idempotency_key="k1")
    )


async def test_the_rail_reaches_the_bank_from_the_sentence_and_not_the_model() -> None:
    """The reader answered with a request carrying the default rail, as it always does — it is not
    told about rails. The sentence is what decides, and this asserts the stamp actually happens."""
    bank = RefusingBank(RecipientHasNoAccountError("Elena Marin"))

    await prepared("hazme una transferencia de 200 euros a Elena", bank)

    assert bank.asked is not None
    assert bank.asked.rail is Rail.TRANSFER


async def test_a_missing_account_keeps_the_payment_open_instead_of_apologising() -> None:
    """Everything about the payment is settled and one number is missing. Ending here would make
    the holder retype what they just said, so the draft comes back whole and the answer names both
    what is missing and what they can do about it."""
    bank = RefusingBank(RecipientHasNoAccountError("Elena Marin"))

    answer = await prepared("hazme una transferencia de 200 euros a Elena", bank)

    assert answer.outcome == PaymentOutcome.NEEDS_CLARIFICATION.value
    assert "número de cuenta" in answer.text
    assert answer.draft is not None
    assert answer.draft.amount_minor == 20_000
    assert answer.draft.recipient_name == "Elena Marin"


async def test_a_bizum_is_unaffected_by_any_of_this() -> None:
    """The rail that already worked. Every sentence this product has handled was one."""
    bank = RefusingBank(RecipientHasNoAccountError("nobody"))

    await prepared("hazle un bizum de 200 euros a Elena", bank)

    assert bank.asked is not None
    assert bank.asked.rail is Rail.BIZUM


def test_a_request_defaults_to_the_rail_that_already_existed() -> None:
    """Constructed without one, a payment is a Bizum. Every caller written before transfers
    existed keeps meaning what it meant, here as on banking's own contract."""
    assert (
        PaymentRequest(recipient_name="Elena", amount_minor=100, currency="EUR").rail is Rail.BIZUM
    )
    assert replace(PaymentDraft(), rail=Rail.TRANSFER).rail is Rail.TRANSFER
