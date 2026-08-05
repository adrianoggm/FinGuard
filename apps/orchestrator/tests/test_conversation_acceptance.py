"""The reported conversations, driven end to end through the HTTP surface.

Every defect in the review was an *integration* defect. The unit tests of the conversation state
were green throughout: the state round-tripped, the mapper mapped, the guard guarded. What broke
was the chain —

    React → serialised state → controller → routing → planner → ledger → mapper → SSE → React

— and a chain is only checked by walking it. So these tests post real bodies to the real routes,
echo the `conversation` object back exactly as the browser does, and assert on the plan that
reached the ledger rather than on prose.

The planner is deterministic and scripted per sentence. That is the point rather than a shortcut:
what is being measured is whether the pipeline preserves and corrects context, and a live model
would make every assertion here a coin toss about something else.
"""

import json
import uuid
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from finguard_orchestrator.application import (
    AskFinancialQuestionHandler,
    AskFinancialQuestionQuery,
    CommandBus,
    ListContactsQuery,
    ListContactsResult,
    PaymentAnswer,
    PreparePaymentCommand,
    QueryBus,
)
from finguard_orchestrator.application.commands.pay import (
    ConfirmPaymentCommand,
    SpokenCandidate,
    SpokenPaymentDraft,
)
from finguard_orchestrator.application.queries.list_contacts import ContactRow
from finguard_orchestrator.bootstrap.dependencies import (
    authenticated_caller,
    get_ask_handler,
    get_command_bus,
    get_query_bus,
    reporting_today,
)
from finguard_orchestrator.bootstrap.main import app
from finguard_orchestrator.domain.enums import Direction, PaymentOutcome, Period
from finguard_orchestrator.domain.models.planning_result import PlanningResult
from finguard_orchestrator.domain.models.query_answer import Group, Movement, QueryAnswer
from finguard_orchestrator.domain.value_objects.caller import Caller
from finguard_orchestrator.domain.value_objects.financial_plan import (
    AbsoluteRange,
    FinancialPlan,
    FinancialPlanDraft,
    RelativePeriod,
)
from finguard_orchestrator.domain.value_objects.scope import Scope
from finguard_orchestrator.domain.value_objects.top_movements import TopMovements

ACCOUNT = uuid.UUID("11111111-1111-1111-1111-111111111111")
INTENT = uuid.UUID("33333333-3333-3333-3333-333333333333")
MARTA = uuid.UUID("22222222-2222-2222-2222-222222222222")
TODAY = date(2026, 8, 4)

THIS_YEAR = RelativePeriod(period=Period.THIS_YEAR)
FUEL_RANKING = TopMovements(
    direction=Direction.EXPENSE, period=THIS_YEAR, categories=("TRANSPORT_FUEL",)
)

FUEL_MOVEMENTS = [
    Movement(
        occurred_at=datetime(2026, 3, 14, 10, 0),
        description="Repostaje",
        amount_minor=8_240,
        direction="EXPENSE",
        merchant_name="Cepsa",
        category="TRANSPORT_FUEL",
        currency="EUR",
    ),
    Movement(
        occurred_at=datetime(2026, 6, 22, 19, 0),
        description="Repostaje",
        amount_minor=7_610,
        direction="EXPENSE",
        merchant_name="Repsol",
        category="TRANSPORT_FUEL",
        currency="EUR",
    ),
    Movement(
        occurred_at=datetime(2026, 7, 1, 8, 0),
        description="Repostaje",
        amount_minor=6_005,
        direction="EXPENSE",
        merchant_name="BP",
        category="TRANSPORT_FUEL",
        currency="EUR",
    ),
]


class ScriptedPlanner:
    """One plan per sentence, keyed by a fragment of it.

    Deterministic on purpose. These tests are about what the pipeline does with a plan — inherit
    it, correct it, keep it across a payment — and a live model would turn every assertion into a
    measurement of the model instead.
    """

    def __init__(self, script: dict[str, PlanningResult]) -> None:
        self._script = script
        self.asked: list[str] = []

    async def plan(self, sentence: str, *, today: date, previous=None, force_follow_up=False):
        self.asked.append(sentence)
        for fragment, result in self._script.items():
            if fragment in sentence.lower():
                return result
        raise AssertionError(f"the scripted planner has no answer for {sentence!r}")


class RecordingLedger:
    def __init__(self) -> None:
        self.plans: list[FinancialPlan] = []
        self.rankings: list[TopMovements] = []

    async def run(self, plan, *, account_id, today, reporting_timezone="Europe/Madrid", **kw):
        self.plans.append(plan)
        return QueryAnswer(
            currency="EUR",
            total_minor=46_556,
            movement_count=3,
            groups=(Group("Elena", 4_656, 2), Group("Sergio", 3_166, 1)),
        )

    async def ranked(self, ranking, *, account_id, today, reporting_timezone):
        self.rankings.append(ranking)
        position = ranking.rank - 1
        return FUEL_MOVEMENTS[position] if position < len(FUEL_MOVEMENTS) else None


class FakeAccounts:
    async def of(self, account_id, *, today, reporting_timezone):
        raise AssertionError("no balance question in these conversations")


class FakeContacts:
    async def all_of(self, account_id):
        return ()


class FakeContactDirectory:
    async def handle(self, query: ListContactsQuery) -> ListContactsResult:
        return ListContactsResult(
            contacts=[ContactRow(str(MARTA), "Marta Solis Vega", "+34600333444")]
        )


class ScriptedPayments:
    """A Bizum that asks for the amount, then previews, then pays. The three turns of a real one."""

    def __init__(self) -> None:
        self.commands: list[PreparePaymentCommand] = []
        self.confirmations: list[str] = []

    async def handle(self, command: PreparePaymentCommand) -> PaymentAnswer:
        self.commands.append(command)
        amount = command.pending.amount_minor if command.pending else None
        if "15" in command.sentence or amount:
            return PaymentAnswer(
                outcome=PaymentOutcome.AWAITING_CONFIRMATION.value,
                text="Vas a enviar 15,00 € a Marta Solis Vega. ¿Confirmas?",
                intent_id=INTENT,
                recipient="Marta Solis Vega",
                amount_minor=1_500,
                currency="EUR",
                concept="comida",
            )
        return PaymentAnswer(
            outcome=PaymentOutcome.NEEDS_CLARIFICATION.value,
            text="¿De cuánto quieres hacerle el bizum a Marta Solis Vega?",
            draft=SpokenPaymentDraft(recipient_name="Marta Solis Vega"),
            candidates=[SpokenCandidate(str(MARTA), "Marta Solis Vega", "+34600333444")],
        )


class ScriptedConfirmations:
    def __init__(self, payments: ScriptedPayments) -> None:
        self._payments = payments

    async def handle(self, command: ConfirmPaymentCommand) -> PaymentAnswer:
        self._payments.confirmations.append(command.reply)
        return PaymentAnswer(
            outcome=PaymentOutcome.PAID.value,
            text="Hecho. He enviado 15,00 € a Marta Solis Vega.",
            intent_id=INTENT,
            currency="EUR",
        )


@pytest.fixture(autouse=True)
def _restore():
    app.dependency_overrides.clear()
    enabled = app.state.payments_enabled
    yield
    app.dependency_overrides.clear()
    app.state.payments_enabled = enabled


def wired(script: dict[str, PlanningResult]):
    """The whole service, with a scripted model and a recording ledger behind it."""
    planner = ScriptedPlanner(script)
    ledger = RecordingLedger()
    payments = ScriptedPayments()

    handler = AskFinancialQuestionHandler(
        planner, ledger, accounts=FakeAccounts(), contacts=FakeContacts()
    )
    queries = QueryBus()
    queries.register(AskFinancialQuestionQuery, handler)
    queries.register(ListContactsQuery, FakeContactDirectory())
    commands = CommandBus()
    commands.register(PreparePaymentCommand, payments)
    commands.register(ConfirmPaymentCommand, ScriptedConfirmations(payments))

    app.dependency_overrides[get_query_bus] = lambda: queries
    app.dependency_overrides[get_ask_handler] = lambda: handler
    app.dependency_overrides[get_command_bus] = lambda: commands
    app.dependency_overrides[reporting_today] = lambda: TODAY
    app.dependency_overrides[authenticated_caller] = lambda: Caller(
        holder_reference="ES-0001",
        account_id=ACCOUNT,
        scopes=frozenset({Scope.QUESTIONS_READ, Scope.PAYMENTS_PREPARE, Scope.PAYMENTS_CONFIRM}),
        reporting_timezone="Atlantic/Canary",
    )
    app.state.payments_enabled = True
    app.state.questions_enabled = True
    return TestClient(app), ledger, payments


class Chat:
    """A client that keeps the conversation the way the browser does: store it, send it back."""

    def __init__(self, client: TestClient, streaming: bool = False) -> None:
        self._client = client
        self._streaming = streaming
        self.conversation: dict | None = None

    def say(self, question: str) -> dict:
        route = "/v1/questions/stream" if self._streaming else "/v1/questions"
        response = self._client.post(
            route, json={"question": question, "conversation": self.conversation}
        )
        assert response.status_code == 200, response.text
        answer = _last_answer(response) if self._streaming else response.json()
        self.conversation = answer["conversation"]
        return answer


def _last_answer(response) -> dict:
    events = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    return [event for event in events if event["type"] == "answer"][-1]


# --- walking down a ranking -----------------------------------------------------------


@pytest.mark.parametrize("streaming", [False, True])
def test_a_ranking_conversation_walks_to_the_position_the_user_names(streaming: bool) -> None:
    """The conversation from the report, three turns of it:

        ¿Cuál fue el gasto más caro del año en gasolina?   the largest
        ¿Y el segundo?                                     the second
        ¿Y el tercero?                                     the third, not the second again

    Both routes, because the browser types into the streaming one and the first version of this
    routing existed only on the other.
    """
    client, ledger, _ = wired({"gasolina": PlanningResult.planned(FUEL_RANKING)})
    chat = Chat(client, streaming)

    chat.say("¿cuál fue el gasto más caro del año en gasolina?")
    chat.say("¿y el segundo?")
    third = chat.say("¿y el tercero?")

    assert [r.rank for r in ledger.rankings] == [1, 2, 3]
    assert "BP" in third["text"]
    # The filters were never re-read from a sentence that names neither of them.
    assert all(r.categories == ("TRANSPORT_FUEL",) for r in ledger.rankings)
    assert all(r.period == THIS_YEAR for r in ledger.rankings)


def test_the_ranking_currency_is_the_ledgers_and_not_a_constant() -> None:
    client, _, _ = wired({"gasolina": PlanningResult.planned(FUEL_RANKING)})

    answer = Chat(client).say("¿cuál fue el gasto más caro del año en gasolina?")

    assert answer["currency"] == "EUR"


# --- a Bizum, start to finish ---------------------------------------------------------


@pytest.mark.parametrize("streaming", [False, True])
def test_a_bizum_survives_every_turn_it_takes_to_make_one(streaming: bool) -> None:
    """Four turns and three states: a payment being built, a preview waiting for a yes, and done.

    Each turn carries the conversation the previous answer produced, exactly as the browser does.
    A field dropped anywhere in the chain shows up here as a payment that has to be started again.
    """
    client, _, payments = wired({})
    chat = Chat(client, streaming)

    started = chat.say("hagamos un bizum a Marta")
    assert started["conversation"]["workflow"] == "PAYMENT_IN_PROGRESS"

    previewed = chat.say("15 euros con concepto de comida")
    assert previewed["outcome"] == PaymentOutcome.AWAITING_CONFIRMATION.value
    assert previewed["conversation"]["workflow"] == "PAYMENT_CONFIRMATION"
    assert previewed["conversation"]["intent_id"] == str(INTENT)
    assert previewed["conversation"]["currency"] == "EUR"

    paid = chat.say("sí")
    assert paid["outcome"] == PaymentOutcome.PAID.value
    assert paid["conversation"]["workflow"] is None
    assert payments.confirmations == ["sí"]


def test_a_question_in_the_middle_of_a_bizum_suspends_it_rather_than_losing_it() -> None:
    """The change of subject from the report. The Bizum has to still be there afterwards, and a
    bare amount must not silently resume it — the user had stopped thinking about the payment."""
    client, ledger, _ = wired(
        {
            "gasté": PlanningResult.planned(
                FinancialPlan(direction=Direction.EXPENSE, period=THIS_YEAR)
            )
        }
    )
    chat = Chat(client)

    chat.say("hagamos un bizum a Marta")
    asked = chat.say("¿cuánto gasté este mes?")

    assert asked["conversation"]["workflow"] == "PAYMENT_IN_PROGRESS"
    assert asked["conversation"]["suspended"] is True
    assert len(ledger.plans) == 1

    reminded = chat.say("15 euros")
    assert reminded["outcome"] == PaymentOutcome.NEEDS_CLARIFICATION.value
    assert reminded["conversation"]["workflow"] == "PAYMENT_IN_PROGRESS"

    resumed = chat.say("continuar con el bizum")
    assert resumed["conversation"]["workflow"] == "PAYMENT_IN_PROGRESS"


# --- the window the user wrote down ---------------------------------------------------


def test_an_explicit_window_is_never_inherited_across_turns() -> None:
    """The worst answer in the report: "los últimos 3 meses" answered over five, because the model
    said the window had not changed and the merge believed it."""
    five_months = RelativePeriod(period=Period.LAST_N_MONTHS, n=5)
    client, ledger, _ = wired(
        {
            "bizum": PlanningResult.planned(
                FinancialPlan(
                    direction=Direction.EXPENSE,
                    period=five_months,
                    categories=("TRANSFER_BIZUM",),
                )
            ),
            "alquiler": PlanningResult.planned(
                # What the model actually emitted: the previous window, inherited.
                FinancialPlan(
                    direction=Direction.EXPENSE,
                    period=five_months,
                    categories=("HOUSING_RENT",),
                )
            ),
        }
    )
    chat = Chat(client)

    chat.say("¿a qué personas les he hecho bizum en los últimos 5 meses?")
    chat.say("¿cuánto he gastado en total en los últimos 3 meses en alquiler?")

    assert ledger.plans[1].period == RelativePeriod(period=Period.LAST_N_MONTHS, n=3)
    assert ledger.plans[1].categories == ("HOUSING_RENT",)


def test_asking_who_is_answered_with_people_rather_than_a_total() -> None:
    """ "¿A qué personas les he hecho Bizum?" asks who. A total is a correct number and the wrong
    shape, which is what it used to come back as."""
    client, ledger, _ = wired(
        {
            "bizum": PlanningResult.planned(
                FinancialPlan(
                    direction=Direction.EXPENSE,
                    period=RelativePeriod(period=Period.LAST_N_MONTHS, n=5),
                    categories=("TRANSFER_BIZUM",),
                )
            )
        }
    )

    answer = Chat(client).say("¿a qué personas les he hecho bizum en los últimos 5 meses?")

    assert ledger.plans[0].group_by.value == "MERCHANT"
    assert [group["key"] for group in answer["groups"]] == ["Elena", "Sergio"]


def test_the_year_before_shifts_the_month_it_follows() -> None:
    """ "¿Y el año anterior?" after July of this year is July of last year — and the category, the
    direction and the metric are the ones already validated, so nothing is re-read."""
    july = PlanningResult.planned(
        FinancialPlan(
            direction=Direction.EXPENSE,
            date_range=AbsoluteRange(start=date(2026, 7, 1), end=date(2026, 7, 31)),
            categories=("RESTAURANTS",),
        )
    )
    client, ledger, _ = wired({"julio": july})
    chat = Chat(client)

    chat.say("¿cuánto gasté en restaurantes en julio de este año?")
    chat.say("¿y el año anterior?")

    assert ledger.plans[1].date_range.start == date(2025, 7, 1)
    assert ledger.plans[1].date_range.end == date(2025, 7, 31)
    assert ledger.plans[1].categories == ("RESTAURANTS",)


# --- not asking the same question three times -----------------------------------------


def test_the_same_question_twice_becomes_a_choice_instead_of_a_third_ask() -> None:
    """From the report, verbatim:

        "¿De qué periodo?"  → "del año"  → "¿De qué año?"  → "de este"  → "¿De qué periodo?"

    A user cannot get out of that by rephrasing, because rephrasing is what failed twice. The
    second identical question is answered with the periods this service can act on — and every one
    of those labels is a phrase `window_words` reads, so pressing one finishes the turn.
    """
    asking = PlanningResult.needs_clarification(
        "¿De qué periodo?", draft=FinancialPlanDraft(direction=Direction.EXPENSE)
    )
    client, _, _ = wired({"": asking})
    chat = Chat(client)

    first = chat.say("¿cuánto llevo gastado?")
    second = chat.say("pues eso")

    assert first["suggestions"] == []
    assert second["suggestions"] == ["Este mes", "El mes pasado", "Los últimos 12 meses", "En 2025"]
    assert second["text"] != first["text"]
