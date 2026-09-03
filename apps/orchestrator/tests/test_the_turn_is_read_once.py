"""One sentence, one reading, and everything downstream obeys it.

Both question routes decide three things about a turn: does it continue a half-built payment, does
it start a new one, and — inside the handler — which of the nine intents it is. All three used to
ask the router separately, from the same sentence, and three answers to one question is a thing
that can disagree with itself. It already did, twice:

    "¿a quién puedo hacerle un bizum?"   the handler knew this was not an order to pay.
                                         The controller, asking first, did not.
    "mueve 200 € a la hucha"             read as a transfer in one place and as the answer to a
                                         pending Bizum in another. It paid the wrong person.

Now the route asks once, at the top, and hands the reading down. That is worth a test on its own
while the reading is free; it is worth more when the interpreter is a model, where three questions
is three round trips per turn.

What these assert is the wiring, not the reading. Whatever interpreter a deployment installs is
what decides — that is the point of the port, and it is why the rule "a model may not start a
payment on its own" lives in `Promoted` rather than here. A controller that second-guessed the
interpreter would be the fourth opinion this change exists to remove.
"""

import json
import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient

from finguard_orchestrator.application import (
    AskFinancialQuestionHandler,
    AskFinancialQuestionQuery,
    AskFinancialQuestionResult,
    CommandBus,
    PaymentAnswer,
    PreparePaymentCommand,
    QueryBus,
)
from finguard_orchestrator.bootstrap.dependencies import (
    authenticated_caller,
    get_ask_handler,
    get_command_bus,
    get_query_bus,
    reporting_today,
    turn_interpreter,
)
from finguard_orchestrator.bootstrap.main import app
from finguard_orchestrator.domain.enums import Spoken
from finguard_orchestrator.domain.errors import InvalidValueError
from finguard_orchestrator.domain.models.conversation import Conversation
from finguard_orchestrator.domain.models.turn_analysis import (
    TurnAnalysis,
    TurnFocus,
    TurnRelation,
)
from finguard_orchestrator.domain.services.intent_router import Intent
from finguard_orchestrator.domain.value_objects.caller import Caller
from finguard_orchestrator.domain.value_objects.scope import Scope


class NoCards:
    """The card directory. Required like the balance and the address book, so no build can be
    half-wired — a chat that cannot reach the wallet answered "eso no puedo calcularlo todavía"
    about a screen the user can open."""

    async def all_of(self, account_id, **kwargs):
        from finguard_orchestrator.domain.models.card import Wallet

        return Wallet(since=__import__("datetime").date(2026, 7, 7))

    async def charges_on(self, account_id, card_id, **kwargs):
        return ()


ACCOUNT = uuid.UUID("11111111-1111-1111-1111-111111111111")
TODAY = date(2026, 8, 4)


class CountingInterpreter:
    """Answers whatever it was told to, and remembers how often it was asked."""

    def __init__(self, answer: Intent = Intent.FINANCIAL_QUERY) -> None:
        self._answer = answer
        self.sentences: list[str] = []

    async def interpret(self, sentence: str, *, conversation: Conversation) -> Intent:
        self.sentences.append(sentence)
        return self._answer


class RecordingHandler:
    """The ask handler, remembering the query it was given."""

    def __init__(self) -> None:
        self.queries: list[AskFinancialQuestionQuery] = []

    async def handle(self, query: AskFinancialQuestionQuery) -> AskFinancialQuestionResult:
        self.queries.append(query)
        return AskFinancialQuestionResult(outcome="PLANNED", text="12,00 € hoy.")

    async def stream(self, query: AskFinancialQuestionQuery):
        yield await self.handle(query)


class UnusedPlanner:
    """The planner, which must not be reached. A turn read as anything but a financial question is
    answered without one, so a call here is the handler having decided for itself."""

    def __init__(self) -> None:
        self.asked: list[str] = []

    async def plan(self, sentence: str, **kwargs: object) -> object:
        self.asked.append(sentence)
        raise AssertionError(f"the planner was asked about {sentence!r}")


class UnusedLedger:
    async def run(self, plan: object, **kwargs: object) -> object:
        raise AssertionError("the bank was read")


class NoAccounts:
    """Required ports the handler will not reach on these turns."""

    async def of(self, account_id: uuid.UUID, **kwargs: object) -> object:
        raise AssertionError("the balance was read")


class NoContacts:
    async def all_of(self, account_id: uuid.UUID) -> tuple[()]:
        return ()


def a_handler(**kwargs: object) -> AskFinancialQuestionHandler:
    return AskFinancialQuestionHandler(
        UnusedPlanner(),  # type: ignore[arg-type]
        UnusedLedger(),  # type: ignore[arg-type]
        accounts=NoAccounts(),  # type: ignore[arg-type]
        contacts=NoContacts(),
        cards=NoCards(),  # type: ignore[arg-type]
        **kwargs,  # type: ignore[arg-type]
    )


class RecordingPayments:
    def __init__(self) -> None:
        self.commands: list[PreparePaymentCommand] = []

    async def handle(self, command: PreparePaymentCommand) -> PaymentAnswer:
        self.commands.append(command)
        return PaymentAnswer(outcome="NEEDS_RECIPIENT", text="¿A quién?")


@pytest.fixture(autouse=True)
def _restore():
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def a_client(
    reads: CountingInterpreter,
) -> tuple[TestClient, RecordingHandler, RecordingPayments]:
    handler = RecordingHandler()
    payments = RecordingPayments()
    queries = QueryBus()
    queries.register(AskFinancialQuestionQuery, handler)
    commands = CommandBus()
    commands.register(PreparePaymentCommand, payments)
    app.dependency_overrides[get_query_bus] = lambda: queries
    app.dependency_overrides[get_command_bus] = lambda: commands
    app.dependency_overrides[get_ask_handler] = lambda: handler
    app.dependency_overrides[reporting_today] = lambda: TODAY
    app.dependency_overrides[turn_interpreter] = lambda: reads
    app.dependency_overrides[authenticated_caller] = lambda: Caller(
        holder_reference="ES-0002",
        account_id=ACCOUNT,
        scopes=frozenset({Scope.QUESTIONS_READ, Scope.PAYMENTS_PREPARE}),
    )
    app.state.questions_enabled = True
    app.state.payments_enabled = True
    return TestClient(app), handler, payments


def test_the_question_route_reads_the_turn_exactly_once():
    reads = CountingInterpreter()
    client, _, _ = a_client(reads)

    client.post("/v1/questions", json={"question": "¿cuánto gasté en agosto?"})

    assert reads.sentences == ["¿cuánto gasté en agosto?"]


def test_the_streaming_route_reads_the_turn_exactly_once():
    """The route the browser actually types into. It grew its own copy of every decision above,
    which is how a fix landed on `/v1/questions` while the demo stayed broken."""
    reads = CountingInterpreter()
    client, _, _ = a_client(reads)

    with client.stream(
        "POST", "/v1/questions/stream", json={"question": "¿cuánto gasté en agosto?"}
    ) as answer:
        answer.read()

    assert reads.sentences == ["¿cuánto gasté en agosto?"]


def test_the_reading_reaches_the_handler():
    """So the handler does not make a second one. It still can — it has an interpreter of its own
    for callers that are not this route — but on this path it must not need to."""
    reads = CountingInterpreter(Intent.EXPLAIN_PREVIOUS)
    client, handler, _ = a_client(reads)

    client.post("/v1/questions", json={"question": "¿por qué?"})

    assert handler.queries[0].analysis == TurnAnalysis(
        Intent.EXPLAIN_PREVIOUS, TurnRelation.FOLLOW_UP, TurnFocus.LAST_ANSWER
    )


def test_the_streaming_reading_reaches_the_handler():
    reads = CountingInterpreter(Intent.EXPLAIN_PREVIOUS)
    client, handler, _ = a_client(reads)

    with client.stream("POST", "/v1/questions/stream", json={"question": "¿por qué?"}) as answer:
        answer.read()

    assert handler.queries[0].analysis == TurnAnalysis(
        Intent.EXPLAIN_PREVIOUS, TurnRelation.FOLLOW_UP, TurnFocus.LAST_ANSWER
    )


def test_normal_and_streaming_routes_handoff_the_same_complete_analysis():
    reads = CountingInterpreter(Intent.EXPLAIN_PREVIOUS)
    normal_client, normal_handler, _ = a_client(reads)
    normal_client.post("/v1/questions", json={"question": "¿por qué?"})

    stream_client, stream_handler, _ = a_client(CountingInterpreter(Intent.EXPLAIN_PREVIOUS))
    with stream_client.stream(
        "POST", "/v1/questions/stream", json={"question": "¿por qué?"}
    ) as answer:
        answer.read()

    assert normal_handler.queries[0].analysis == stream_handler.queries[0].analysis
    assert normal_handler.queries[0].intent is None
    assert stream_handler.queries[0].intent is None


def test_query_rejects_discordant_legacy_intent_and_analysis():
    with pytest.raises(InvalidValueError):
        AskFinancialQuestionQuery(
            sentence="¿por qué?",
            account_id=ACCOUNT,
            today=TODAY,
            intent=Intent.FINANCIAL_QUERY,
            analysis=TurnAnalysis(
                Intent.EXPLAIN_PREVIOUS,
                TurnRelation.FOLLOW_UP,
                TurnFocus.LAST_ANSWER,
            ),
        )


def test_the_installed_interpreter_decides_whether_this_is_a_payment():
    """A sentence the rules would call a question, routed to the payment path because the installed
    interpreter said so. Nothing behind the port gets a vote — which is exactly why the deployed
    interpreter is `Deterministic` today and why promoting a model keeps `Promoted`'s payment rule.
    """
    reads = CountingInterpreter(Intent.PAYMENT)
    client, handler, payments = a_client(reads)

    client.post("/v1/questions", json={"question": "¿cuánto gasté en agosto?"})

    assert [command.sentence for command in payments.commands] == ["¿cuánto gasté en agosto?"]
    assert handler.queries == []


def test_an_unambiguous_order_outranks_the_catch_all_reading():
    """This asserted the opposite until a test subject spent seven turns unable to send 25 €.

    The rule was "the reading is what routes, so a payment nobody read as one is a question — the
    safe way round", and the safety argument is weaker than it looks: routing to the payment path
    moves no money, it produces a preview that needs an explicit yes. Reading an order as a question
    cost the holder the whole conversation, and they ended up shouting at the assistant.

    So FINANCIAL_QUERY does not settle it. That intent is the catch-all — the prompt tells the model
    to answer it whenever unsure — and `payment_router.is_payment` is the *narrower* reader: it
    needs a payment verb and money, and it refuses questions, the past tense and money arriving. A
    closed vocabulary saying "this is an order" beats a model saying "I am not sure", which is the
    doctrine `explicit_facts` already rests on.
    """
    reads = CountingInterpreter(Intent.FINANCIAL_QUERY)
    client, handler, payments = a_client(reads)

    client.post("/v1/questions", json={"question": "hazle un bizum de 20 € a Marta"})

    assert [command.sentence for command in payments.commands] == ["hazle un bizum de 20 € a Marta"]
    assert handler.queries == []


def test_a_positive_reading_that_is_not_a_payment_is_still_respected():
    """The half of the old invariant that survives, and the reason the override is not general.

    LIST_CONTACTS is what "¿a quién puedo hacerle un bizum?" is, and `is_payment` says True for it —
    the narrower reader is narrower about questions, not omniscient. Every reading other than the
    catch-all is a positive choice about what the sentence *is*, and overriding one would bring back
    the defect `_is_an_order_to_pay` was written to fix: the assistant asking who to pay, to
    somebody asking who they could pay.
    """
    reads = CountingInterpreter(Intent.LIST_CONTACTS)
    client, handler, payments = a_client(reads)

    client.post("/v1/questions", json={"question": "¿a quién puedo hacerle un bizum?"})

    assert payments.commands == []


def test_a_previewed_payment_is_not_classified_at_all():
    """A preview owns the next turn whatever the words mean, so there is nothing to read and no
    reason to pay a model to read it. `sí` is not a financial question in any interpreter."""
    reads = CountingInterpreter()
    client, _, _ = a_client(reads)

    client.post(
        "/v1/questions",
        json={
            "question": "sí",
            "conversation": {
                "workflow": "PAYMENT_CONFIRMATION",
                "intent_id": str(uuid.uuid4()),
                "recipient": "Marta Solis Vega",
                "amount_minor": 2_000,
            },
        },
    )

    assert reads.sentences == []


async def test_the_handler_obeys_the_reading_it_was_given():
    """The other half of the wiring. A route that hands its reading down to a handler that ignores
    it has moved nothing: there would still be two readings, one of them silent."""
    handler = a_handler()

    # A sentence any router calls a financial question, read as something else by the caller. The
    # planner raises if it is reached, so this passing means the reading was honoured.
    answer = await handler.handle(
        AskFinancialQuestionQuery(
            sentence="¿cuánto gasté en agosto?",
            account_id=ACCOUNT,
            today=TODAY,
            intent=Intent.IDENTITY_OR_HELP,
        )
    )

    assert answer.outcome == Spoken.IDENTITY.value


async def test_the_handler_reads_the_turn_when_nobody_did():
    """The corpus and every unit test call it this way, so the fallback is not decoration. It is
    also the seam that keeps a future caller from getting a *different* reading than the route: the
    handler asks the same interpreter the deployment installed."""
    reads = CountingInterpreter(Intent.IDENTITY_OR_HELP)
    handler = a_handler(interpreter=reads)

    await handler.handle(
        AskFinancialQuestionQuery(
            sentence="¿cuánto gasté en agosto?", account_id=ACCOUNT, today=TODAY
        )
    )

    assert reads.sentences == ["¿cuánto gasté en agosto?"]


def test_the_stream_carries_the_answer_through():
    """Guards the plumbing above: a route that read the turn correctly and then dropped the answer
    would pass every assertion here and be broken on screen."""
    reads = CountingInterpreter()
    client, _, _ = a_client(reads)

    with client.stream(
        "POST", "/v1/questions/stream", json={"question": "¿cuánto gasté en agosto?"}
    ) as response:
        body = response.read().decode()

    payloads = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: ")]
    assert [payload for payload in payloads if payload["type"] == "answer"]
