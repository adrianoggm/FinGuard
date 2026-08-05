"""A conversation that goes wrong is still a conversation.

`test_conversation_acceptance` walks the conversations that work. These walk the ones that do not:
the bank did not answer, the filters matched nothing, the question was refused, the same question
came back twice. Each of those leaves the user holding a state, and the state is only useful if
pressing what it offers actually does something.

Driven over HTTP with the conversation echoed back, like the browser, and for the reason that file
gives: the handler's own tests were green through every defect in the review. What broke was the
chain — controller, mapper, payload, routing, back — and a chain is only checked by walking it.

**The property most of this file is about.** Every label this service offers as a suggestion is one
it reads itself, deterministically, on the next turn. That is what makes a suggestion a control
rather than a hint: a button whose behaviour depends on the configured model is a button nobody can
promise anything about. It is stated in `query_recovery`'s docstring and was asserted nowhere.
"""

import json
import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient

from finguard_orchestrator.application import (
    AskFinancialQuestionHandler,
    AskFinancialQuestionQuery,
    CommandBus,
    QueryBus,
)
from finguard_orchestrator.bootstrap.dependencies import (
    authenticated_caller,
    get_ask_handler,
    get_command_bus,
    get_query_bus,
    reporting_today,
)
from finguard_orchestrator.bootstrap.main import app
from finguard_orchestrator.domain.enums import Direction, Period, PlanningOutcome, Spoken
from finguard_orchestrator.domain.errors import BankingUnavailableError
from finguard_orchestrator.domain.models.planning_result import PlanningResult
from finguard_orchestrator.domain.models.query_answer import Group, QueryAnswer
from finguard_orchestrator.domain.services import query_recovery
from finguard_orchestrator.domain.value_objects.caller import Caller
from finguard_orchestrator.domain.value_objects.financial_plan import (
    AbsoluteRange,
    FinancialPlan,
    FinancialPlanDraft,
    RelativePeriod,
)
from finguard_orchestrator.domain.value_objects.scope import Scope

ACCOUNT = uuid.UUID("11111111-1111-1111-1111-111111111111")
TODAY = date(2026, 8, 4)

LAST_MONTH = RelativePeriod(period=Period.LAST_MONTH)

SOMETHING = QueryAnswer(
    currency="EUR",
    total_minor=46_556,
    movement_count=3,
    groups=(Group("total", 46_556, 3),),
)
NOTHING = QueryAnswer(currency="EUR", total_minor=0, movement_count=0)


class Planner:
    """One plan per sentence fragment, and a record of every sentence it was asked about.

    `asked` is what half these tests assert on: a control the user pressed must be read here and
    never sent to a model, or its behaviour changes with the configured provider.
    """

    def __init__(self, script: dict[str, PlanningResult]) -> None:
        self._script = script
        self.asked: list[str] = []

    async def plan(self, sentence: str, **_: object) -> PlanningResult:
        self.asked.append(sentence)
        for fragment, result in self._script.items():
            if fragment in sentence.lower():
                return result
        raise AssertionError(f"nothing scripted for {sentence!r}")


class Ledger:
    """A ledger that is either down or answering, switched by the test between turns.

    A flag rather than a queue of outcomes, because the handler reads more than once per turn: it
    retries a failure before giving up, so a queue whose second entry was a success made the *first*
    turn succeed and there was never a failed state to recover from.
    """

    def __init__(self, answer: QueryAnswer = SOMETHING, *, down: bool = False) -> None:
        self.answer = answer
        self.down = down
        self.plans: list[FinancialPlan] = []

    async def run(self, plan: FinancialPlan, **_: object) -> QueryAnswer:
        self.plans.append(plan)
        if self.down:
            raise BankingUnavailableError("the bank did not answer")
        return self.answer

    async def ranked(self, *_: object, **__: object) -> None:
        raise AssertionError("no ranking in these conversations")


class NoAccounts:
    async def of(self, *_: object, **__: object) -> None:
        raise AssertionError("no balance question in these conversations")


class NoContacts:
    async def all_of(self, *_: object, **__: object) -> tuple[()]:
        return ()


@pytest.fixture(autouse=True)
def _restore():  # type: ignore[no-untyped-def]
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def wired(
    script: dict[str, PlanningResult],
    answer: QueryAnswer = SOMETHING,
    *,
    down: bool = False,
) -> tuple[TestClient, Planner, Ledger]:
    planner = Planner(script)
    ledger = Ledger(answer, down=down)
    handler = AskFinancialQuestionHandler(
        planner,  # type: ignore[arg-type]
        ledger,  # type: ignore[arg-type]
        accounts=NoAccounts(),  # type: ignore[arg-type]
        contacts=NoContacts(),  # type: ignore[arg-type]
    )
    queries = QueryBus()
    queries.register(AskFinancialQuestionQuery, handler)

    app.dependency_overrides[get_query_bus] = lambda: queries
    app.dependency_overrides[get_ask_handler] = lambda: handler
    app.dependency_overrides[get_command_bus] = lambda: CommandBus()
    app.dependency_overrides[reporting_today] = lambda: TODAY
    app.dependency_overrides[authenticated_caller] = lambda: Caller(
        holder_reference="ES-0001",
        account_id=ACCOUNT,
        scopes=frozenset({Scope.QUESTIONS_READ}),
    )
    app.state.questions_enabled = True
    app.state.payments_enabled = False
    return TestClient(app), planner, ledger


class Chat:
    """A client that holds the conversation the way the browser does: store it, send it back."""

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


def _last_answer(response: object) -> dict:
    events = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()  # type: ignore[attr-defined]
        if line.startswith("data: ")
    ]
    return [event for event in events if event["type"] == "answer"][-1]


GROCERIES = FinancialPlan(direction=Direction.EXPENSE, period=LAST_MONTH, categories=("GROCERIES",))
AT_A_SHOP = FinancialPlan(
    direction=Direction.EXPENSE,
    period=LAST_MONTH,
    categories=("GROCERIES",),
    merchants=("Mercadonna",),
)


# --- the bank did not answer --------------------------------------------------------------


@pytest.mark.parametrize("streaming", [False, True])
def test_a_question_the_bank_could_not_answer_is_kept_to_be_retried(streaming: bool) -> None:
    """The plan survives the failure, over the wire, so the user does not retype the question.

    Both routes, because the browser types into the streaming one and this state has to cross two
    boundaries it never crossed before: out through the mapper, and back in as a typed plan.
    """
    client, _, _ = wired({"gasté": PlanningResult.planned(GROCERIES)}, down=True)

    answer = Chat(client, streaming).say("¿cuánto gasté el mes pasado en el súper?")

    assert answer["outcome"] == "BANKING_UNAVAILABLE"
    assert answer["conversation"]["retry_plan"]["categories"] == ["GROCERIES"]
    assert answer["suggestions"] == query_recovery.retry_suggestions()


def test_pressing_retry_runs_the_same_question_without_asking_the_model() -> None:
    """The reason the plan is kept rather than the sentence. Re-reading the words would give the
    model a second chance to read them differently, and the user asked once."""
    client, planner, ledger = wired({"gasté": PlanningResult.planned(GROCERIES)}, down=True)
    chat = Chat(client)

    chat.say("¿cuánto gasté el mes pasado en el súper?")
    asked_before = len(planner.asked)
    ledger.down = False

    retried = chat.say(query_recovery.RETRY)

    assert planner.asked == planner.asked[:asked_before], "the sentence was read a second time"
    assert retried["outcome"] == PlanningOutcome.PLANNED.value
    assert ledger.plans[-1].categories == ("GROCERIES",)
    assert ledger.plans[-1].period == LAST_MONTH


def test_a_bank_that_recovers_leaves_no_retry_behind() -> None:
    """A retry that survives its own success is a "reintenta" three turns later running a question
    nobody remembers asking."""
    client, _, ledger = wired({"gasté": PlanningResult.planned(GROCERIES)}, down=True)
    chat = Chat(client)

    chat.say("¿cuánto gasté el mes pasado en el súper?")
    ledger.down = False
    retried = chat.say(query_recovery.RETRY)

    assert retried["conversation"]["retry_plan"] is None


def test_cancelling_a_failed_query_stops_a_later_retry_reviving_it() -> None:
    """The other half. "Cancelar consulta" has to actually discard it, or the word "reintenta" two
    turns later reaches a plan the user abandoned."""
    client, _, ledger = wired({"gasté": PlanningResult.planned(GROCERIES)}, down=True)
    chat = Chat(client)

    chat.say("¿cuánto gasté el mes pasado en el súper?")
    ledger.down = False
    reads_before = len(ledger.plans)
    cancelled = chat.say(query_recovery.CANCEL)

    assert cancelled["outcome"] == Spoken.GUIDANCE.value
    assert cancelled["conversation"]["retry_plan"] is None
    assert len(ledger.plans) == reads_before, "cancelling ran the query it was cancelling"


# --- the filters matched nothing ----------------------------------------------------------


def test_an_empty_answer_offers_relaxations_of_the_filters_it_actually_used() -> None:
    """Not a fixed list. A question with no merchant in it must not be offered "buscar sin
    comercio", which does nothing and reads as an answer that failed."""
    client, _, _ = wired({"mercadonna": PlanningResult.planned(AT_A_SHOP)}, NOTHING)

    answer = Chat(client).say("¿cuánto gasté en Mercadonna el mes pasado?")

    assert answer["suggestions"] == [
        query_recovery.WITHOUT_CATEGORY,
        query_recovery.WITHOUT_MERCHANT,
        query_recovery.THREE_MONTHS,
    ]


def test_pressing_a_relaxation_drops_that_filter_and_leaves_the_rest() -> None:
    """One field, in code. A relaxation that re-read the sentence would be a second chance for the
    model to change something the user never touched."""
    client, planner, ledger = wired({"mercadonna": PlanningResult.planned(AT_A_SHOP)}, NOTHING)
    chat = Chat(client)

    chat.say("¿cuánto gasté en Mercadonna el mes pasado?")
    asked_before = len(planner.asked)
    chat.say(query_recovery.WITHOUT_MERCHANT)

    assert len(planner.asked) == asked_before, "a relaxation reached the model"
    assert ledger.plans[-1].merchants == ()
    assert ledger.plans[-1].categories == ("GROCERIES",), "it dropped a filter nobody pressed"
    assert ledger.plans[-1].period == LAST_MONTH


@pytest.mark.parametrize(
    "pressed",
    [query_recovery.WITHOUT_CATEGORY, query_recovery.WITHOUT_MERCHANT, query_recovery.THREE_MONTHS],
)
def test_every_relaxation_offered_is_one_the_next_turn_reads_itself(pressed: str) -> None:
    """The contract of the whole module, and it was asserted nowhere: a label offered here is a
    label read here. One that fell through to the model would be a button whose behaviour depends
    on which model a deployment configured."""
    client, planner, ledger = wired({"mercadonna": PlanningResult.planned(AT_A_SHOP)}, NOTHING)
    chat = Chat(client)

    chat.say("¿cuánto gasté en Mercadonna el mes pasado?")
    asked_before = len(planner.asked)
    reads_before = len(ledger.plans)

    chat.say(pressed)

    assert len(planner.asked) == asked_before, f"{pressed!r} reached the model"
    assert len(ledger.plans) > reads_before, f"{pressed!r} did nothing"


# --- the same question, twice -------------------------------------------------------------


@pytest.mark.parametrize("choice", query_recovery.WINDOW_CHOICES)
def test_the_periods_offered_after_a_loop_finish_the_turn(choice: str) -> None:
    """`test_conversation_acceptance` asserts the four choices are offered. This presses each one.

    Offering a way out that does not work is worse than asking a third time: the user has now been
    told the button is the answer, and the button is another dead end.
    """
    asking = PlanningResult.needs_clarification(
        "¿De qué periodo?", draft=FinancialPlanDraft(direction=Direction.EXPENSE)
    )
    client, _, ledger = wired({"": asking})
    chat = Chat(client)

    chat.say("¿cuánto llevo gastado?")
    chat.say("pues eso")
    answered = chat.say(choice)

    assert answered["outcome"] == PlanningOutcome.PLANNED.value, f"{choice!r} did not finish it"
    assert ledger.plans, f"{choice!r} produced no query"


def test_the_slot_a_question_asked_does_not_outlive_the_answer_to_it() -> None:
    """Three turns, and the third is the point. A question, its answer, then a *different*
    question back: kept memory would read the third turn as a repeat of the first and hand out a
    list of periods instead of asking — a real question, dismissed as a loop."""
    client, _, _ = wired(
        {
            "llevo gastado": PlanningResult.needs_clarification("¿De qué periodo?"),
            "el mes pasado": PlanningResult.planned(GROCERIES),
            "y en gasolina": PlanningResult.needs_clarification("¿De qué periodo?"),
        }
    )
    chat = Chat(client)

    asked = chat.say("¿cuánto llevo gastado?")
    assert asked["conversation"]["last_asked"] == "WINDOW"

    answered = chat.say("el mes pasado")
    assert answered["conversation"]["last_asked"] is None

    again = chat.say("¿y en gasolina?")
    assert again["suggestions"] == [], "an ordinary question was dismissed as a repeat"


# --- a refusal, and what comes after it -----------------------------------------------------


def test_a_refusal_does_not_outlive_the_question_it_refused() -> None:
    """An average is declined by the deterministic guard, which is a control and stays one. What
    must not happen is the refusal colouring the next turn: the conversation after it is an
    ordinary conversation, and the state that named the capability is gone."""
    client, _, _ = wired({"gasté": PlanningResult.planned(GROCERIES)})
    chat = Chat(client)

    refused = chat.say("¿cuál es mi gasto medio en gasolina el mes pasado?")
    assert refused["outcome"] == PlanningOutcome.UNSUPPORTED.value
    assert refused["conversation"]["workflow"] == "UNSUPPORTED_CAPABILITY"

    after = chat.say("¿cuánto gasté el mes pasado en el súper?")

    assert after["outcome"] == PlanningOutcome.PLANNED.value
    assert after["conversation"]["workflow"] is None
    assert after["conversation"]["capability"] is None


# --- a month, and the year behind it --------------------------------------------------------


def test_a_month_with_no_year_can_be_shifted_a_year_back() -> None:
    """Two deterministic readers composed, and neither knows about the other: one turns "en junio"
    into a calendar month, the next turns "¿y el año anterior?" into the same days one year back.

    Worth walking because the shift only works over an *explicit range* — a named period would have
    to be resolved, and resolving windows is banking's job. A bare month produces a range, so it
    composes; that is a property of the pair rather than of either.
    """
    june = FinancialPlan(
        direction=Direction.EXPENSE,
        date_range=AbsoluteRange(start=date(2026, 6, 1), end=date(2026, 6, 30)),
    )
    client, _, ledger = wired({"junio": PlanningResult.planned(june)})
    chat = Chat(client)

    chat.say("¿cuánto gasté en junio?")
    chat.say("¿y el año anterior?")

    assert ledger.plans[1].date_range.start == date(2025, 6, 1)
    assert ledger.plans[1].date_range.end == date(2025, 6, 30)


def test_a_month_named_in_a_follow_up_replaces_the_window_it_would_inherit() -> None:
    """The rule that produced the worst answer in the review, applied to the window a bare month
    names. The model emits the previous window because it thinks nothing changed; what the sentence
    says outright wins, and now a month with no year is something the sentence says outright."""
    client, _, ledger = wired(
        {
            "el mes pasado": PlanningResult.planned(GROCERIES),
            # What a model emits on the follow-up: the inherited window, unchanged.
            "en marzo": PlanningResult.planned(GROCERIES),
        }
    )
    chat = Chat(client)

    chat.say("¿cuánto gasté el mes pasado en el súper?")
    chat.say("¿y en marzo?")

    assert ledger.plans[1].date_range == AbsoluteRange(
        start=date(2026, 3, 1), end=date(2026, 3, 31)
    )
    assert ledger.plans[1].period is None, "the inherited period survived beside an explicit range"


# --- both routes, the same conversation -----------------------------------------------------


def test_the_two_routes_leave_the_same_state_after_the_same_failure() -> None:
    """The divergence this codebase has had to fix twice: a behaviour wired into `/v1/questions`
    and not into the one the browser types into. Compared on the state rather than the prose,
    because the state is what the next turn is built from."""
    direct, _, _ = wired({"gasté": PlanningResult.planned(GROCERIES)}, down=True)
    plain = Chat(direct).say("¿cuánto gasté el mes pasado en el súper?")

    streamed_client, _, _ = wired({"gasté": PlanningResult.planned(GROCERIES)}, down=True)
    streamed = Chat(streamed_client, streaming=True).say("¿cuánto gasté el mes pasado en el súper?")

    assert plain["conversation"] == streamed["conversation"]
    assert plain["suggestions"] == streamed["suggestions"]
    assert plain["outcome"] == streamed["outcome"]
