"""The payment endpoints: the shape of the contract, and which answer each refusal gets.

Two steps, two routes, and the second one is the only one that moves money. What matters here is
that a caller can tell the refusals apart without reading prose: an ambiguous recipient is
answerable by retrying with a fuller name, an expired intent by preparing it again, and an already
confirmed one by doing nothing at all. Those are three different actions, so they are three
different statuses.

The gateway is a fake. That the database actually pays once is `test_payments.py`; what is under
test here is the contract.
"""

import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from finguard_banking.application import (
    CancelPaymentCommand,
    CancelPaymentHandler,
    CommandBus,
    ConfirmPaymentCommand,
    ConfirmPaymentHandler,
    PreparePaymentCommand,
    PreparePaymentHandler,
)
from finguard_banking.bootstrap.dependencies import get_command_bus
from finguard_banking.bootstrap.main import app
from finguard_banking.domain.errors import (
    AmbiguousContactError,
    ContactNotFoundError,
    InsufficientFundsError,
    IntentExpiredError,
    IntentNotConfirmableError,
    IntentNotFoundError,
)
from finguard_banking.domain.models.contact import Contact
from finguard_banking.domain.models.payment import PaymentPreview, PaymentReceipt
from finguard_banking.domain.value_objects.money import Money

ACCOUNT = uuid.UUID("11111111-1111-1111-1111-111111111111")
CONTACT = uuid.UUID("22222222-2222-2222-2222-222222222222")
INTENT = uuid.UUID("33333333-3333-3333-3333-333333333333")
TRANSACTION = uuid.UUID("44444444-4444-4444-4444-444444444444")

# The account is in the path, like the balance endpoint's. A payment is an operation *on* an
# account, and a body that names its own account is a body that can name somebody else's.
PAYMENTS = f"/v1/accounts/{ACCOUNT}/payments"
BODY = {
    "recipientName": "Marta Solis",
    "amountMinor": 2_000,
    "currency": "EUR",
}
KEY = {"Idempotency-Key": "bizum-1"}

PREVIEW = PaymentPreview(
    intent_id=INTENT,
    recipient="Marta Solis Vega",
    recipient_phone="+34600333444",
    amount=Money(2_000, "EUR"),
    balance_before=Money(1_037_042, "EUR"),
    balance_after=Money(1_035_042, "EUR"),
    expires_at=datetime(2026, 8, 3, 10, 5, tzinfo=UTC),
)
RECEIPT = PaymentReceipt(
    intent_id=INTENT,
    transaction_id=TRANSACTION,
    recipient="Marta Solis Vega",
    recipient_phone="+34600333444",
    amount=Money(2_000, "EUR"),
    balance_after=Money(1_035_042, "EUR"),
    confirmed_at=datetime(2026, 8, 3, 10, 1, tzinfo=UTC),
)


class FakeContacts:
    def __init__(self, error: Exception | None = None) -> None:
        self._error = error

    async def resolve(self, account_id: uuid.UUID, name: str) -> Contact:
        if self._error is not None:
            raise self._error
        return Contact(contact_id=CONTACT, display_name="Marta Solis Vega", phone="+34600333444")


class FakeGateway:
    """Records what it was asked to do. Nothing here decides anything: the decisions under test
    belong to the routes and to the error translation."""

    def __init__(self, error: Exception | None = None) -> None:
        self._error = error
        self.prepared: dict[str, object] = {}
        self.cancelled: uuid.UUID | None = None

    async def prepare(
        self,
        *,
        account_id: uuid.UUID,
        contact_id: uuid.UUID,
        amount: Money,
        key: str,
    ) -> PaymentPreview:
        if self._error is not None:
            raise self._error
        self.prepared = {"account_id": account_id, "contact_id": contact_id, "key": key}
        return PREVIEW

    async def confirm(self, *, account_id: uuid.UUID, intent_id: uuid.UUID) -> PaymentReceipt:
        if self._error is not None:
            raise self._error
        return RECEIPT

    async def cancel(self, *, account_id: uuid.UUID, intent_id: uuid.UUID) -> None:
        if self._error is not None:
            raise self._error
        self.cancelled = intent_id


def client_for(gateway: FakeGateway, contacts: FakeContacts | None = None) -> TestClient:
    bus = CommandBus()
    bus.register(PreparePaymentCommand, PreparePaymentHandler(contacts or FakeContacts(), gateway))
    bus.register(ConfirmPaymentCommand, ConfirmPaymentHandler(gateway))
    bus.register(CancelPaymentCommand, CancelPaymentHandler(gateway))
    app.dependency_overrides[get_command_bus] = lambda: bus
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides() -> None:
    app.dependency_overrides.clear()


def test_preparing_answers_with_the_contract_names() -> None:
    response = client_for(FakeGateway()).post(PAYMENTS, json=BODY, headers=KEY)

    assert response.status_code == 200
    assert response.json() == {
        "intentId": str(INTENT),
        "recipient": "Marta Solis Vega",
        "recipientPhone": "+34600333444",
        "amountMinor": 2_000,
        "currency": "EUR",
        "balanceBeforeMinor": 1_037_042,
        "balanceAfterMinor": 1_035_042,
        "expiresAt": "2026-08-03T10:05:00Z",
    }


def test_the_idempotency_key_travels_from_the_header_to_the_gateway() -> None:
    """It arrives in a header because that is where HTTP puts it, and it has to reach the
    database unchanged: a key the transport invented would make every retry a new payment."""
    gateway = FakeGateway()

    client_for(gateway).post(PAYMENTS, json=BODY, headers={"Idempotency-Key": "abc-123"})

    assert gateway.prepared["key"] == "abc-123"


def test_preparing_without_an_idempotency_key_is_refused() -> None:
    """Required, not generated on the caller's behalf. A key we invent per request is a key that
    differs between a request and its retry, which is the same as having none."""
    response = client_for(FakeGateway()).post(PAYMENTS, json=BODY)

    assert response.status_code == 422


def test_confirming_answers_with_the_receipt() -> None:
    response = client_for(FakeGateway()).post(f"{PAYMENTS}/{INTENT}/confirm")

    assert response.status_code == 200
    assert response.json() == {
        "intentId": str(INTENT),
        "transactionId": str(TRANSACTION),
        "recipient": "Marta Solis Vega",
        "recipientPhone": "+34600333444",
        "amountMinor": 2_000,
        "currency": "EUR",
        "balanceAfterMinor": 1_035_042,
        "confirmedAt": "2026-08-03T10:01:00Z",
    }


def test_cancelling_answers_that_nothing_happened() -> None:
    gateway = FakeGateway()

    response = client_for(gateway).post(f"{PAYMENTS}/{INTENT}/cancel")

    assert response.status_code == 204
    assert gateway.cancelled == INTENT


def test_an_ambiguous_recipient_carries_the_candidates() -> None:
    """The one refusal the user can answer. Without the names in the body the client has to ask
    "which Marta?" without being able to say which Martas, so they travel as a field rather than
    inside the message text."""
    error = AmbiguousContactError("Marta", ("Marta Ruiz Gomez", "Marta Solis Vega"))

    response = client_for(FakeGateway(), FakeContacts(error)).post(PAYMENTS, json=BODY, headers=KEY)

    assert response.status_code == 409
    assert response.json()["code"] == "ambiguous_contact"
    assert response.json()["candidates"] == ["Marta Ruiz Gomez", "Marta Solis Vega"]


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (ContactNotFoundError("Pepe"), 404, "contact_not_found"),
        (InsufficientFundsError(1_000, 2_000), 409, "insufficient_funds"),
    ],
)
def test_a_refused_preparation_says_which_refusal_it_was(
    error: Exception, status: int, code: str
) -> None:
    contacts = FakeContacts(error if isinstance(error, ContactNotFoundError) else None)
    gateway = FakeGateway(None if isinstance(error, ContactNotFoundError) else error)

    response = client_for(gateway, contacts).post(PAYMENTS, json=BODY, headers=KEY)

    assert response.status_code == status
    assert response.json()["code"] == code


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (IntentNotFoundError(INTENT), 404, "intent_not_found"),
        # 410 rather than 409: the intent is gone and preparing it again is the way forward. A
        # client that retries the same confirmation will never succeed, and the status says so.
        (IntentExpiredError(INTENT), 410, "intent_expired"),
        # 409: the state is what makes it unanswerable, and the money already moved. This is the
        # status a retried confirmation lands on, which is why it must not read as an error the
        # caller should fix.
        (IntentNotConfirmableError(INTENT, "CONFIRMED"), 409, "intent_not_confirmable"),
        (InsufficientFundsError(1_000, 2_000), 409, "insufficient_funds"),
    ],
)
def test_a_refused_confirmation_says_which_refusal_it_was(
    error: Exception, status: int, code: str
) -> None:
    response = client_for(FakeGateway(error)).post(f"{PAYMENTS}/{INTENT}/confirm")

    assert response.status_code == status
    assert response.json()["code"] == code


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("amountMinor", 0),
        ("amountMinor", -2_000),
        ("currency", "EURO"),
        ("recipientName", ""),
        ("currency", "eur"),
    ],
)
def test_a_payment_outside_the_contract_is_rejected(field: str, value: object) -> None:
    response = client_for(FakeGateway()).post(PAYMENTS, json={**BODY, field: value}, headers=KEY)

    assert response.status_code == 422


@pytest.mark.parametrize("smuggled", ["contactId", "accountId", "intentId"])
def test_the_body_cannot_name_who_it_pays_or_who_pays(smuggled: str) -> None:
    """`recipientName` is resolved against the caller's own contacts and the account comes from
    the path. Accepting an identifier here would let a caller pay somebody who is not in their
    list, or pay out of an account that is not the one they addressed."""
    response = client_for(FakeGateway()).post(
        PAYMENTS, json={**BODY, smuggled: str(CONTACT)}, headers=KEY
    )

    assert response.status_code == 422
