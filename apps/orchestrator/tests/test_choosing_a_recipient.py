"""The chosen contact, from the tap to the bank.

Three turns of the reported conversation were the same question:

    "pues 15 euros con concepto de comida a Marta" → "Tienes varios contactos… ¿A quién?"
    "Marta Vega"                                   → "Tienes varios contactos… ¿A quién?"

The reply resolved the ambiguity for any reader and not for `contacts_in`, because "Marta Vega"
part-matched both Martas. Scoring the most specific match fixed that sentence; it cannot fix the
shape, because a *name* is what is ambiguous. Two contacts called Marta Solis Vega have no spelling
that tells them apart, and the user has already answered the question by pointing at one of them.

So the answer travels as an identifier. The candidates go out with `contact_id`, the client sends
back the one that was tapped, and this path never matches a name again — it looks the contact up in
the account's own directory, which is also what makes a made-up identifier a refusal rather than a
payment.
"""

import json
import uuid
from dataclasses import replace

import httpx2 as httpx
import pytest

from finguard_orchestrator.application.commands.pay import (
    PreparePaymentCommand,
    PreparePaymentHandler,
)
from finguard_orchestrator.domain.enums import PaymentOutcome
from finguard_orchestrator.domain.models.contact import Contact
from finguard_orchestrator.domain.models.conversation import (
    Conversation,
    PaymentInProgress,
    Slot,
)
from finguard_orchestrator.domain.models.payment_result import PaymentPreview
from finguard_orchestrator.domain.value_objects.payment_draft import PaymentDraft, PaymentField
from finguard_orchestrator.domain.value_objects.payment_request import PaymentRequest
from finguard_orchestrator.infrastructure.banking.http_payments import HttpPayments
from finguard_orchestrator.infrastructure.dto.llm_dto import ConversationPayload
from finguard_orchestrator.infrastructure.mappers import conversation_mapper

PREVIEW_BODY = {
    "intentId": "44444444-4444-4444-4444-444444444444",
    "recipient": "Marta Solis Vega",
    "recipientPhone": "+34600333444",
    "amountMinor": 1_500,
    "currency": "EUR",
    "balanceBeforeMinor": 1_037_042,
    "balanceAfterMinor": 1_035_542,
    "expiresAt": "2026-08-04T12:00:00Z",
}

ACCOUNT = uuid.UUID("11111111-1111-1111-1111-111111111111")
RUIZ = Contact(
    uuid.UUID("22222222-2222-2222-2222-222222222222"), "Marta Ruiz Gomez", "+34600111222"
)
VEGA = Contact(
    uuid.UUID("33333333-3333-3333-3333-333333333333"), "Marta Solis Vega", "+34600333444"
)

PREVIEW = PaymentPreview(
    intent_id=uuid.UUID("44444444-4444-4444-4444-444444444444"),
    recipient="Marta Solis Vega",
    recipient_phone="+34600333444",
    amount_minor=1_500,
    currency="EUR",
    balance_before_minor=1_037_042,
    balance_after_minor=1_035_542,
    expires_at=__import__("datetime").datetime(2026, 8, 4, 12, 0),
)


class FakeBank:
    def __init__(self) -> None:
        self.requested: PaymentRequest | None = None

    async def prepare(self, request: PaymentRequest, *, account_id: uuid.UUID, key: str):
        self.requested = request
        return PREVIEW


class FakeContacts:
    def __init__(self, *rows: Contact) -> None:
        self._rows = rows

    async def all_of(self, account_id: uuid.UUID) -> tuple[Contact, ...]:
        return self._rows


class NeverRead:
    async def read(self, sentence: str, *, policy=None):
        raise AssertionError(f"the model was consulted about {sentence!r}")


def handler(bank: FakeBank, *contacts: Contact) -> PreparePaymentHandler:
    return PreparePaymentHandler(NeverRead(), bank, contacts=FakeContacts(*contacts))


# --- the draft ------------------------------------------------------------------------


def test_a_chosen_contact_is_a_recipient() -> None:
    """The slot is filled by the choice, not by the name that came with it. Otherwise the assistant
    would ask "¿a quién?" while holding the answer."""
    draft = PaymentDraft(recipient_contact_id=VEGA.contact_id, amount_minor=1_500)

    assert PaymentField.RECIPIENT not in draft.missing


def test_the_latest_choice_wins() -> None:
    """Somebody who picks again has changed their mind, and a merge keeping the older identifier
    would pay the person they just replaced."""
    first = PaymentDraft(recipient_contact_id=RUIZ.contact_id, amount_minor=1_500)

    corrected = first.filled_with(PaymentDraft(recipient_contact_id=VEGA.contact_id))

    assert corrected.recipient_contact_id == VEGA.contact_id


def test_a_name_typed_after_a_choice_replaces_it() -> None:
    """ "No, mejor a Aitor" names somebody by name, and leaving the old identifier in place would
    send the money to whoever was tapped before it."""
    chosen = PaymentDraft(recipient_contact_id=VEGA.contact_id, amount_minor=1_500)

    changed = chosen.filled_with(PaymentDraft(recipient_name="Aitor"))

    assert changed.recipient_contact_id is None
    assert changed.recipient_name == "Aitor"


# --- the handler ----------------------------------------------------------------------


async def test_a_tapped_contact_is_paid_without_matching_a_name() -> None:
    """The end of the loop. The identifier decides who gets paid, so the sentence beside it — which
    is the display name the client echoed — never reaches `contacts_in`."""
    bank = FakeBank()
    pending = PaymentDraft(
        recipient_contact_id=VEGA.contact_id, amount_minor=1_500, concept="comida"
    )

    answer = await handler(bank, RUIZ, VEGA).handle(
        PreparePaymentCommand(
            sentence="Marta Solis Vega",
            account_id=ACCOUNT,
            idempotency_key="k",
            pending=pending,
        )
    )

    assert answer.outcome == PaymentOutcome.AWAITING_CONFIRMATION.value
    assert bank.requested is not None
    assert bank.requested.recipient_contact_id == VEGA.contact_id
    assert bank.requested.concept == "comida", "the note did not survive the choice"


async def test_the_two_martas_are_not_ambiguous_once_one_is_chosen() -> None:
    """Both are still in the directory and both still part-match the sentence. The identifier is
    what makes this turn unambiguous, which no amount of name scoring could."""
    bank = FakeBank()
    twins = replace(VEGA, display_name="Marta Solis Vega")

    answer = await handler(bank, twins, VEGA).handle(
        PreparePaymentCommand(
            sentence="Marta Solis Vega",
            account_id=ACCOUNT,
            idempotency_key="k",
            pending=PaymentDraft(recipient_contact_id=VEGA.contact_id, amount_minor=1_500),
        )
    )

    assert answer.outcome == PaymentOutcome.AWAITING_CONFIRMATION.value


async def test_an_identifier_that_is_not_in_the_directory_is_refused() -> None:
    """It arrived from a client, so it is input. Banking checks it too — that is the boundary that
    matters — and refusing here means the answer is a sentence rather than a round trip."""
    bank = FakeBank()

    answer = await handler(bank, RUIZ).handle(
        PreparePaymentCommand(
            sentence="quien sea",
            account_id=ACCOUNT,
            idempotency_key="k",
            pending=PaymentDraft(recipient_contact_id=VEGA.contact_id, amount_minor=1_500),
        )
    )

    assert answer.outcome == PaymentOutcome.REFUSED.value
    assert bank.requested is None, "an unknown contact reached the bank"


async def test_a_choice_without_an_amount_still_asks_for_one() -> None:
    """Choosing who does not say how much. The question is the same one a typed name gets."""
    bank = FakeBank()

    answer = await handler(bank, RUIZ, VEGA).handle(
        PreparePaymentCommand(
            sentence="Marta Solis Vega",
            account_id=ACCOUNT,
            idempotency_key="k",
            pending=PaymentDraft(recipient_contact_id=VEGA.contact_id),
        )
    )

    assert answer.outcome == PaymentOutcome.NEEDS_CLARIFICATION.value
    assert "cuánto" in answer.text
    assert answer.draft is not None
    assert answer.draft.recipient_contact_id == str(VEGA.contact_id), "the choice was forgotten"


def _sending_to(monkeypatch, handler) -> HttpPayments:
    """The gateway with its transport replaced, so the request body can be read.

    `httpx2.AsyncClient` is constructed inside `_post` rather than injected, which is right for
    production — one client per call, no shared pool to leak across requests — and means a test has
    to reach it here. The real class is captured first, or the replacement calls itself.
    """
    real = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler))
    )
    return HttpPayments(base_url="http://banking.invalid", timeout_seconds=1.0)


# --- and out to the bank --------------------------------------------------------------


async def test_the_bank_is_asked_by_identifier_and_not_by_name(
    monkeypatch: "pytest.MonkeyPatch",
) -> None:
    """The body, because this client exists instead of a generated one and the shape is therefore
    stated twice. Banking refuses a body that names its recipient both ways, so sending the name
    alongside the identifier would turn every chosen payment into a 422."""
    sent: dict[str, object] = {}

    def transport(request: httpx.Request) -> httpx.Response:
        sent.update(json.loads(request.content))
        return httpx.Response(200, json=PREVIEW_BODY)

    gateway = _sending_to(monkeypatch, transport)

    await gateway.prepare(
        PaymentRequest(
            recipient_name="Marta Solis Vega",
            recipient_contact_id=VEGA.contact_id,
            amount_minor=1_500,
            currency="EUR",
        ),
        account_id=ACCOUNT,
        key="k",
    )

    assert sent["recipientContactId"] == str(VEGA.contact_id)
    assert "recipientName" not in sent, "banking refuses a body that names the recipient twice"


async def test_a_payment_nobody_chose_is_still_asked_by_name(
    monkeypatch: "pytest.MonkeyPatch",
) -> None:
    """Most payments. The identifier is for the case a name cannot answer, not instead of one."""
    sent: dict[str, object] = {}

    def transport(request: httpx.Request) -> httpx.Response:
        sent.update(json.loads(request.content))
        return httpx.Response(200, json=PREVIEW_BODY)

    gateway = _sending_to(monkeypatch, transport)

    await gateway.prepare(
        PaymentRequest(recipient_name="Marta", amount_minor=1_500, currency="EUR"),
        account_id=ACCOUNT,
        key="k",
    )

    assert sent["recipientName"] == "Marta"
    assert "recipientContactId" not in sent


# --- and back, through the wire -------------------------------------------------------


def test_the_choice_survives_the_round_trip() -> None:
    """The client echoes the state without reading it, so a field this drops is a choice the next
    turn does not have — and the turn after a choice is the one that pays."""
    out = Conversation().with_active(
        PaymentInProgress(
            draft=PaymentDraft(recipient_contact_id=VEGA.contact_id, amount_minor=1_500),
            awaiting=Slot.RECIPIENT_CHOICE,
        )
    )

    back = conversation_mapper.to_domain(
        ConversationPayload.model_validate(conversation_mapper.to_payload(out).model_dump())
    )

    assert back == out
