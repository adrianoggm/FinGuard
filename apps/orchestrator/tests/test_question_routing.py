"""One chat box, two paths.

The demo is a single field somebody types into, so `/v1/questions` has to be able to receive "hazle
un bizum de 20 euros a Marta" as well as "¿cuánto gasté en junio?" — a client cannot decide which
endpoint to use, because deciding is exactly what `payment_router` is for.

The routing is deterministic and it happens in the controller, which is the layer that knows about
credentials. Three things have to be true, and they are what this file checks:

    a payment sentence, with the scope        reaches the payment path and comes back with a preview
    a payment sentence, without the scope     comes back as the capability refusal, not a 403
    anything else                             goes to the question path, untouched

The second is the one worth arguing about. A 403 would be technically correct and useless: the user
did not choose their credential, and "no puedo hacer pagos" is the honest answer to somebody whose
assistant cannot make them.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from finguard_orchestrator.application.bus.command_bus import CommandBus
from finguard_orchestrator.application.commands.pay import (
    PaymentAnswer,
    PreparePaymentCommand,
)
from finguard_orchestrator.bootstrap.dependencies import authenticated_caller, get_command_bus
from finguard_orchestrator.bootstrap.main import app
from finguard_orchestrator.domain.enums import PaymentOutcome
from finguard_orchestrator.domain.value_objects.caller import Caller
from finguard_orchestrator.domain.value_objects.scope import Scope

ACCOUNT = uuid.UUID("11111111-1111-1111-1111-111111111111")
INTENT = uuid.UUID("33333333-3333-3333-3333-333333333333")
PAYMENT = "hazle un bizum de 20 euros a Marta"

PREVIEW = PaymentAnswer(
    outcome=PaymentOutcome.AWAITING_CONFIRMATION.value,
    text="Vas a enviar 20,00€ a Marta Solis Vega. ¿Confirmas?",
    intent_id=INTENT,
    recipient="Marta Solis Vega",
    amount_minor=2_000,
    currency="EUR",
    balance_after_minor=1_035_042,
)


class FakePreparing:
    def __init__(self) -> None:
        self.sentences: list[str] = []

    async def handle(self, command: PreparePaymentCommand) -> PaymentAnswer:
        self.sentences.append(command.sentence)
        return PREVIEW


def client_for(*scopes: str, payments: bool = True) -> tuple[TestClient, FakePreparing]:
    preparing = FakePreparing()
    bus = CommandBus()
    bus.register(PreparePaymentCommand, preparing)
    app.dependency_overrides[get_command_bus] = lambda: bus
    app.dependency_overrides[authenticated_caller] = lambda: Caller(
        holder_reference="ES-0001", account_id=ACCOUNT, scopes=frozenset(scopes)
    )
    app.state.payments_enabled = payments
    app.state.questions_enabled = True
    return TestClient(app), preparing


@pytest.fixture(autouse=True)
def _restore() -> None:
    app.dependency_overrides.clear()
    enabled = app.state.payments_enabled
    yield
    app.dependency_overrides.clear()
    app.state.payments_enabled = enabled


def test_a_payment_typed_into_the_chat_reaches_the_payment_path() -> None:
    client, preparing = client_for(Scope.QUESTIONS_READ, Scope.PAYMENTS_PREPARE)

    response = client.post("/v1/questions", json={"question": PAYMENT})

    assert response.status_code == 200
    assert response.json()["outcome"] == PaymentOutcome.AWAITING_CONFIRMATION.value
    assert response.json()["intent_id"] == str(INTENT)
    assert preparing.sentences == [PAYMENT]


def test_the_preview_comes_back_as_the_text_to_show() -> None:
    client, _ = client_for(Scope.QUESTIONS_READ, Scope.PAYMENTS_PREPARE)

    body = client.post("/v1/questions", json={"question": PAYMENT}).json()

    assert "Marta Solis Vega" in body["text"]
    assert "20,00" in body["text"]


def test_without_the_payment_scope_it_is_a_refusal_and_not_a_403() -> None:
    """The user did not choose their credential. "No puedo hacer pagos" is the honest answer; a 403
    is a fact about our configuration that they can do nothing with."""
    client, preparing = client_for(Scope.QUESTIONS_READ)

    response = client.post("/v1/questions", json={"question": PAYMENT})

    assert response.status_code == 200
    assert response.json()["outcome"] == "UNSUPPORTED"
    assert preparing.sentences == []


def test_with_payments_switched_off_it_is_the_same_refusal() -> None:
    """A deployment that has not turned payments on says it cannot make them, rather than 503 —
    which would read as "try again later" for something that is not coming back."""
    client, preparing = client_for(Scope.QUESTIONS_READ, Scope.PAYMENTS_PREPARE, payments=False)

    response = client.post("/v1/questions", json={"question": PAYMENT})

    assert response.status_code == 200
    assert response.json()["outcome"] == "UNSUPPORTED"
    assert preparing.sentences == []


@pytest.mark.parametrize(
    "question",
    [
        "¿cuánto gasté en gasolina en junio?",
        "¿cuánto le he mandado a Marta este año?",
        "le mandé 20 euros a Marta la semana pasada",
    ],
)
def test_a_question_is_never_routed_to_the_payment_path(question: str) -> None:
    """Including the ones that name money and a person. The past tense is the costly direction:
    somebody asking what they sent must not be shown a prompt to send it again."""
    client, preparing = client_for(Scope.QUESTIONS_READ, Scope.PAYMENTS_PREPARE)

    client.post("/v1/questions", json={"question": question})

    assert preparing.sentences == []
