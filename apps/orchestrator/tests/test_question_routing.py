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

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from finguard_orchestrator.application.bus.command_bus import CommandBus
from finguard_orchestrator.application.commands.pay import (
    ConfirmPaymentCommand,
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
    text="Vas a enviar 20,00 € a Marta Solis Vega. ¿Confirmas?",
    intent_id=INTENT,
    recipient="Marta Solis Vega",
    amount_minor=2_000,
    currency="EUR",
    balance_after_minor=1_035_042,
)


class FakePreparing:
    def __init__(self) -> None:
        self.sentences: list[str] = []
        self.confirmations: list[str] = []
        self.commands: list[PreparePaymentCommand] = []

    async def handle(self, command: PreparePaymentCommand) -> PaymentAnswer:
        self.commands.append(command)
        self.sentences.append(command.sentence)
        return PREVIEW


class FakeConfirming:
    def __init__(self, record: FakePreparing) -> None:
        self._record = record

    async def handle(self, command: ConfirmPaymentCommand) -> PaymentAnswer:
        self._record.confirmations.append(command.reply)
        if command.reply == "sí":
            return PaymentAnswer(
                outcome=PaymentOutcome.PAID.value,
                text="Hecho.",
                intent_id=INTENT,
                currency="EUR",
            )
        if command.reply == "no":
            return PaymentAnswer(
                outcome=PaymentOutcome.CANCELLED.value,
                text="No he enviado nada.",
                intent_id=INTENT,
            )
        if command.reply == "reintenta":
            return PaymentAnswer(
                outcome=PaymentOutcome.BANKING_UNAVAILABLE.value,
                text="No he podido comprobar el pago; reintenta.",
                intent_id=INTENT,
            )
        return PaymentAnswer(
            outcome=PaymentOutcome.AWAITING_CONFIRMATION.value,
            text="Responde sí o no.",
            intent_id=INTENT,
        )


def client_for(*scopes: str, payments: bool = True) -> tuple[TestClient, FakePreparing]:
    preparing = FakePreparing()
    bus = CommandBus()
    bus.register(PreparePaymentCommand, preparing)
    bus.register(ConfirmPaymentCommand, FakeConfirming(preparing))
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


def test_a_payment_mixed_with_a_read_is_returned_as_choices_before_either_runs() -> None:
    client, preparing = client_for(Scope.QUESTIONS_READ, Scope.PAYMENTS_PREPARE)

    response = client.post(
        "/v1/questions", json={"question": "Haz un bizum a Marta y cuanto gaste este mes"}
    )

    assert response.status_code == 200
    assert response.json()["outcome"] == "NEEDS_CLARIFICATION"
    assert response.json()["suggestions"] == [
        "Haz un bizum a Marta",
        "cuanto gaste este mes",
    ]
    assert preparing.sentences == []


def test_an_unsafe_compound_has_the_same_choice_on_the_streaming_endpoint() -> None:
    client, _ = client_for(Scope.QUESTIONS_READ)

    response = client.post(
        "/v1/questions/stream",
        json={"question": "Haz un bizum a Marta y cuanto gaste este mes"},
    )
    events = events_of(response)

    assert [event["type"] for event in events] == ["answer"]
    assert events[0]["outcome"] == "NEEDS_CLARIFICATION"
    assert events[0]["suggestions"] == ["Haz un bizum a Marta", "cuanto gaste este mes"]


def test_starting_a_payment_discards_an_older_failed_query_retry() -> None:
    client, _ = client_for(Scope.QUESTIONS_READ, Scope.PAYMENTS_PREPARE)

    body = client.post(
        "/v1/questions",
        json={
            "question": PAYMENT,
            "conversation": {"retry_plan": {"direction": "EXPENSE", "period": "LAST_MONTH"}},
        },
    ).json()

    assert body["conversation"]["workflow"] == "PAYMENT_CONFIRMATION"
    assert body["conversation"]["retry_plan"] is None


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


def events_of(response) -> list[dict]:
    """The SSE payloads, in order."""
    return [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


def confirmation_conversation() -> dict:
    return {
        "workflow": "PAYMENT_CONFIRMATION",
        "awaiting": "CONFIRMATION",
        "intent_id": str(INTENT),
        "recipient": "Marta Solis Vega",
        "amount_minor": 2_000,
        "concept": "tapas",
    }


def test_changing_the_amount_cancels_the_old_intent_and_prepares_a_new_preview() -> None:
    client, payments = client_for(
        Scope.QUESTIONS_READ, Scope.PAYMENTS_PREPARE, Scope.PAYMENTS_CONFIRM
    )

    body = client.post(
        "/v1/questions",
        json={"question": "mejor 30 euros", "conversation": confirmation_conversation()},
    ).json()

    assert payments.confirmations == ["no"]
    assert len(payments.commands) == 1
    pending = payments.commands[0].pending
    assert pending is not None
    assert pending.recipient_name == "Marta Solis Vega"
    assert pending.amount_minor == 2_000
    assert pending.concept == "tapas"
    assert body["outcome"] == PaymentOutcome.AWAITING_CONFIRMATION.value
    assert body["conversation"]["workflow"] == "PAYMENT_CONFIRMATION"


@pytest.mark.parametrize(
    ("correction", "recipient", "concept"),
    [
        ("era para María", None, "tapas"),
        ("cambia el concepto a cena", "Marta Solis Vega", "tapas"),
        ("quita el concepto", "Marta Solis Vega", None),
    ],
)
def test_changing_recipient_or_concept_discards_the_old_intent_before_rebuilding(
    correction: str, recipient: str | None, concept: str | None
) -> None:
    client, payments = client_for(
        Scope.QUESTIONS_READ, Scope.PAYMENTS_PREPARE, Scope.PAYMENTS_CONFIRM
    )

    body = client.post(
        "/v1/questions",
        json={"question": correction, "conversation": confirmation_conversation()},
    ).json()

    assert payments.confirmations == ["no"]
    assert len(payments.commands) == 1
    assert payments.commands[0].pending is not None
    assert payments.commands[0].pending.recipient_name == recipient
    assert payments.commands[0].pending.amount_minor == 2_000
    assert payments.commands[0].pending.concept == concept
    assert body["outcome"] == PaymentOutcome.AWAITING_CONFIRMATION.value
    assert body["conversation"]["workflow"] == "PAYMENT_CONFIRMATION"
    assert body["text"].startswith("He descartado el Bizum anterior.")


@pytest.mark.parametrize(
    ("reply", "outcome", "workflow"),
    [
        ("sí", PaymentOutcome.PAID.value, None),
        ("no", PaymentOutcome.CANCELLED.value, None),
        ("¿cuánto era?", PaymentOutcome.AWAITING_CONFIRMATION.value, "PAYMENT_CONFIRMATION"),
        ("reintenta", PaymentOutcome.BANKING_UNAVAILABLE.value, "PAYMENT_CONFIRMATION"),
    ],
)
def test_the_stream_returns_the_state_after_a_payment_confirmation(
    reply: str, outcome: str, workflow: str | None
) -> None:
    """A terminal reply closes the buttons; an unclear one keeps the same preview available."""
    client, payments = client_for(
        Scope.QUESTIONS_READ, Scope.PAYMENTS_PREPARE, Scope.PAYMENTS_CONFIRM
    )

    response = client.post(
        "/v1/questions/stream",
        json={"question": reply, "conversation": confirmation_conversation()},
    )

    answer = events_of(response)[-1]
    assert answer["outcome"] == outcome
    assert answer["conversation"]["workflow"] == workflow
    assert payments.confirmations == [reply]
    if outcome == PaymentOutcome.AWAITING_CONFIRMATION.value:
        assert "Marta Solis Vega" in answer["text"]
        assert "20,00" in answer["text"]
    if outcome == PaymentOutcome.BANKING_UNAVAILABLE.value:
        assert "reintenta" in answer["text"]


def test_the_stream_answers_a_payment_the_same_way_the_other_route_does() -> None:
    """Same sentence, same answer, whichever endpoint a client used.

    The streaming route existed first and sent everything into the question pipeline, so "hazle un
    bizum de 20 euros a Marta" came back as the capability refusal there and as a preview on
    `/v1/questions`. A demo that shows stages on screen uses the stream, so the endpoint the user
    actually types into was the one that could not pay.
    """
    client, preparing = client_for(Scope.QUESTIONS_READ, Scope.PAYMENTS_PREPARE)

    streamed = events_of(client.post("/v1/questions/stream", json={"question": PAYMENT}))
    direct = client.post("/v1/questions", json={"question": PAYMENT}).json()

    answer = [event for event in streamed if event["type"] == "answer"]
    assert len(answer) == 1
    assert answer[0]["outcome"] == direct["outcome"] == PaymentOutcome.AWAITING_CONFIRMATION.value
    assert answer[0]["intent_id"] == direct["intent_id"] == str(INTENT)
    assert preparing.sentences == [PAYMENT, PAYMENT]


def test_the_stream_says_what_it_is_doing_before_the_preview_arrives() -> None:
    """A stage first, like the question path. Preparing involves a model call and a call to the
    bank, so silence until the preview lands is the problem the stages exist to solve."""
    client, _ = client_for(Scope.QUESTIONS_READ, Scope.PAYMENTS_PREPARE)

    streamed = events_of(client.post("/v1/questions/stream", json={"question": PAYMENT}))

    assert streamed[0]["type"] == "stage"
    assert streamed[-1]["type"] == "answer"


def test_the_stream_refuses_without_the_scope_like_the_other_route() -> None:
    client, preparing = client_for(Scope.QUESTIONS_READ)

    streamed = events_of(client.post("/v1/questions/stream", json={"question": PAYMENT}))

    assert [e for e in streamed if e["type"] == "answer"][0]["outcome"] == "UNSUPPORTED"
    assert preparing.sentences == []


def test_the_stream_still_streams_a_question() -> None:
    """The stages are what this route is for, and routing a payment must not cost them."""
    client, _ = client_for(Scope.QUESTIONS_READ, Scope.PAYMENTS_PREPARE)

    streamed = events_of(
        client.post("/v1/questions/stream", json={"question": "¿cuánto gasté en junio?"})
    )

    assert [event["type"] for event in streamed].count("stage") >= 1
