"""A resent request is one payment, not two.

The idempotency key was invented here, per request. A key that differs between a request and its
retry is the same as no key at all: a client whose request timed out and sent it again got a second
intent for one intention. No money moves — confirming is keyed on the intent — but the holder ends
up holding two previews of a payment they meant once, and only one of them can be the one they
approve. The other sits in the ledger until it expires.

Banking has replayed a key correctly since it was written. Every endpoint here that can prepare a
payment now passes the caller's, and the chat is one of them: "hazle un bizum de 20 a Marta" is a
question route that prepares.

Absent, one is still generated. The header is optional because this is the surface a browser talks
to, and a client written before it has to keep working — at the cost stated above.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from finguard_orchestrator.application.bus.command_bus import CommandBus
from finguard_orchestrator.application.bus.query_bus import QueryBus
from finguard_orchestrator.application.commands.pay import (
    PaymentAnswer,
    PrepareChosenPaymentCommand,
    PrepareOwnTransferCommand,
    PreparePaymentCommand,
    _refusal,
)
from finguard_orchestrator.application.queries.list_contacts import (
    ListContactsQuery,
    ListContactsResult,
)
from finguard_orchestrator.bootstrap.dependencies import (
    authenticated_caller,
    get_command_bus,
    get_query_bus,
)
from finguard_orchestrator.bootstrap.main import app
from finguard_orchestrator.domain.enums import PaymentOutcome
from finguard_orchestrator.domain.services import payment_writer
from finguard_orchestrator.domain.value_objects.caller import Caller
from finguard_orchestrator.domain.value_objects.scope import Scope

ACCOUNT = uuid.UUID("11111111-1111-1111-1111-111111111111")
PIGGY = uuid.UUID("22222222-2222-2222-2222-222222222222")
INTENT = uuid.UUID("33333333-3333-3333-3333-333333333333")

MINE = "attempt-7f2c"

PREVIEWED = PaymentAnswer(
    outcome=PaymentOutcome.AWAITING_CONFIRMATION.value,
    text="Vas a enviar 20,00 € a Marta Solis Vega. ¿Confirmas?",
    intent_id=INTENT,
    recipient="Marta Solis Vega",
    amount_minor=2_000,
    currency="EUR",
)


class Records:
    """Every prepare command that reached the bus, whichever route sent it."""

    def __init__(self) -> None:
        self.keys: list[str] = []

    async def handle(self, command: object) -> PaymentAnswer:
        self.keys.append(command.idempotency_key)  # type: ignore[attr-defined]
        return PREVIEWED


class NoContacts:
    async def handle(self, query: ListContactsQuery) -> ListContactsResult:
        return ListContactsResult(contacts=[])


@pytest.fixture(autouse=True)
def _restore():  # type: ignore[no-untyped-def]
    app.dependency_overrides.clear()
    enabled = app.state.payments_enabled
    yield
    app.dependency_overrides.clear()
    app.state.payments_enabled = enabled


def a_client() -> tuple[TestClient, Records]:
    preparing = Records()
    commands = CommandBus()
    for kind in (PreparePaymentCommand, PrepareChosenPaymentCommand, PrepareOwnTransferCommand):
        commands.register(kind, preparing)  # type: ignore[arg-type]
    queries = QueryBus()
    queries.register(ListContactsQuery, NoContacts())
    app.dependency_overrides[get_command_bus] = lambda: commands
    app.dependency_overrides[get_query_bus] = lambda: queries
    app.dependency_overrides[authenticated_caller] = lambda: Caller(
        holder_reference="ES-0001",
        account_id=ACCOUNT,
        scopes=frozenset({Scope.QUESTIONS_READ, Scope.PAYMENTS_PREPARE}),
    )
    app.state.payments_enabled = True
    app.state.questions_enabled = True
    return TestClient(app), preparing


SENT = [
    ("/v1/payments", {"sentence": "hazle un bizum de 20 euros a Marta Solis Vega"}),
    (
        "/v1/payments/chosen",
        {"recipient_name": "Marta Solis Vega", "amount_minor": 2_000, "currency": "EUR"},
    ),
    (
        "/v1/payments/between-accounts",
        {"to_account_id": str(PIGGY), "amount_minor": 2_000, "currency": "EUR"},
    ),
]


@pytest.mark.parametrize(("route", "body"), SENT)
def test_the_callers_key_is_the_one_the_bank_is_given(route: str, body: dict) -> None:
    client, preparing = a_client()

    response = client.post(route, json=body, headers={"Idempotency-Key": MINE})

    assert response.status_code == 200
    assert preparing.keys == [MINE]


@pytest.mark.parametrize(("route", "body"), SENT)
def test_the_same_attempt_sent_twice_is_one_key(route: str, body: dict) -> None:
    """The property, and the whole point: the bank sees one intention and answers with the intent it
    already made, rather than making a second."""
    client, preparing = a_client()

    client.post(route, json=body, headers={"Idempotency-Key": MINE})
    client.post(route, json=body, headers={"Idempotency-Key": MINE})

    assert preparing.keys == [MINE, MINE]


@pytest.mark.parametrize(("route", "body"), SENT)
def test_a_caller_that_sends_none_still_gets_one(route: str, body: dict) -> None:
    """Optional, because a client written before the header exists. What it costs is the orphan
    above, and two requests without a key are still two intentions as far as anything can tell."""
    client, preparing = a_client()

    client.post(route, json=body)
    client.post(route, json=body)

    assert len(preparing.keys) == 2
    assert all(preparing.keys)
    assert preparing.keys[0] != preparing.keys[1], "a generated key is not shared between requests"


# --- the chat, which prepares payments too ------------------------------------------------


def test_a_resent_question_that_is_a_payment_is_one_intent() -> None:
    """The route the browser types into. A sentence is classified deterministically and a payment
    order becomes a prepare, so a question resent after a timeout is exactly the case this is for.
    """
    client, preparing = a_client()
    said = {"question": "hazle un bizum de 20 euros a Marta Solis Vega"}

    client.post("/v1/questions", json=said, headers={"Idempotency-Key": MINE})
    client.post("/v1/questions", json=said, headers={"Idempotency-Key": MINE})

    assert preparing.keys == [MINE, MINE]


def test_the_streaming_route_carries_it_too() -> None:
    """The browser types into this one. A key that only reached the other route was a key the chat
    never used — the same divergence the payment routing itself had to be fixed for twice."""
    client, preparing = a_client()

    response = client.post(
        "/v1/questions/stream",
        json={"question": "hazle un bizum de 20 euros a Marta Solis Vega"},
        headers={"Idempotency-Key": MINE},
    )

    assert response.status_code == 200
    assert preparing.keys == [MINE]


# --- what the user is told when the key was already used ----------------------------------


def test_a_refused_request_does_not_claim_a_payment_already_happened() -> None:
    """Found by sending a reused key at the running stack.

    Every `PaymentRefusedError` this service does not have a name for fell through to "ese pago ya
    no está pendiente, comprueba tus movimientos" — a claim that a payment existed and was dealt
    with. Nothing had been sent, so it sent somebody looking through their movements for a payment
    that was never made: the alarming direction of wrong, about money.

    An intent that really is spent arrives as `IntentGoneError` and keeps that sentence.
    """
    answer = _refusal("HTTP 409: key_reused_for_another_payment")

    assert answer.outcome == PaymentOutcome.REFUSED.value
    assert answer.text == payment_writer.nothing_was_sent()
    assert "movimientos" not in answer.text


def test_a_balance_that_did_not_cover_it_still_says_so() -> None:
    """The one refusal with a cause worth naming: the user can act on it, and "no he enviado nada"
    alone would leave them guessing why."""
    assert _refusal("insufficient_funds").text == payment_writer.insufficient_funds()
