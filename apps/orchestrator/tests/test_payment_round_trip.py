"""The pending workflow is consulted before the sentence is classified.

This is the structural half of the payment fix, and it lives at the controller because that is where
the chat's two paths fork. The rule the report asked for, in order:

    a payment under way, and this sentence fills a slot   → continue the payment
    a payment order                                       → start a payment
    anything else                                         → classify it afresh

Without the first line, "15 euros" was classified from scratch — money with no payment verb is
not a payment — and reached the financial planner, which asked which period it was about.

The last line matters as much: a payment under way must not swallow a genuine change of subject.
"¿Cuánto gasté este mes?" in the middle of a bizum is still a question.
"""

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from finguard_orchestrator.application.bus.command_bus import CommandBus
from finguard_orchestrator.application.bus.query_bus import QueryBus
from finguard_orchestrator.application.commands.pay import (
    PaymentAnswer,
    PreparePaymentCommand,
    SpokenCandidate,
    SpokenPaymentDraft,
)
from finguard_orchestrator.application.queries.ask_financial_question import (
    AskFinancialQuestionQuery,
    AskFinancialQuestionResult,
)
from finguard_orchestrator.application.queries.list_contacts import (
    ContactRow,
    ListContactsQuery,
    ListContactsResult,
)
from finguard_orchestrator.bootstrap.dependencies import (
    authenticated_caller,
    get_ask_handler,
    get_command_bus,
    get_query_bus,
)
from finguard_orchestrator.bootstrap.main import app
from finguard_orchestrator.domain.enums import PaymentOutcome
from finguard_orchestrator.domain.value_objects.caller import Caller
from finguard_orchestrator.domain.value_objects.scope import Scope

ACCOUNT = uuid.UUID("11111111-1111-1111-1111-111111111111")
INTENT = uuid.UUID("33333333-3333-3333-3333-333333333333")

CONTACTS = ListContactsResult(
    contacts=[
        ContactRow(str(uuid.UUID(int=1)), "Marta Ruiz Gomez", "+34600111222"),
        ContactRow(str(uuid.UUID(int=2)), "Marta Solis Vega", "+34600333444"),
    ]
)


def paying(*, suspended: bool = False, **draft: object) -> dict:
    """A payment under way, as the wire spells it. The tests send states rather than fields now,
    which is the change: a client cannot assemble a combination the service never issues."""
    return {
        "workflow": "PAYMENT_IN_PROGRESS",
        "awaiting": "AMOUNT" if "amount_minor" not in draft else "RECIPIENT",
        "payment": {"currency": "EUR", **draft},
        "suspended": suspended,
    }


PAYING_MARTA = paying(recipient_name="Marta Solis Vega")

PREVIEWED = PaymentAnswer(
    outcome=PaymentOutcome.AWAITING_CONFIRMATION.value,
    text="Vas a enviar 15,00 € a Marta Solis Vega. ¿Confirmas?",
    intent_id=INTENT,
)

ASKED_FOR_THE_AMOUNT = PaymentAnswer(
    outcome=PaymentOutcome.NEEDS_CLARIFICATION.value,
    text="¿De cuánto quieres hacerle el bizum a Marta Solis Vega?",
    draft=SpokenPaymentDraft(recipient_name="Marta Solis Vega"),
)


class FakePreparing:
    def __init__(self, answer: PaymentAnswer) -> None:
        self._answer = answer
        self.commands: list[PreparePaymentCommand] = []

    async def handle(self, command: PreparePaymentCommand) -> PaymentAnswer:
        self.commands.append(command)
        return self._answer


ANSWERED = AskFinancialQuestionResult(outcome="PLANNED", text="1.895,97 € el mes pasado.")


class FakeAsking:
    """Both shapes, because the two endpoints reach the question path differently: /v1/questions
    goes through the bus and the stream takes the handler directly. Testing one and not the other is
    how the endpoints diverged twice."""

    def __init__(self) -> None:
        self.sentences: list[str] = []

    async def handle(self, query: AskFinancialQuestionQuery) -> AskFinancialQuestionResult:
        self.sentences.append(query.sentence)
        return ANSWERED

    async def stream(self, query: AskFinancialQuestionQuery):
        self.sentences.append(query.sentence)
        yield ANSWERED


class FakeContacts:
    async def handle(self, query: ListContactsQuery) -> ListContactsResult:
        return CONTACTS


def client_for(answer: PaymentAnswer = PREVIEWED) -> tuple[TestClient, FakePreparing, FakeAsking]:
    preparing, asking = FakePreparing(answer), FakeAsking()
    commands = CommandBus()
    commands.register(PreparePaymentCommand, preparing)
    queries = QueryBus()
    queries.register(AskFinancialQuestionQuery, asking)
    queries.register(ListContactsQuery, FakeContacts())
    app.dependency_overrides[get_command_bus] = lambda: commands
    app.dependency_overrides[get_query_bus] = lambda: queries
    app.dependency_overrides[get_ask_handler] = lambda: asking
    app.dependency_overrides[authenticated_caller] = lambda: Caller(
        holder_reference="ES-0001",
        account_id=ACCOUNT,
        scopes=frozenset({Scope.QUESTIONS_READ, Scope.PAYMENTS_PREPARE}),
    )
    app.state.payments_enabled = True
    app.state.questions_enabled = True
    return TestClient(app), preparing, asking


@pytest.fixture(autouse=True)
def _restore():
    app.dependency_overrides.clear()
    enabled = app.state.payments_enabled
    yield
    app.dependency_overrides.clear()
    app.state.payments_enabled = enabled


def test_an_amount_continues_the_payment_it_was_asked_for() -> None:
    """The turn that broke the reported conversation."""
    client, preparing, asking = client_for()

    body = client.post(
        "/v1/questions",
        json={
            "question": "15 euros",
            "conversation": PAYING_MARTA,
        },
    ).json()

    assert asking.sentences == [], "an answer to '¿de cuánto?' reached the financial planner"
    assert len(preparing.commands) == 1
    assert preparing.commands[0].pending is not None
    assert preparing.commands[0].pending.recipient_name == "Marta Solis Vega"
    assert body["intent_id"] == str(INTENT)


def test_a_name_continues_a_payment_that_is_missing_one() -> None:
    client, preparing, asking = client_for()

    client.post(
        "/v1/questions",
        json={"question": "a Marta Solis Vega", "conversation": paying(amount_minor=1500)},
    )

    assert asking.sentences == []
    assert preparing.commands[0].pending is not None
    assert preparing.commands[0].pending.amount_minor == 1_500


def test_a_change_of_subject_is_still_a_question() -> None:
    """The rule that keeps the state machine honest. A payment under way must not swallow
    everything the user says next."""
    client, preparing, asking = client_for()

    client.post(
        "/v1/questions",
        json={
            "question": "¿cuánto gasté el mes pasado?",
            "conversation": PAYING_MARTA,
        },
    )

    assert preparing.commands == [], "a question was read as part of a payment"
    assert asking.sentences == ["¿cuánto gasté el mes pasado?"]


def test_a_bare_amount_does_not_silently_resume_a_suspended_payment() -> None:
    client, preparing, asking = client_for()

    body = client.post(
        "/v1/questions",
        json={
            "question": "15 euros",
            "conversation": paying(suspended=True, recipient_name="Marta Solis Vega"),
        },
    ).json()

    assert preparing.commands == []
    assert asking.sentences == []
    assert body["outcome"] == PaymentOutcome.NEEDS_CLARIFICATION.value
    assert "pausado" in body["text"].lower()
    assert body["conversation"]["suspended"] is True


def test_an_explicit_resume_reopens_the_payment_on_the_streaming_route() -> None:
    client, preparing, asking = client_for(answer=ASKED_FOR_THE_AMOUNT)

    response = client.post(
        "/v1/questions/stream",
        json={
            "question": "continuar con el bizum",
            "conversation": paying(suspended=True, recipient_name="Marta Solis Vega"),
        },
    )
    answer = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ][-1]

    assert asking.sentences == []
    assert len(preparing.commands) == 1
    assert preparing.commands[0].pending is not None
    assert preparing.commands[0].pending.recipient_name == "Marta Solis Vega"
    assert answer["outcome"] == PaymentOutcome.NEEDS_CLARIFICATION.value
    assert answer["conversation"]["suspended"] is False


def test_a_new_payment_order_does_not_inherit_the_suspended_recipient() -> None:
    client, preparing, asking = client_for()

    body = client.post(
        "/v1/questions",
        json={
            "question": "hazle un bizum de 20 euros a Aitor",
            "conversation": paying(suspended=True, recipient_name="Marta Solis Vega"),
        },
    ).json()

    assert asking.sentences == []
    assert len(preparing.commands) == 1
    assert preparing.commands[0].pending is None
    assert body["outcome"] == PaymentOutcome.AWAITING_CONFIRMATION.value


def test_uncertainty_repeats_the_missing_field_instead_of_becoming_a_financial_question() -> None:
    """The draft cannot advance, but the conversation can still give the user a useful next step."""
    client, preparing, asking = client_for()

    body = client.post(
        "/v1/questions",
        json={"question": "no sé", "conversation": PAYING_MARTA},
    ).json()

    assert preparing.commands == []
    assert asking.sentences == []
    assert body["outcome"] == PaymentOutcome.NEEDS_CLARIFICATION.value
    assert "Marta Solis Vega" in body["text"]
    assert body["conversation"]["workflow"] == "PAYMENT_IN_PROGRESS"


def test_uncertainty_about_the_recipient_offers_the_contact_list() -> None:
    client, preparing, asking = client_for()

    body = client.post(
        "/v1/questions",
        json={"question": "ni idea", "conversation": paying(amount_minor=1_500)},
    ).json()

    assert preparing.commands == []
    assert asking.sentences == []
    assert "contactos" in body["text"]
    assert "15,00" in body["text"]
    assert body["conversation"]["workflow"] == "PAYMENT_IN_PROGRESS"


@pytest.mark.parametrize("reply", ["no", "cancela", "mejor no"])
def test_an_incomplete_payment_can_be_abandoned(reply: str) -> None:
    client, preparing, asking = client_for()

    response = client.post(
        "/v1/questions/stream",
        json={"question": reply, "conversation": PAYING_MARTA},
    )
    answer = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ][-1]

    assert preparing.commands == []
    assert asking.sentences == []
    assert answer["outcome"] == PaymentOutcome.CANCELLED.value
    assert answer["conversation"]["workflow"] is None


def test_the_question_the_payment_asks_carries_the_draft_out() -> None:
    """Without it on the way out there is nothing to echo, and the next turn is a fresh sentence."""
    client, _, _ = client_for(ASKED_FOR_THE_AMOUNT)

    body = client.post("/v1/questions", json={"question": "hazle un bizum a Marta Solis"}).json()

    assert body["conversation"]["workflow"] == "PAYMENT_IN_PROGRESS"
    assert body["conversation"]["payment"]["recipient_name"] == "Marta Solis Vega"
    assert body["conversation"]["awaiting"] == "AMOUNT"


def test_an_ambiguity_comes_back_with_the_names_and_their_identifiers() -> None:
    ambiguous = PaymentAnswer(
        outcome=PaymentOutcome.NEEDS_CLARIFICATION.value,
        text="Tienes varios contactos que se llaman Marta. ¿A quién?",
        draft=SpokenPaymentDraft(amount_minor=1_500),
        candidates=[
            SpokenCandidate(str(uuid.UUID(int=1)), "Marta Ruiz Gomez", "+34600111222"),
            SpokenCandidate(str(uuid.UUID(int=2)), "Marta Solis Vega", "+34600333444"),
        ],
    )
    client, _, _ = client_for(ambiguous)

    body = client.post(
        "/v1/questions", json={"question": "hazle un bizum de 15 euros a Marta"}
    ).json()

    assert [c["display_name"] for c in body["conversation"]["candidates"]] == [
        "Marta Ruiz Gomez",
        "Marta Solis Vega",
    ]
    assert body["conversation"]["candidates"][0]["contact_id"] == str(uuid.UUID(int=1))
    assert body["conversation"]["payment"]["amount_minor"] == 1_500


def test_a_complete_draft_is_not_a_continuation() -> None:
    """A payment with both slots filled has already become a preview waiting for a yes. Treating a
    later sentence as a continuation of it would prepare the same payment twice."""
    client, preparing, asking = client_for()

    client.post(
        "/v1/questions",
        json={
            "question": "¿cuánto gasté?",
            "conversation": paying(recipient_name="Marta Solis Vega", amount_minor=1500),
        },
    )

    assert preparing.commands == []
    assert asking.sentences == ["¿cuánto gasté?"]


def test_a_draft_carrying_an_absurd_amount_is_a_bad_request() -> None:
    """It cannot have come from here, and it is input on the way back."""
    client, preparing, _ = client_for()

    response = client.post(
        "/v1/questions",
        json={"question": "15 euros", "conversation": paying(amount_minor=99_999_999)},
    )

    assert response.status_code == 422
    assert preparing.commands == []


def test_the_streaming_endpoint_continues_a_payment_too() -> None:
    """The web client types into this one, so a payment that could only be continued on the other
    route was a payment that could not be continued at all.

    The first version of the continuation wired the routing into /v1/questions alone, and the
    browser conversation still asked "¿de qué periodo?" — the same divergence between the two
    endpoints that had already been fixed once for starting a payment.
    """
    client, preparing, asking = client_for()

    with client.stream(
        "POST",
        "/v1/questions/stream",
        json={"question": "15 euros", "conversation": PAYING_MARTA},
    ) as response:
        body = "".join(response.iter_text())

    assert asking.sentences == [], "the stream sent a payment reply to the financial planner"
    assert len(preparing.commands) == 1
    assert preparing.commands[0].pending is not None
    assert str(INTENT) in body


def test_the_streaming_endpoint_still_lets_a_question_through() -> None:
    client, preparing, asking = client_for()

    with client.stream(
        "POST",
        "/v1/questions/stream",
        json={
            "question": "¿cuánto gasté el mes pasado?",
            "conversation": PAYING_MARTA,
        },
    ) as response:
        list(response.iter_text())

    assert preparing.commands == []
    assert asking.sentences == ["¿cuánto gasté el mes pasado?"]
