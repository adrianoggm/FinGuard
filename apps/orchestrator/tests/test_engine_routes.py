"""The LangGraph runtime on the two question routes.

Driven over HTTP on the episodes stack: its fake ledger, accounts, directory and contacts, and an
interpreter that is either scripted (it returns the operations a test gives it) or the real adapter
over a canned model. What is under test is the seam: what the graph answers, what it refuses
before it runs, what travels on the wire, and what each response says about who answered and how
many model calls it cost.
"""

import asyncio
import json
import uuid
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import ValidationError
from support.episodes import (
    ACCOUNT,
    INTENT,
    Accounts,
    Contacts,
    Directory,
    Stack,
    a_stack,
)
from support.memory_dialogue_store import MemoryDialogueStore
from support.memory_turn_reservations import MemoryTurnReservations

from finguard_orchestrator.application.engine.turn_result import EngineAnswer
from finguard_orchestrator.application.queries.financial_turn import AskFinancialQuestionResult
from finguard_orchestrator.bootstrap.config import Settings, get_settings
from finguard_orchestrator.bootstrap.main import app, create_app
from finguard_orchestrator.domain.engine.operations import (
    Ask,
    Delegate,
    Operation,
    Patch,
    PlanFields,
    Visualize,
)
from finguard_orchestrator.domain.engine.state import DialogueState, QueryFocus
from finguard_orchestrator.domain.enums import (
    Direction,
    GroupBy,
    PaymentOutcome,
    Period,
    Rail,
)
from finguard_orchestrator.domain.errors import QueryRejectedError
from finguard_orchestrator.domain.models.account_summary import Holding
from finguard_orchestrator.domain.models.conversation import (
    Conversation,
    PaymentConfirmation,
    Workflow,
)
from finguard_orchestrator.domain.models.query_answer import Group
from finguard_orchestrator.domain.ports.turn_operations import NO_ACCOUNT_NAMES
from finguard_orchestrator.domain.services import category_words
from finguard_orchestrator.domain.services.deterministic_visual_planner import (
    DeterministicVisualPlanner,
)
from finguard_orchestrator.domain.services.visual_plan_verifier import verify
from finguard_orchestrator.domain.services.visual_planning_policy import VisualPlanningPolicy
from finguard_orchestrator.domain.value_objects.caller import Caller
from finguard_orchestrator.domain.value_objects.financial_plan import (
    AbsoluteRange,
    FinancialPlan,
    RelativePeriod,
)
from finguard_orchestrator.domain.value_objects.scope import Scope
from finguard_orchestrator.infrastructure.controllers import engine_dispatch
from finguard_orchestrator.infrastructure.engine.llm_turn_operations import LlmTurnOperations
from finguard_orchestrator.infrastructure.graph.graph_state import GraphPorts
from finguard_orchestrator.infrastructure.graph.graph_turn import GraphTurnHandler
from finguard_orchestrator.infrastructure.http.dependencies import authenticated_caller
from finguard_orchestrator.infrastructure.llm.counting_provider import (
    CountingProvider,
    calls_so_far,
    counting_calls,
    record_provider_request,
    record_successful_model,
    record_usage,
)
from finguard_orchestrator.infrastructure.mappers import conversation_mapper
from finguard_orchestrator.infrastructure.security.metric_tokens import MetricTokenCodec

JSON_ROUTE = "/v1/questions"
STREAM_ROUTE = "/v1/questions/stream"
ROUTES = (JSON_ROUTE, STREAM_ROUTE)
JULY = AbsoluteRange(date(2026, 7, 1), date(2026, 7, 31))
CATEGORIES = frozenset({"RESTAURANTS", "LEISURE", "GROCERIES", "TRANSPORT_FUEL", "SALARY"})
SOMEBODY_ELSE = UUID(int=200)
# Three whole months, so a breakdown by month has the time axis a line chart needs.
SPRING = AbsoluteRange(date(2026, 5, 1), date(2026, 7, 31))

RESTAURANTS_IN_JULY = (
    Ask(PlanFields(direction=Direction.EXPENSE, window=JULY, categories=("RESTAURANTS",))),
)
AND_IN_LEISURE = (Patch("focus", PlanFields(categories=("LEISURE",))),)


def ran(*categories: str) -> FinancialPlan:
    """A July plan as the ledger saw it: across every account, the scope being every account."""
    return FinancialPlan(
        direction=Direction.EXPENSE, date_range=JULY, categories=categories, across_accounts=True
    )


class Scripted:
    """The interpreter, scripted: each call answers the next turn's operations, in order."""

    def __init__(self, *turns: tuple[Operation, ...]) -> None:
        self._turns = list(turns)
        self.seen: list[DialogueState] = []

    async def interpret(
        self,
        sentence: str,
        state: DialogueState,
        *,
        today: date,
        account_names: Mapping[UUID, str] = NO_ACCOUNT_NAMES,
    ) -> tuple[Operation, ...]:
        self.seen.append(state)
        return self._turns.pop(0)


class Canned:
    """A model, for the real interpreter adapter: canned answers in order, no network."""

    name = "canned"

    def __init__(self, *answers: object) -> None:
        self._answers = [a if isinstance(a, str) else json.dumps(a) for a in answers]

    async def complete(self, prompt: str, **_: object) -> str:
        return self._answers.pop(0)


class Refusing(Directory):
    """The episodes' account directory, refused by the bank while `refusing` is set."""

    refusing = False

    async def all_of(self, *_: object, **__: object) -> tuple[Holding, ...]:
        if self.refusing:
            raise QueryRejectedError("accounts")
        return await super().all_of()


class NoInvestigation:
    """These conversations never compare by merchant, so nothing should be assessed."""

    async def assess(self, _: object) -> object:
        raise AssertionError("this conversation needs no investigation")


def engine_on(
    stack: Stack,
    interpreter: object,
    *,
    directory: Directory | None = None,
    store: MemoryDialogueStore | None = None,
) -> GraphTurnHandler:
    """The graph as the composition root builds it, on the stack's own fakes and in memory. Given a
    `store`, it is a new process over the same database: nothing of the old graph survives but
    the canonical conversation."""
    engine = GraphTurnHandler(
        GraphPorts(
            interpreter,  # type: ignore[arg-type]
            NoInvestigation(),  # type: ignore[arg-type]
            store or MemoryDialogueStore(),
            stack.ledger,  # type: ignore[arg-type]
            Accounts(),  # type: ignore[arg-type]
            directory or Directory(),  # type: ignore[arg-type]
            Contacts(),  # type: ignore[arg-type]
            CATEGORIES,
            visual_policy=VisualPlanningPolicy(
                model=None,
                fallback=DeterministicVisualPlanner(),
                verifier=verify,
                mode="deterministic",
                budget_seconds=1,
            ),
            count_calls=calls_so_far,
        ),
        MemoryTurnReservations(),
        InMemorySaver(),
    )
    app.state.engine = engine
    return engine


@dataclass
class Turn:
    status: int
    headers: Mapping[str, str]
    # The answer, read the same way on both routes; on a 409, the refusal's body.
    body: dict[str, Any]
    frames: list[dict[str, Any]]

    @property
    def conversation(self) -> dict[str, Any]:
        conversation: dict[str, Any] = self.body["conversation"]
        return conversation

    @property
    def engine(self) -> tuple[str, str | None]:
        """Who answered, and the model calls, or None where none is reported (M-5)."""
        return self.headers["x-finguard-engine"], self.headers.get("x-finguard-model-calls")


def post(
    stack: Stack, route: str, question: str, conversation: dict[str, Any] | None = None
) -> Turn:
    # A graph turn is durable and needs the caller's own attempt key; a fresh one per call, so
    # every post is a new turn and never the replay of an earlier one.
    response = stack.client.post(
        route,
        json={"question": question, "conversation": conversation},
        headers={"Idempotency-Key": str(uuid.uuid4())},
    )
    if route == STREAM_ROUTE and response.status_code == 200:
        frames = [
            json.loads(line.removeprefix("data: "))
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        body = {key: value for key, value in frames[-1].items() if key != "type"}
        return Turn(response.status_code, response.headers, body, frames)
    return Turn(response.status_code, response.headers, response.json(), [])


@pytest.fixture(autouse=True)
def _restore(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    # `app` outlives every test: the engine one test switches on must not answer the next.
    monkeypatch.setattr(app.state, "engine", None, raising=False)
    monkeypatch.setattr(app.state, "metrics_enabled", False, raising=False)
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def test_every_provider_the_app_builds_is_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    """Through `create_app`, because what could break is the wiring. The graph itself is built in
    the lifespan, once PostgreSQL is open, so before it there is no engine to answer at all."""
    monkeypatch.setenv("ORCHESTRATOR_ENGINE_MODEL", "an-engine-model")
    get_settings.cache_clear()
    try:
        built = create_app()
    finally:
        get_settings.cache_clear()

    assert isinstance(built.state.planner_probe, CountingProvider)
    assert built.state.engine is None


# --- the engine answers -------------------------------------------------------------------------


@pytest.mark.parametrize("route", ROUTES)
def test_engine_answers_a_query_over_http(route: str) -> None:
    stack = a_stack()
    interpreter = Scripted(RESTAURANTS_IN_JULY)
    engine_on(stack, interpreter)

    turn = post(stack, route, "¿cuánto gasté en restaurantes en julio?")

    assert turn.status == 200
    assert turn.body["outcome"] == "PLANNED"
    assert turn.body["text"].startswith("465,56 € en restaurantes del 2026-07-01 al 2026-07-31")
    assert turn.body["plan"] == "del 2026-07-01 al 2026-07-31, gastos, restaurantes"
    assert stack.ledger.plans == [ran("RESTAURANTS")]
    assert UUID(turn.conversation["conversation_id"])
    assert turn.conversation["version"] == 1
    assert turn.engine == ("model", "0")
    if route == STREAM_ROUTE:
        assert [frame["type"] for frame in turn.frames] == ["stage", "answer"]
        assert turn.frames[0]["name"] == "interpreting"


@pytest.mark.parametrize("route", ROUTES)
def test_a_delegation_is_said_and_the_focus_survives(route: str) -> None:
    """An operation the graph has no node for is answered in words, never sent to a second
    planner. The graph remembers it said so, and the focus is where it was: the next follow-up
    still moves July's restaurants."""
    stack = a_stack()
    interpreter = Scripted(RESTAURANTS_IN_JULY, (Delegate("UNSUPPORTED"),), AND_IN_LEISURE)
    engine_on(stack, interpreter)

    asked = post(stack, route, "¿cuánto gasté en restaurantes en julio?")
    engine_id = asked.conversation["conversation_id"]
    delegated = post(stack, route, "¿cuánto gasté este mes y cuánto ingresé?", asked.conversation)

    assert delegated.status == 200
    assert delegated.body["outcome"] == "UNSUPPORTED"
    assert "Todavía no puedo resolver esa operación" in delegated.body["text"]
    assert (delegated.conversation["conversation_id"], delegated.conversation["version"]) == (
        engine_id,
        2,
    )
    assert delegated.engine[0] == "model"
    assert stack.ledger.plans == [ran("RESTAURANTS")]

    patched = post(stack, route, "¿y en ocio?", delegated.conversation)
    assert patched.body["outcome"] == "PLANNED"
    assert patched.body["text"].startswith("465,56 € en ocio del 2026-07-01 al 2026-07-31")
    assert interpreter.seen[-1].focus == QueryFocus(
        FinancialPlan(direction=Direction.EXPENSE, date_range=JULY, categories=("RESTAURANTS",))
    )
    assert stack.ledger.plans == [ran("RESTAURANTS"), ran("LEISURE")]
    assert (patched.conversation["conversation_id"], patched.conversation["version"]) == (
        engine_id,
        3,
    )


def _a_payment_preview_on_the_wire(conversation_id: UUID) -> dict[str, Any]:
    return conversation_mapper.to_payload(
        Conversation(
            conversation_id=conversation_id,
            version=3,
            active=PaymentConfirmation(
                intent_id=INTENT,
                recipient="Aitor Beltran Diaz",
                amount_minor=1_200,
                rail=Rail.BIZUM,
            ),
        )
    ).model_dump(mode="json")


@pytest.mark.parametrize("route", ROUTES)
def test_a_payment_workflow_the_graph_does_not_own_is_refused(route: str) -> None:
    """A preview an older client still holds from the retired coordinator: a stale «sí» must not
    confirm anything, so it is refused in words before the graph is even invoked."""
    stack = a_stack()
    preview = _a_payment_preview_on_the_wire(UUID(int=801))

    class Graph:
        async def owns_workflow(self, *_: object, **__: object) -> bool:
            return False

        async def answer(self, *_: object, **__: object) -> object:
            raise AssertionError("a workflow the graph does not own must be refused before it")

    app.state.engine = Graph()
    refused = post(stack, route, "sí", preview)

    assert refused.status == 200
    assert refused.body["outcome"] == "INVALID_DOMAIN"
    assert "No puedo continuarla ni ejecutarla" in refused.body["text"]
    # The answer keeps the conversation's id and version and carries no workflow back: there is
    # nothing left on the wire a client could confirm.
    assert (refused.conversation["conversation_id"], refused.conversation["version"]) == (
        str(UUID(int=801)),
        3,
    )
    assert refused.conversation["workflow"] is None
    assert stack.bank.confirmed == []
    assert refused.engine == ("model", "0")


@pytest.mark.parametrize("route", ROUTES)
def test_the_routes_fail_closed_if_the_graph_is_missing(route: str) -> None:
    stack = a_stack()
    app.state.engine = None

    turn = post(stack, route, "¿cuánto gasté este mes?")

    assert turn.status == 503
    assert "not available" in turn.body["detail"]
    assert stack.ledger.ran == []


@pytest.mark.parametrize("route", ROUTES)
def test_the_graph_resumes_its_own_payment_confirmation(route: str) -> None:
    stack = a_stack()
    conversation_id = UUID(int=800)
    payload = _a_payment_preview_on_the_wire(conversation_id)

    class Graph:
        async def owns_workflow(
            self, received_id: UUID, *, account_id: UUID, workflow: Workflow
        ) -> bool:
            assert received_id == conversation_id
            assert account_id == ACCOUNT
            assert workflow is Workflow.PAYMENT_CONFIRMATION
            return True

        async def answer(self, *_: object, **kwargs: object) -> EngineAnswer:
            assert kwargs["conversation_id"] == conversation_id
            assert kwargs["version"] == 3
            assert kwargs["account_id"] == ACCOUNT
            return EngineAnswer(
                AskFinancialQuestionResult(
                    outcome=PaymentOutcome.CANCELLED.value,
                    text="Vale, no he enviado nada.",
                ),
                DialogueState(conversation_id, version=4),
                None,
                0,
                {},
            )

    app.state.engine = Graph()
    refused = post(stack, route, "no", payload)

    assert refused.status == 200
    assert refused.body["outcome"] == PaymentOutcome.CANCELLED.value
    assert refused.conversation["workflow"] is None


@pytest.mark.parametrize("route", ROUTES)
def test_stale_version_is_409_with_current_state(route: str) -> None:
    """Review Focus 2 on the wire: a double submit is refused with the holder's own state."""
    stack = a_stack()
    interpreter = Scripted(RESTAURANTS_IN_JULY, AND_IN_LEISURE)
    engine_on(stack, interpreter)
    first = post(stack, route, "¿cuánto gasté en restaurantes en julio?")
    second = post(stack, route, "¿y en ocio?", first.conversation)

    again = post(stack, route, "¿y en ocio?", first.conversation)

    assert again.status == 409
    assert again.body["code"] == "STALE_CONVERSATION"
    assert again.body["conversation"]["conversation_id"] == second.conversation["conversation_id"]
    assert again.body["conversation"]["version"] == 2
    # Refused before anything ran.
    assert len(interpreter.seen) == 2 and len(stack.ledger.plans) == 2
    assert again.engine == ("model", "0")


@pytest.mark.parametrize("route", ROUTES)
def test_another_holders_id_names_nothing_of_theirs(route: str) -> None:
    """Ruling R20 on the wire: the store is keyed by account, so someone else's conversation id
    names an empty conversation of one's own and never theirs."""
    stack = a_stack()
    interpreter = Scripted(RESTAURANTS_IN_JULY, AND_IN_LEISURE, AND_IN_LEISURE)
    engine_on(stack, interpreter)
    theirs = post(stack, route, "¿cuánto gasté en restaurantes en julio?")
    holder = app.dependency_overrides[authenticated_caller]
    app.dependency_overrides[authenticated_caller] = lambda: Caller(
        holder_reference="ES-0002",
        account_id=SOMEBODY_ELSE,
        scopes=frozenset({Scope.QUESTIONS_READ}),
    )

    # With their version: stale against the empty state this account holds under that id, and
    # the 409 hands back that empty state, not theirs. Refused before anything ran.
    stale = post(stack, route, "¿y en ocio?", theirs.conversation)
    assert stale.status == 409
    assert stale.body["conversation"]["version"] == 0
    assert len(interpreter.seen) == 1

    # Without one: read against an empty state of one's own.
    mine = post(
        stack,
        route,
        "¿y en ocio?",
        {"conversation_id": theirs.conversation["conversation_id"]},
    )
    assert mine.status == 200
    assert mine.conversation["version"] == 1
    assert (interpreter.seen[-1].focus, interpreter.seen[-1].turns) == (None, ())
    assert stack.ledger.plans == [ran("RESTAURANTS")]

    # The holder's own conversation is exactly where they left it.
    app.dependency_overrides[authenticated_caller] = holder
    continued = post(stack, route, "¿y en ocio?", theirs.conversation)
    assert continued.conversation["version"] == 2
    assert stack.ledger.plans == [ran("RESTAURANTS"), ran("LEISURE")]


@pytest.mark.parametrize("route", ROUTES)
def test_an_unknown_id_without_a_version_is_adopted(route: str) -> None:
    """An explicit id is part of the conversation's identity: one the graph holds nothing of is
    started under that same id, never silently forked under another."""
    stack = a_stack()
    interpreter = Scripted(RESTAURANTS_IN_JULY, AND_IN_LEISURE)
    engine_on(stack, interpreter)
    unknown = "00000000-0000-4000-8000-000000004242"

    first = post(
        stack, route, "¿cuánto gasté en restaurantes en julio?", {"conversation_id": unknown}
    )
    second = post(stack, route, "¿y en ocio?", first.conversation)

    assert first.conversation["conversation_id"] == unknown
    assert first.conversation["version"] == 1
    assert second.conversation["conversation_id"] == unknown
    assert second.conversation["version"] == 2
    assert stack.ledger.plans == [ran("RESTAURANTS"), ran("LEISURE")]


@pytest.mark.parametrize("route", ROUTES)
def test_a_rejected_directory_read_is_answered_honestly_and_saves_nothing(route: str) -> None:
    stack = a_stack()
    directory = Refusing()
    interpreter = Scripted(RESTAURANTS_IN_JULY, AND_IN_LEISURE)
    engine_on(stack, interpreter, directory=directory)
    first = post(stack, route, "¿cuánto gasté en restaurantes en julio?")
    directory.refusing = True

    refused = post(stack, route, "¿y en ocio?", first.conversation)

    assert refused.status == 200
    assert refused.body["outcome"] == "INVALID_DOMAIN"
    assert "el banco ha rechazado" in refused.body["text"]
    assert refused.conversation["conversation_id"] == first.conversation["conversation_id"]
    assert refused.conversation["version"] == 1
    assert refused.engine[0] == "model"
    assert len(interpreter.seen) == 1

    # Nothing was saved: the same version answers once the bank reads again.
    directory.refusing = False
    retried = post(stack, route, "¿y en ocio?", refused.conversation)
    assert retried.conversation["version"] == 2
    assert stack.ledger.plans == [ran("RESTAURANTS"), ran("LEISURE")]


def test_a_rejected_directory_read_does_not_claim_the_sentence_was_understood() -> None:
    # The directory and the contacts are read before the sentence is: the interpreter is never
    # asked, so the answer must not say it understood anything.
    stack = a_stack()
    directory = Refusing()
    interpreter = Scripted(RESTAURANTS_IN_JULY)
    engine_on(stack, interpreter, directory=directory)
    directory.refusing = True

    refused = post(stack, JSON_ROUTE, "¿cuánto gasté en restaurantes en julio?")

    assert interpreter.seen == []
    assert "He entendido" not in refused.body["text"]


# --- what each response says about itself -------------------------------------------------------

ASKS_RESTAURANTS_IN_JULY = {
    "operations": [
        {
            "ask": [
                {"direction": "EXPENSE"},
                {"result_kind": "SCALAR"},
                {"categories": ["RESTAURANTS"]},
                {"window": {"range": {"start": "2026-07-01", "end": "2026-07-31"}}},
            ]
        }
    ]
}
DELEGATES_IT = {"operations": [{"delegate": "UNSUPPORTED"}]}


class Hosted(Canned):
    """A canned model that reports what a hosted provider does: each logical call is one HTTP
    attempt to a named model, with the usage the provider returned."""

    reports_provider_requests = True

    async def complete(self, prompt: str, **kwargs: object) -> str:
        record_provider_request("a-model", self.name)
        answer = await super().complete(prompt, **kwargs)
        record_successful_model("a-model")
        record_usage(120, 30)
        return answer


def an_adapter(provider: Canned) -> LlmTurnOperations:
    """The real interpreter adapter, over a canned model wrapped as the composition root wraps
    every provider it builds."""
    return LlmTurnOperations(
        CountingProvider(provider),
        model="a-model",
        temperature=0.0,
        max_output_tokens=600,
        categories={key: category_words.name_of(key) for key in sorted(CATEGORIES)},
    )


@pytest.mark.parametrize("route", ROUTES)
def test_headers_report_engine_and_model_calls(route: str) -> None:
    stack = a_stack()
    engine_on(stack, an_adapter(Canned(ASKS_RESTAURANTS_IN_JULY, DELEGATES_IT)))

    answered = post(stack, route, "¿cuánto gasté en restaurantes en julio?")
    delegated = post(
        stack, route, "¿cuánto gasté este mes y cuánto ingresé?", answered.conversation
    )

    assert answered.body["outcome"] == "PLANNED"
    assert answered.engine == ("model", "1")
    # A delegation still read the sentence once: that call is this turn's, answered or not. The
    # graph answers before the stream starts, so both routes carry the exact count.
    assert delegated.body["outcome"] == "UNSUPPORTED"
    assert delegated.engine == ("model", "1")
    # A local model reports no usage, and missing usage is left absent, never reported as zero.
    assert "x-finguard-prompt-tokens" not in answered.headers


@pytest.mark.parametrize("route", ROUTES)
def test_headers_report_provider_requests_models_and_tokens(route: str) -> None:
    stack = a_stack()
    engine_on(stack, an_adapter(Hosted(ASKS_RESTAURANTS_IN_JULY)))

    turn = post(stack, route, "¿cuánto gasté en restaurantes en julio?")

    assert turn.body["outcome"] == "PLANNED"
    assert {
        name: turn.headers.get(f"x-finguard-{name}")
        for name in (
            "model-calls",
            "provider-requests",
            "models-attempted",
            "models-used",
            "prompt-tokens",
            "completion-tokens",
            "token-reports",
        )
    } == {
        "model-calls": "1",
        "provider-requests": "1",
        "models-attempted": "a-model",
        "models-used": "a-model",
        "prompt-tokens": "120",
        "completion-tokens": "30",
        "token-reports": "1",
    }


async def test_calls_made_in_tasks_the_request_spawned_are_the_requests() -> None:
    """Why the tally is an object and not an int: a call in a gathered task runs in a copy of the
    request's context, and only a shared object carries it back."""
    provider = CountingProvider(Canned("a", "b"))

    with counting_calls() as calls:
        await asyncio.gather(
            provider.complete("one", model="m", temperature=0.0, max_output_tokens=8),
            provider.complete("two", model="m", temperature=0.0, max_output_tokens=8),
        )
        assert calls.count == calls_so_far() == 2

    assert calls_so_far() == 0


async def test_outside_a_request_nothing_is_counted() -> None:
    provider = CountingProvider(Canned("a"))

    assert await provider.complete("x", model="m", temperature=0.0, max_output_tokens=8) == "a"
    assert calls_so_far() == 0
    assert provider.name == "canned"


@pytest.mark.parametrize("route", ROUTES)
def test_a_forgotten_conversation_is_a_409_the_client_can_adopt(route: str) -> None:
    """After a restart the store holds nothing: the version an open tab sends is stale against the
    empty state, and the 409 hands back that state under the same id, so the next turn adopts it
    instead of a 409 on every turn."""
    stack = a_stack()
    engine_on(stack, Scripted(RESTAURANTS_IN_JULY))
    first = post(stack, route, "¿cuánto gasté en restaurantes en julio?")
    engine_on(stack, Scripted(RESTAURANTS_IN_JULY))  # a new process: an empty store

    stale = post(stack, route, "¿cuánto gasté en restaurantes en julio?", first.conversation)
    again = post(
        stack, route, "¿cuánto gasté en restaurantes en julio?", stale.body["conversation"]
    )

    assert stale.status == 409
    assert stale.body["conversation"]["conversation_id"] == first.conversation["conversation_id"]
    assert stale.body["conversation"]["version"] == 0
    assert again.status == 200
    assert again.conversation["conversation_id"] == first.conversation["conversation_id"]
    assert again.conversation["version"] == 1


def test_graph_visual_operations_render_and_restyle_over_http_without_bank_reads() -> None:
    stack = a_stack()
    stack.ledger.groups_for_next_turn = (
        Group("2026-05", 30_000, 3),
        Group("2026-06", 18_000, 2),
        Group("2026-07", 9_000, 1),
    )
    engine_on(
        stack,
        Scripted(
            (Ask(PlanFields(direction=Direction.EXPENSE, window=SPRING, group_by=GroupBy.MONTH)),),
            (Visualize("ACTIVE_RESULT", "AUTO"),),
            (Visualize("ACTIVE_CHART", "BAR"),),
        ),
    )

    queried = post(stack, JSON_ROUTE, "desglosa mis gastos por meses de mayo a julio")
    bank_reads = len(stack.ledger.ran)
    created = post(stack, JSON_ROUTE, "enséñamelo en un gráfico", queried.conversation)
    restyled = post(stack, JSON_ROUTE, "cámbialo a barras", created.conversation)

    assert queried.status == created.status == restyled.status == 200
    assert created.body["visualization"]["kind"] == "chart"
    # Months are a time axis: the deterministic planner draws them as a line.
    assert created.body["visualization"]["spec"]["mark"]["type"] == "line"
    assert restyled.body["visualization"]["kind"] == "chart"
    assert restyled.body["visualization"]["spec"]["mark"]["type"] == "bar"
    assert created.conversation["version"] == 2
    assert restyled.conversation["version"] == 3
    assert len(stack.ledger.ran) == bank_reads
    assert created.engine[0] == restyled.engine[0] == "model"


def test_graph_payment_confirmation_state_survives_the_http_mapping() -> None:
    conversation_id = UUID(int=501)
    intent_id = UUID(int=502)
    result = AskFinancialQuestionResult(
        outcome=PaymentOutcome.AWAITING_CONFIRMATION.value,
        text="Vas a enviar 12,00 € a Aitor Beltran Diaz. ¿Confirmas?",
        conversation=Conversation(
            conversation_id=conversation_id,
            version=4,
            active=PaymentConfirmation(
                intent_id=intent_id,
                recipient="Aitor Beltran Diaz",
                amount_minor=1_200,
                rail=Rail.BIZUM,
            ),
        ),
    )

    answer = engine_dispatch._on_the_wire(  # noqa: SLF001
        result,
        Conversation(conversation_id=conversation_id, version=4),
        Caller("holder", UUID(int=503)),
        MetricTokenCodec({"test": b"x" * 32}, "test"),
    )

    assert answer.conversation is not None
    assert answer.conversation.workflow == "PAYMENT_CONFIRMATION"
    assert answer.conversation.intent_id == intent_id
    assert answer.conversation.recipient == "Aitor Beltran Diaz"
    assert answer.conversation.amount_minor == 1_200
    assert answer.conversation.conversation_id == conversation_id
    assert answer.conversation.version == 4


def test_there_is_no_parallel_payment_route() -> None:
    """Payments go through the conversation alone, from the chat or from a form's typed action: a
    second way to prepare one would be a workflow the graph never sees."""
    stack = a_stack()

    response = stack.client.post(
        "/v1/payments/chosen",
        headers={"Idempotency-Key": "parallel-workflow-is-gone"},
        json={"recipient_name": "Marta Solis Vega", "amount_minor": 2_000, "currency": "EUR"},
    )

    assert response.status_code == 404
    assert stack.bank.prepared == []


@pytest.mark.parametrize("field", ["engine_temperature", "engine_max_output_tokens"])
def test_the_engines_sampling_refuses_a_negative(field: str) -> None:
    """M-7: refused when the settings load, by name, rather than by the provider mid-turn."""
    with pytest.raises(ValidationError, match=field):
        Settings(**{field: -1})  # type: ignore[arg-type]


FOOD_LAST_MONTH = {
    "operations": [
        {
            "ask": [
                {"direction": "EXPENSE"},
                {"window": {"named": "LAST_MONTH"}},
                {"result_kind": "SCALAR"},
                {"categories": ["RESTAURANTS", "GROCERIES"]},
            ]
        }
    ]
}


def patched(target: str, **changes: object) -> dict[str, Any]:
    return {
        "operations": [
            {
                "patch": {
                    "target": target,
                    "changes": [{name: value} for name, value in changes.items()],
                    "task_id": None,
                }
            }
        ]
    }


@pytest.mark.parametrize("route", ROUTES)
def test_a_clarification_completes_the_query_it_asked_about_across_a_restart(route: str) -> None:
    """«Los gastos en restaurantes» names no period, so the turn asks for one and keeps what it
    understood. After a restart «el mes pasado» completes that same query: last month's
    restaurant spending, with nothing asked again."""
    stack = a_stack()
    store = MemoryDialogueStore()
    restaurants = {
        "operations": [
            {
                "ask": [
                    {"direction": "EXPENSE"},
                    {"result_kind": "SCALAR"},
                    {"categories": ["RESTAURANTS"]},
                ]
            }
        ]
    }
    engine_on(stack, an_adapter(Canned(restaurants)), store=store)
    asked = post(stack, route, "Los gastos en restaurantes")

    assert asked.status == 200
    assert asked.body["outcome"] == "NEEDS_CLARIFICATION"
    assert "restaurantes" in asked.body["text"]
    assert stack.ledger.ran == []

    last_month = patched("pending", window={"named": "LAST_MONTH"})
    engine_on(stack, an_adapter(Canned(last_month)), store=store)  # a new process
    answered = post(stack, route, "El mes pasado", asked.conversation)

    assert answered.status == 200
    assert answered.body["outcome"] != "NEEDS_CLARIFICATION", answered.body["text"]
    (ran,) = stack.ledger.plans
    assert ran.direction is Direction.EXPENSE
    assert ran.period == RelativePeriod(Period.LAST_MONTH)
    assert ran.categories == ("RESTAURANTS",)


@pytest.mark.parametrize("route", ROUTES)
def test_a_complete_question_after_a_piggy_bank_list_runs_on_every_account(route: str) -> None:
    """CONV-004: after the Hucha's latest movements, «¿Cuánto me gasté en comida y restaurantes el
    mes pasado?» is a new question. The model infers groceries from «comida»; no task on the
    table holds it, so nothing is suspect and it runs, across every account, naming both."""
    stack = a_stack()
    hucha = {"operations": [{"list_movements": {"accounts": ["Hucha"], "page_size": 20}}]}
    engine_on(stack, an_adapter(Canned(hucha, FOOD_LAST_MONTH)))
    listed = post(stack, route, "¿Me puedes enseñar los últimos movimientos en la Hucha?")
    asked = post(
        stack,
        route,
        "¿Cuánto me gasté en comida y restaurantes el mes pasado?",
        listed.conversation,
    )

    assert asked.status == 200
    assert asked.body["outcome"] != "NEEDS_CLARIFICATION", asked.body["text"]
    (ran,) = stack.ledger.plans
    assert ran.direction is Direction.EXPENSE
    assert ran.period == RelativePeriod(Period.LAST_MONTH)
    assert set(ran.categories) == {"RESTAURANTS", "GROCERIES"}
    assert ran.across_accounts
    assert "restaurantes" in asked.body["text"] and "supermercado" in asked.body["text"]


@pytest.mark.parametrize("route", ROUTES)
def test_one_conversation_keeps_each_field_it_was_told(route: str) -> None:
    """The review's five turns, in one conversation and never reset. «comida» runs as the model
    reads it; «de restaurantes», «¿y en supermercados?» and «ahora en mi cuenta corriente» each
    move one field and keep last month; «¿y los últimos movimientos?» starts a list that inherits
    none of those filters."""
    stack = a_stack()
    engine_on(
        stack,
        an_adapter(
            Canned(
                FOOD_LAST_MONTH,
                patched("focus", categories=["RESTAURANTS"]),
                patched("focus", categories=["GROCERIES"]),
                patched("focus", accounts=["Cuenta corriente"]),
                {"operations": [{"list_movements": {"page_size": 20}}]},
            )
        ),
    )
    conversation = None
    answers = []
    for said in (
        "Los gastos en comida el mes pasado",
        "De restaurantes",
        "¿Y en supermercados?",
        "Ahora en mi cuenta corriente",
        "¿Y los últimos movimientos?",
    ):
        turn = post(stack, route, said, conversation)
        assert turn.status == 200, turn.body
        assert turn.body["outcome"] != "NEEDS_CLARIFICATION", (said, turn.body["text"])
        conversation = turn.conversation
        answers.append(turn.body)

    food, restaurants, groceries, current = stack.ledger.plans
    for plan in (food, restaurants, groceries, current):
        assert plan.direction is Direction.EXPENSE
        assert plan.period == RelativePeriod(Period.LAST_MONTH)
    assert set(food.categories) == {"RESTAURANTS", "GROCERIES"}
    assert restaurants.categories == ("RESTAURANTS",)
    assert groceries.categories == current.categories == ("GROCERIES",)
    assert food.across_accounts and restaurants.across_accounts and groceries.across_accounts
    assert not current.across_accounts
    assert "Cuenta corriente" in answers[3]["text"]
    (latest,) = stack.ledger.listed
    assert latest.categories == () and latest.merchant_terms == ()
    assert latest.period is None and latest.date_from is None
    assert latest.direction is Direction.ALL


@pytest.mark.parametrize("route", ROUTES)
def test_money_moved_to_the_hucha_is_a_query_of_own_transfers(route: str) -> None:
    """«¿Cuánto transferí a la hucha este año?» answered «no movements» while 300 € went to the
    Hucha every month: no query could ask for transfers between the holder's own accounts. The
    model can now say so, and banking is asked for exactly those."""
    stack = a_stack()
    to_the_hucha = {
        "operations": [
            {
                "ask": [
                    {"direction": "INCOME"},
                    {"window": {"named": "THIS_YEAR"}},
                    {"accounts": ["Hucha"]},
                    {"result_kind": "SCALAR"},
                    {"own_transfers": True},
                ]
            }
        ]
    }
    engine_on(stack, an_adapter(Canned(to_the_hucha)))

    asked = post(stack, route, "¿Cuánto transferí a la hucha este año?")

    assert asked.status == 200
    assert asked.body["outcome"] != "NEEDS_CLARIFICATION", asked.body["text"]
    (ran,) = stack.ledger.plans
    assert ran.internal_only and ran.include_internal
    assert ran.direction is Direction.INCOME
    assert ran.period == RelativePeriod(Period.THIS_YEAR)
