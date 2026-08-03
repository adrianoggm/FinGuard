"""Banking's payment contract, both directions.

`refusal_for` and the two body readers are pure, so this runs with nothing else alive. That matters
more here than on the query path: this client exists because we chose not to generate one, and the
whole cost of that choice is that the shapes are stated twice. These tests are what turns "stated
twice" into "checked twice".

The mapping under test decides whether the assistant tells somebody their payment failed. Getting
`intent_not_confirmable` wrong in the optimistic direction says "no he enviado nada" about money
that already left.
"""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from finguard_orchestrator.domain.errors import (
    AmbiguousRecipientError,
    BankingUnavailableError,
    IntentGoneError,
    PaymentRefusedError,
    RecipientNotFoundError,
)
from finguard_orchestrator.infrastructure.banking.http_payments import (
    preview_from_body,
    receipt_from_body,
    refusal_for,
)

INTENT = "33333333-3333-3333-3333-333333333333"
TRANSACTION = "44444444-4444-4444-4444-444444444444"

PREVIEW = {
    "intentId": INTENT,
    "recipient": "Marta Solis Vega",
    "recipientPhone": "+34600333444",
    "amountMinor": 2_000,
    "currency": "EUR",
    "balanceBeforeMinor": 1_037_042,
    "balanceAfterMinor": 1_035_042,
    "expiresAt": "2026-08-03T10:05:00Z",
}
RECEIPT = {
    "intentId": INTENT,
    "transactionId": TRANSACTION,
    "recipient": "Marta Solis Vega",
    "amountMinor": 2_000,
    "currency": "EUR",
    "balanceAfterMinor": 1_035_042,
    "confirmedAt": "2026-08-03T10:01:00Z",
}


# --- statuses to errors ------------------------------------------------------------


def test_a_good_answer_is_not_a_refusal() -> None:
    assert refusal_for(200, PREVIEW) is None
    assert refusal_for(204, {}) is None


def test_two_people_with_the_same_name_carries_both_names() -> None:
    """The assistant cannot ask "which Marta?" without them, so a mapping that dropped the
    candidates would turn an answerable question into a dead end."""
    refusal = refusal_for(
        409,
        {
            "code": "ambiguous_contact",
            "detail": "'Marta' matches several contacts: Marta Ruiz Gomez, Marta Solis Vega",
            "candidates": ["Marta Ruiz Gomez", "Marta Solis Vega"],
        },
    )

    assert isinstance(refusal, AmbiguousRecipientError)
    assert refusal.candidates == ("Marta Ruiz Gomez", "Marta Solis Vega")
    assert refusal.name == "Marta"


def test_somebody_who_is_not_a_contact_keeps_their_name() -> None:
    refusal = refusal_for(
        404, {"code": "contact_not_found", "detail": "no contact matching 'Pepe'"}
    )

    assert isinstance(refusal, RecipientNotFoundError)
    assert refusal.name == "Pepe"


def test_a_name_that_cannot_be_recovered_still_produces_a_sentence() -> None:
    """Banking does not return the name as a field, so it is quoted out of the message. When that
    fails the answer degrades to something sayable rather than to an empty string."""
    refusal = refusal_for(404, {"code": "contact_not_found", "detail": "not found"})

    assert isinstance(refusal, RecipientNotFoundError)
    assert refusal.name


def test_a_balance_that_does_not_cover_it_is_a_refusal() -> None:
    refusal = refusal_for(409, {"code": "insufficient_funds", "detail": "balance is 1000"})

    assert isinstance(refusal, PaymentRefusedError)
    assert refusal.reason == "insufficient_funds"


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (410, "intent_expired"),
        (409, "intent_not_confirmable"),
        (404, "intent_not_found"),
    ],
)
def test_an_intent_that_is_no_longer_live_says_so_without_guessing_why(
    status: int, code: str
) -> None:
    """Three statuses, one error. They mean the same thing to whoever is looking at the preview —
    and crucially, `intent_not_confirmable` is not read as "nothing happened": it is what a second
    confirmation of a payment that went through looks like."""
    refusal = refusal_for(status, {"code": code, "detail": "..."})

    assert isinstance(refusal, IntentGoneError)
    assert refusal.reason == code


def test_the_bank_being_broken_is_not_a_refusal() -> None:
    """A 500 is unknown, not "no". The distinction is the whole reason this is a function: a refusal
    means nothing moved, and a 500 on a confirmation means nobody knows yet."""
    assert isinstance(refusal_for(500, {"detail": "boom"}), BankingUnavailableError)
    assert isinstance(refusal_for(503, {}), BankingUnavailableError)


def test_a_status_we_do_not_recognise_is_a_refusal_and_not_a_retry() -> None:
    """A 4xx is our request being wrong. Reported as a refusal, because reporting it as the bank
    being down invites a retry of something that will fail identically."""
    refusal = refusal_for(422, {"code": "invalid_input", "detail": "amountMinor"})

    assert isinstance(refusal, PaymentRefusedError)
    assert "invalid_input" in refusal.reason


# --- bodies to models --------------------------------------------------------------


def test_a_preview_is_read_field_by_field() -> None:
    preview = preview_from_body(PREVIEW)

    assert preview.intent_id == UUID(INTENT)
    assert preview.recipient == "Marta Solis Vega"
    assert preview.recipient_phone == "+34600333444"
    assert preview.amount_minor == 2_000
    assert preview.balance_before_minor == 1_037_042
    assert preview.balance_after_minor == 1_035_042
    assert preview.expires_at == datetime(2026, 8, 3, 10, 5, tzinfo=UTC)


def test_a_receipt_points_at_the_ledger_movement() -> None:
    receipt = receipt_from_body(RECEIPT)

    assert receipt.transaction_id == UUID(TRANSACTION)
    assert receipt.confirmed_at == datetime(2026, 8, 3, 10, 1, tzinfo=UTC)
    assert receipt.balance_after_minor == 1_035_042


def test_a_timestamp_never_arrives_naive() -> None:
    """Banking writes UTC with a Z. A naive datetime here would compare wrongly against an expiry
    and would print an hour that is not the one the ledger recorded."""
    assert preview_from_body(PREVIEW).expires_at.tzinfo is not None


@pytest.mark.parametrize("missing", ["intentId", "amountMinor", "balanceAfterMinor", "expiresAt"])
def test_a_preview_missing_a_field_is_not_repaired(missing: str) -> None:
    body = {key: value for key, value in PREVIEW.items() if key != missing}

    with pytest.raises(BankingUnavailableError):
        preview_from_body(body)


def test_a_receipt_we_cannot_read_is_not_reported_as_a_payment() -> None:
    """The money moved either way. Saying so honestly beats inventing the figures on the receipt."""
    with pytest.raises(BankingUnavailableError):
        receipt_from_body({**RECEIPT, "transactionId": "not-a-uuid"})
