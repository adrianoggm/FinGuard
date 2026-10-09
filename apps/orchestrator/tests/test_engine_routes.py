"""The model-led engine behind `ORCHESTRATOR_CONVERSATION_ENGINE`, on the two question routes.

Driven over HTTP on the episodes stack: its fake ledger, accounts, directory and contacts, the real
payment path behind it, and an interpreter that is either scripted (it returns the operations a
test gives it) or the real adapter over a canned model. What is under test is the seam: which turns
the engine answers, which it hands to the rules route, what travels on the wire between them, and
what each response says about who answered and how many model calls it cost.
"""

import asyncio
import hashlib
import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import ValidationError
from support.episodes import (
    INTENT,
    Accounts,
    Contacts,
    Directory,
    Stack,
    a_stack,
)

from finguard_orchestrator.application.engine.answer_turn import AnswerTurnHandler
from finguard_orchestrator.application.graph.graph import GraphPorts
from finguard_orchestrator.application.graph.graph_turn import GraphTurnHandler
from finguard_orchestrator.bootstrap.config import Settings, get_settings
from finguard_orchestrator.bootstrap.dependencies import authenticated_caller
from finguard_orchestrator.bootstrap.main import app, create_app
from finguard_orchestrator.domain.engine.operations import (
    Ask,
    Delegate,
    Operation,
    Patch,
    PlanFields,
    Visualize,
)
from finguard_orchestrator.domain.engine.state import ComparisonFocus, DialogueState, QueryFocus
from finguard_orchestrator.domain.enums import Direction, GroupBy
from finguard_orchestrator.domain.errors import QueryRejectedError
from finguard_orchestrator.domain.models.account_summary import Holding
from finguard_orchestrator.domain.models.conversation import Conversation, FinancialClarification
from finguard_orchestrator.domain.models.query_answer import Group
from finguard_orchestrator.domain.ports.turn_operations import NO_ACCOUNT_NAMES
from finguard_orchestrator.domain.services import category_words
from finguard_orchestrator.domain.services.deterministic_visual_planner import (
    DeterministicVisualPlanner,
)
from finguard_orchestrator.domain.services.visual_plan_verifier import verify
from finguard_orchestrator.domain.services.visual_planning_policy import VisualPlanningPolicy
from finguard_orchestrator.domain.value_objects.caller import Caller
from finguard_orchestrator.domain.value_objects.comparison_plan import ComparisonPlan
from finguard_orchestrator.domain.value_objects.financial_plan import (
    AbsoluteRange,
    FinancialPlan,
    FinancialPlanDraft,
)
from finguard_orchestrator.domain.value_objects.scope import Scope
from finguard_orchestrator.infrastructure.controllers import engine_dispatch
from finguard_orchestrator.infrastructure.dto.llm_dto import QuestionRequest
from finguard_orchestrator.infrastructure.engine.llm_turn_operations import LlmTurnOperations
from finguard_orchestrator.infrastructure.engine.memory_dialogue_store import MemoryDialogueStore
from finguard_orchestrator.infrastructure.graph.memory_turn_reservations import (
    MemoryTurnReservations,
)
from finguard_orchestrator.infrastructure.llm.counting_provider import (
    CountingProvider,
    calls_so_far,
    counting_calls,
)
from finguard_orchestrator.infrastructure.mappers import conversation_mapper

JSON_ROUTE = "/v1/questions"
STREAM_ROUTE = "/v1/questions/stream"
ROUTES = (JSON_ROUTE, STREAM_ROUTE)
JULY = AbsoluteRange(date(2026, 7, 1), date(2026, 7, 31))
CATEGORIES = frozenset({"RESTAURANTS", "LEISURE", "GROCERIES", "TRANSPORT_FUEL", "SALARY"})
SOMEBODY_ELSE = UUID(int=200)

RESTAURANTS_IN_JULY = (
    Ask(PlanFields(direction=Direction.EXPENSE, window=JULY, categories=("RESTAURANTS",))),
)
AND_IN_LEISURE = (Patch("focus", PlanFields(categories=("LEISURE",))),)
TO_PAYMENTS = (Delegate("PAYMENT"),)


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


def engine_on(
    stack: Stack, interpreter: object, *, directory: Directory | None = None
) -> AnswerTurnHandler:
    """The engine as the composition root builds it under `model`, on the stack's own fakes."""
    engine = AnswerTurnHandler(
        interpreter,  # type: ignore[arg-type]
        MemoryDialogueStore(),
        stack.ledger,
        Accounts(),  # type: ignore[arg-type]
        directory or Directory(),
        Contacts(),
        CATEGORIES,
        count_calls=calls_so_far,
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
    response = stack.client.post(route, json={"question": question, "conversation": conversation})
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


# --- the flag off ------------------------------------------------------------------------------

# What both routes answered at f4f82e1c, before any of the engine's wiring existed: the same three
# turns, one conversation, the same stack. A body that moves by one byte with the engine off is a
# change to production, which the flag exists to rule out. Re-pin only for a deliberate change to
# the rules route itself, never for one made in the engine's name.
FIXED_ID = "00000000-0000-4000-8000-00000000c0de"
RULES_TURNS = (
    "¿cuánto gasté en restaurantes en julio?",
    "¿y en ocio?",
    "hazle un bizum de 20 a Marta Solis",
)
PINNED = {
    JSON_ROUTE: (
        "7685e4ba36b811fd7a6b6a8d446b064bbbe9da2f8994c54da564b0552f0c8eca",
        "8be8cf06b60ed88318c50e272c7090151af17e07c8810806950bbfe2f1b6a3b5",
        "6abfcea537c7eac7b8933ba5562888724a6b15b8842869c0e8f199943b596165",
    ),
    STREAM_ROUTE: (
        "3bb238b152ca6139d0a88ce659d024d9116be9703b8c8dab1d702512edee2a07",
        "e3de225584bb535dff30c57b1df24f2c44dbf5b2068825620bbf2543af331f27",
        "372f7739fe32578facb5e684bd7022f9a3f986ec4493a35c74cca89f3d9f8119",
    ),
}


@pytest.mark.parametrize("route", ROUTES)
def test_flag_off_keeps_rules_byte_identical(route: str) -> None:
    stack = a_stack(account_scope_default="all")
    conversation: dict[str, Any] = {"conversation_id": FIXED_ID}

    for sentence, pinned in zip(RULES_TURNS, PINNED[route], strict=True):
        body: dict[str, Any] = {"question": sentence, "conversation": conversation}
        if route == STREAM_ROUTE:
            body["channel"] = "TEXT"
        response = stack.client.post(route, json=body)

        assert response.status_code == 200
        assert hashlib.sha256(response.content).hexdigest() == pinned, response.text
        # The stream's headers leave before the rules route's planner runs: no count rather
        # than a partial one (M-5).
        assert (
            response.headers["x-finguard-engine"],
            response.headers.get("x-finguard-model-calls"),
        ) == ("rules", None if route == STREAM_ROUTE else "0")
        frames = [
            json.loads(line.removeprefix("data: "))
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        conversation = frames[-1]["conversation"] if frames else response.json()["conversation"]
        assert "version" not in conversation


def test_the_rules_engine_is_what_ships() -> None:
    """Asked of the class and of `.env.defaults`, not of an instance, so a developer who has
    switched the engine on in their own `.env` does not fail a test about what the code ships."""
    assert Settings.model_fields["conversation_engine"].default == "rules"
    shipped = (Path(__file__).resolve().parents[1] / ".env.defaults").read_text(encoding="utf-8")
    assert "\nORCHESTRATOR_CONVERSATION_ENGINE=rules\n" in shipped


@pytest.mark.parametrize("chosen", ["rules", "model"])
def test_the_setting_decides_whether_an_engine_is_built(
    monkeypatch: pytest.MonkeyPatch, chosen: str
) -> None:
    """Through `create_app`, because what could break is the wiring."""
    monkeypatch.setenv("ORCHESTRATOR_CONVERSATION_ENGINE", chosen)
    monkeypatch.setenv("ORCHESTRATOR_ENGINE_MODEL", "")
    get_settings.cache_clear()
    try:
        built = create_app()
        settings = get_settings()
    finally:
        get_settings.cache_clear()

    engine = built.state.engine
    if chosen == "rules":
        assert engine is None
        return
    assert isinstance(engine, AnswerTurnHandler)
    interpreter = engine._interpreter  # noqa: SLF001
    assert isinstance(interpreter, LlmTurnOperations)
    # No engine model of its own: the planner's, on the planner's provider, counted.
    assert interpreter._model == settings.planner_model  # noqa: SLF001
    assert isinstance(interpreter._provider, CountingProvider)  # noqa: SLF001
    assert (interpreter._temperature, interpreter._max_output_tokens) == (  # noqa: SLF001
        settings.engine_temperature,
        settings.engine_max_output_tokens,
    )
    assert engine._count_calls is calls_so_far  # noqa: SLF001


def test_every_provider_the_app_builds_is_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORCHESTRATOR_ENGINE_MODEL", "an-engine-model")
    monkeypatch.setenv("ORCHESTRATOR_CONVERSATION_ENGINE", "model")
    get_settings.cache_clear()
    try:
        built = create_app()
    finally:
        get_settings.cache_clear()

    assert isinstance(built.state.planner_probe, CountingProvider)
    assert built.state.engine._interpreter._model == "an-engine-model"  # noqa: SLF001


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
    # The rules route never ran: its planner was not asked.
    assert stack.planner.asked == []
    assert UUID(turn.conversation["conversation_id"])
    assert turn.conversation["version"] == 1
    assert turn.engine == ("model", "0")
    if route == STREAM_ROUTE:
        assert [frame["type"] for frame in turn.frames] == ["stage", "answer"]
        assert turn.frames[0]["name"] == "interpreting"


@pytest.mark.parametrize("route", ROUTES)
def test_payment_is_delegated_and_focus_survives(route: str) -> None:
    """Review Focus 5: a payment in the middle of an engine conversation is the rules route's, and
    the engine's focus, its conversation id and its version are where they were afterwards."""
    stack = a_stack()
    interpreter = Scripted(RESTAURANTS_IN_JULY, TO_PAYMENTS, AND_IN_LEISURE)
    engine_on(stack, interpreter)

    asked = post(stack, route, "¿cuánto gasté en restaurantes en julio?")
    engine_id = asked.conversation["conversation_id"]
    previewed = post(stack, route, "hazle un bizum de 20 a Marta Solis Vega", asked.conversation)

    # The rules route's preview, carrying the engine's id and version untouched.
    assert previewed.body["outcome"] == "AWAITING_CONFIRMATION"
    assert previewed.conversation["workflow"] == "PAYMENT_CONFIRMATION"
    assert (previewed.conversation["conversation_id"], previewed.conversation["version"]) == (
        engine_id,
        1,
    )
    assert len(stack.bank.prepared) == 1
    assert previewed.engine[0] == "rules"

    # A money workflow on the wire owns its turn: the engine is not even asked.
    refused = post(stack, route, "no", previewed.conversation)
    assert refused.body["outcome"] == "CANCELLED"
    assert stack.bank.cancelled == [INTENT]
    assert refused.conversation["workflow"] is None
    assert (refused.conversation["conversation_id"], refused.conversation["version"]) == (
        engine_id,
        1,
    )
    assert refused.engine[0] == "rules"
    assert len(interpreter.seen) == 2

    # And the engine picks up where it left off: the patch moves July's subject, nothing else.
    patched = post(stack, route, "¿y en ocio?", refused.conversation)
    assert patched.body["outcome"] == "PLANNED"
    assert patched.body["text"].startswith("465,56 € en ocio del 2026-07-01 al 2026-07-31")
    assert interpreter.seen[-1].focus == QueryFocus(
        FinancialPlan(direction=Direction.EXPENSE, date_range=JULY, categories=("RESTAURANTS",))
    )
    assert stack.ledger.plans == [ran("RESTAURANTS"), ran("LEISURE")]
    assert (patched.conversation["conversation_id"], patched.conversation["version"]) == (
        engine_id,
        2,
    )
    # The engine's memory is its own, server-side: none of the rules route's rides beside it.
    assert (patched.conversation["last_plan"], patched.conversation["turns"]) == (None, [])
    assert patched.engine[0] == "model"


@pytest.mark.parametrize("route", ROUTES)
def test_authoritative_graph_never_falls_back_for_legacy_payment_workflow(route: str) -> None:
    """A stale payment preview must not reach the legacy confirmation handler in graph mode."""
    stack = a_stack()
    preview = post(stack, route, "hazle un bizum de 20 euros a Marta Solis Vega")
    assert preview.body["outcome"] == "AWAITING_CONFIRMATION"

    class AuthoritativeGraph:
        authoritative = True

        async def answer(self, *_: object, **__: object) -> object:
            raise AssertionError("legacy workflow must be refused before graph invocation")

    app.state.engine = AuthoritativeGraph()
    refused = post(stack, route, "sí", preview.conversation)

    assert refused.status == 200
    assert refused.body["outcome"] == "INVALID_DOMAIN"
    assert "No puedo continuarla ni ejecutarla" in refused.body["text"]
    assert refused.conversation["workflow"] == "PAYMENT_CONFIRMATION"
    assert refused.conversation["intent_id"] == preview.conversation["intent_id"]
    assert stack.bank.confirmed == []
    assert refused.engine == ("model", "0")


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
def test_another_holders_id_gets_a_fresh_conversation(route: str) -> None:
    """Ruling R20 on the wire: someone else's conversation id names nothing of theirs."""
    stack = a_stack()
    interpreter = Scripted(RESTAURANTS_IN_JULY, AND_IN_LEISURE, AND_IN_LEISURE, AND_IN_LEISURE)
    engine_on(stack, interpreter)
    theirs = post(stack, route, "¿cuánto gasté en restaurantes en julio?")
    holder = app.dependency_overrides[authenticated_caller]
    app.dependency_overrides[authenticated_caller] = lambda: Caller(
        holder_reference="ES-0002",
        account_id=SOMEBODY_ELSE,
        scopes=frozenset({Scope.QUESTIONS_READ}),
    )

    # With their version: a fresh conversation of one's own (R27), read against an empty state.
    fresh = post(stack, route, "¿y en ocio?", theirs.conversation)
    assert fresh.status == 200
    assert fresh.conversation["conversation_id"] != theirs.conversation["conversation_id"]
    assert fresh.conversation["version"] == 1
    assert (interpreter.seen[-1].focus, interpreter.seen[-1].turns) == (None, ())

    # Without one: a fresh conversation, read against an empty state.
    mine = post(
        stack,
        route,
        "¿y en ocio?",
        {"conversation_id": theirs.conversation["conversation_id"]},
    )
    assert mine.status == 200
    assert mine.conversation["conversation_id"] != theirs.conversation["conversation_id"]
    assert mine.conversation["version"] == 1
    assert (interpreter.seen[-1].focus, interpreter.seen[-1].turns) == (None, ())
    assert stack.ledger.plans == [ran("RESTAURANTS")]

    # The holder's own conversation is exactly where they left it.
    app.dependency_overrides[authenticated_caller] = holder
    continued = post(stack, route, "¿y en ocio?", theirs.conversation)
    assert continued.conversation["version"] == 2
    assert stack.ledger.plans == [ran("RESTAURANTS"), ran("LEISURE")]


@pytest.mark.parametrize("route", ROUTES)
def test_an_unknown_id_gets_a_new_id_that_the_client_adopts(route: str) -> None:
    """Never saved, forgotten by a restart, or minted by the rules route: a new conversation under
    a new id, which the next turn echoes and the engine then finds."""
    stack = a_stack()
    interpreter = Scripted(RESTAURANTS_IN_JULY, AND_IN_LEISURE)
    engine_on(stack, interpreter)
    unknown = "00000000-0000-4000-8000-000000004242"

    first = post(
        stack, route, "¿cuánto gasté en restaurantes en julio?", {"conversation_id": unknown}
    )
    second = post(stack, route, "¿y en ocio?", first.conversation)

    assert first.conversation["conversation_id"] != unknown
    assert first.conversation["version"] == 1
    assert second.conversation["conversation_id"] == first.conversation["conversation_id"]
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
    assert "He entendido" not in refused.body["text"]
    assert refused.conversation["conversation_id"] == first.conversation["conversation_id"]
    assert refused.conversation["version"] == 1
    assert refused.engine[0] == "model"
    assert len(interpreter.seen) == 1

    # Nothing was saved: the same version answers once the bank reads again.
    directory.refusing = False
    retried = post(stack, route, "¿y en ocio?", refused.conversation)
    assert retried.conversation["version"] == 2
    assert stack.ledger.plans == [ran("RESTAURANTS"), ran("LEISURE")]


# --- what each response says about itself -------------------------------------------------------

ASKS_RESTAURANTS_IN_JULY = {
    "operations": [
        {
            "ask": [
                {"direction": "EXPENSE"},
                {"categories": ["RESTAURANTS"]},
                {"window": {"range": {"start": "2026-07-01", "end": "2026-07-31"}}},
            ]
        }
    ]
}
DELEGATES_THE_PAYMENT = {"operations": [{"delegate": "PAYMENT"}]}


def an_adapter(*answers: object) -> LlmTurnOperations:
    """The real interpreter adapter, over a canned model wrapped as the composition root wraps
    every provider it builds."""
    return LlmTurnOperations(
        CountingProvider(Canned(*answers)),
        model="a-model",
        temperature=0.0,
        max_output_tokens=600,
        categories={key: category_words.name_of(key) for key in sorted(CATEGORIES)},
    )


@pytest.mark.parametrize("route", ROUTES)
def test_headers_report_engine_and_model_calls(route: str) -> None:
    stack = a_stack()
    engine_on(stack, an_adapter(ASKS_RESTAURANTS_IN_JULY, DELEGATES_THE_PAYMENT))

    answered = post(stack, route, "¿cuánto gasté en restaurantes en julio?")
    delegated = post(stack, route, "hazle un bizum de 20 a Marta Solis Vega", answered.conversation)

    assert answered.body["outcome"] == "PLANNED"
    assert answered.engine == ("model", "1")
    # The engine's call is counted on the turn the rules route went on to answer; on the stream
    # that route's own calls come after the headers, so no count is sent at all (M-5).
    assert delegated.body["outcome"] == "AWAITING_CONFIRMATION"
    assert delegated.engine == ("rules", None if route == STREAM_ROUTE else "1")


def test_the_rules_engine_reports_its_own_model_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Engine-agnostic: the planner's call goes through the same counted provider. Asserted on the
    JSON route, whose headers leave after the answer; the stream's leave before its first frame,
    and the rules route's planner runs inside the stream (see `engine_dispatch`)."""
    stack = a_stack()
    provider = CountingProvider(Canned("{}"))
    planned = stack.planner.plan

    async def plan_after_a_model_call(sentence: str, **context: Any) -> Any:
        await provider.complete("plan", model="m", temperature=0.0, max_output_tokens=8)
        return await planned(sentence, **context)

    monkeypatch.setattr(stack.planner, "plan", plan_after_a_model_call)

    turn = post(stack, JSON_ROUTE, "¿cuánto gasté en restaurantes en julio?")

    assert turn.body["outcome"] == "PLANNED"
    assert turn.engine == ("rules", "1")


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


# --- what a handed-over turn hands back ---------------------------------------------------------


@pytest.mark.parametrize("route", ROUTES)
def test_a_delegated_compound_read_keeps_the_engine_conversation(route: str) -> None:
    """R25: a compound read is the rules route's, and its later clauses are read in a context
    built for them; that context must still be the engine's conversation, or the next engine turn
    starts a new one."""
    stack = a_stack()
    interpreter = Scripted(RESTAURANTS_IN_JULY, (Delegate("UNSUPPORTED"),), AND_IN_LEISURE)
    engine_on(stack, interpreter)
    first = post(stack, route, "¿cuánto gasté en restaurantes en julio?")
    engine_id = first.conversation["conversation_id"]

    compound = post(stack, route, "Cuanto gaste este mes y cuanto ingrese?", first.conversation)
    followed = post(stack, route, "¿y en ocio?", compound.conversation)

    assert compound.body["outcome"] == "COMPOUND"
    assert compound.engine[0] == "rules"
    assert (compound.conversation["conversation_id"], compound.conversation["version"]) == (
        engine_id,
        1,
    )
    assert followed.body["outcome"] == "PLANNED"
    assert (followed.conversation["conversation_id"], followed.conversation["version"]) == (
        engine_id,
        2,
    )


# --- a workflow on the wire (R26) ---------------------------------------------------------------

DRAFT_KEYS = ("workflow", "payment", "candidates", "awaiting", "suspended")


@pytest.mark.parametrize("route", ROUTES)
def test_a_suspended_bizum_lets_the_engine_answer_and_rides_along(route: str) -> None:
    """A half-built Bizum the rules route set aside for a question keeps nothing else on that
    route: the engine answers the next analytics turn, and hands the draft back untouched so it
    can still be resumed there."""
    stack = a_stack()
    interpreter = Scripted(RESTAURANTS_IN_JULY, TO_PAYMENTS, AND_IN_LEISURE)
    engine_on(stack, interpreter)
    first = post(stack, route, "¿cuánto gasté en restaurantes en julio?")
    drafted = post(stack, route, "hazle un bizum de 20 a Marta", first.conversation)
    assert drafted.conversation["workflow"] == "PAYMENT_IN_PROGRESS"

    # Open and not set aside: still the payment's turn, on the rules route.
    aside = post(stack, route, "¿cuánto gasté en restaurantes en julio?", drafted.conversation)
    assert aside.engine[0] == "rules" and len(interpreter.seen) == 2
    assert aside.conversation["suspended"] is True

    answered = post(stack, route, "¿y en ocio?", aside.conversation)

    assert answered.engine[0] == "model"
    assert answered.body["text"].startswith("465,56 € en ocio del 2026-07-01 al 2026-07-31")
    assert answered.conversation["version"] == 2
    assert {key: answered.conversation[key] for key in DRAFT_KEYS} == {
        key: aside.conversation[key] for key in DRAFT_KEYS
    }


@pytest.mark.parametrize("route", ROUTES)
def test_an_open_rules_question_is_answered_by_the_rules_route(route: str) -> None:
    """M-1: a question the rules route asked is answered where it was asked."""
    stack = a_stack()
    interpreter = Scripted()
    engine_on(stack, interpreter)
    asked = conversation_mapper.to_payload(
        Conversation(
            conversation_id=UUID(int=77),
            version=3,
            active=FinancialClarification(
                draft=FinancialPlanDraft(direction=Direction.EXPENSE, categories=("RESTAURANTS",))
            ),
        )
    ).model_dump(mode="json")

    turn = post(stack, route, "en julio", asked)

    assert turn.engine[0] == "rules"
    assert interpreter.seen == []


@pytest.mark.parametrize("route", ROUTES)
def test_a_preview_owns_the_next_turn_even_an_analytics_one(route: str) -> None:
    """M-7: a payment awaiting a yes is not suspended by a question; the rules route answers it
    and the preview stays open."""
    stack = a_stack()
    interpreter = Scripted(RESTAURANTS_IN_JULY, TO_PAYMENTS)
    engine_on(stack, interpreter)
    first = post(stack, route, "¿cuánto gasté en restaurantes en julio?")
    previewed = post(stack, route, "hazle un bizum de 20 a Marta Solis Vega", first.conversation)

    turn = post(stack, route, "¿y en ocio?", previewed.conversation)

    assert turn.engine[0] == "rules"
    assert len(interpreter.seen) == 2
    assert turn.conversation["workflow"] == "PAYMENT_CONFIRMATION"
    assert stack.bank.confirmed == []


@pytest.mark.parametrize("route", ROUTES)
def test_a_forgotten_conversation_starts_again_without_a_409(route: str) -> None:
    """R27: after a restart the store holds nothing; an open tab's next turn is answered under a
    new id, which it adopts, instead of a 409 on every turn."""
    stack = a_stack()
    engine_on(stack, Scripted(RESTAURANTS_IN_JULY))
    first = post(stack, route, "¿cuánto gasté en restaurantes en julio?")
    engine_on(stack, Scripted(RESTAURANTS_IN_JULY))  # a new process: an empty store

    again = post(stack, route, "¿cuánto gasté en restaurantes en julio?", first.conversation)

    assert again.status == 200
    assert again.conversation["conversation_id"] != first.conversation["conversation_id"]
    assert again.conversation["version"] == 1


@pytest.mark.parametrize("route", ROUTES)
def test_a_chart_of_what_is_on_screen_is_drawn_from_the_engines_plan(
    route: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R28: «ponlo en un gráfico» after an engine answer is the rules route's to draw, and the
    plan it is handed as the one on screen is the engine's, read from the same accounts."""
    stack = a_stack()
    engine_on(stack, Scripted(RESTAURANTS_IN_JULY, (Delegate("CHART"),)))
    handed: list[object] = []
    planned = stack.planner.plan

    async def plan(sentence: str, *, context: Any) -> Any:
        handed.append(context.continuation.plan if context.continuation else None)
        return await planned(sentence, context=context)

    monkeypatch.setattr(stack.planner, "plan", plan)
    first = post(stack, route, "¿cuánto gasté en restaurantes en julio?")

    charted = post(stack, route, "ponlo en un gráfico", first.conversation)

    assert charted.engine[0] == "rules"
    assert handed == [ran("RESTAURANTS")]
    assert charted.conversation["conversation_id"] == first.conversation["conversation_id"]


def test_graph_visual_operations_render_and_restyle_over_http_without_bank_reads() -> None:
    stack = a_stack(account_scope_default="all")
    stack.ledger.groups_for_next_turn = (
        Group("Alimentación", 30_000, 3),
        Group("Restaurantes", 18_000, 2),
        Group("Transporte", 9_000, 1),
    )
    operations = Scripted(
        (Ask(PlanFields(direction=Direction.EXPENSE, window=JULY, group_by=GroupBy.CATEGORY)),),
        (Visualize("ACTIVE_RESULT", "AUTO"),),
        (Visualize("ACTIVE_CHART", "LINE"),),
    )

    class UnusedAssessor:
        async def assess(self, _: object) -> object:
            raise AssertionError("this read-only chart conversation needs no investigation")

    stack_engine = GraphTurnHandler(
        GraphPorts(
            operations,
            UnusedAssessor(),  # type: ignore[arg-type]
            MemoryDialogueStore(),
            stack.ledger,  # type: ignore[arg-type]
            Accounts(),  # type: ignore[arg-type]
            Directory(),  # type: ignore[arg-type]
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
    app.state.engine = stack_engine

    queried = post(stack, JSON_ROUTE, "desglosa gastos por categorías de julio")
    bank_reads = len(stack.ledger.ran)
    created = post(stack, JSON_ROUTE, "enséñamelo en un gráfico", queried.conversation)
    restyled = post(stack, JSON_ROUTE, "cámbialo a líneas", created.conversation)

    assert queried.status == created.status == restyled.status == 200
    assert created.body["visualization"]["kind"] == "chart"
    assert created.body["visualization"]["spec"]["mark"]["type"] == "bar"
    assert restyled.body["visualization"]["kind"] == "chart"
    assert restyled.body["visualization"]["spec"]["mark"]["type"] == "line"
    assert created.conversation["version"] == 2
    assert restyled.conversation["version"] == 3
    assert len(stack.ledger.ran) == bank_reads
    assert created.engine[0] == restyled.engine[0] == "model"


def test_a_pair_on_screen_is_handed_over_as_the_last_comparison() -> None:
    june = AbsoluteRange(date(2026, 6, 1), date(2026, 6, 30))
    pair = ComparisonPlan(
        FinancialPlan(direction=Direction.EXPENSE, date_range=june, categories=("LEISURE",)),
        FinancialPlan(direction=Direction.EXPENSE, date_range=JULY, categories=("LEISURE",)),
    )
    question = QuestionRequest(question="ponlo en un gráfico")

    engine_dispatch._hand_over_the_screen(  # noqa: SLF001
        question, DialogueState(UUID(int=5), version=2, focus=ComparisonFocus(pair))
    )

    assert question.conversation is not None
    handed = conversation_mapper.to_domain(question.conversation).last_comparison
    assert handed == ComparisonPlan(
        replace(pair.earlier, across_accounts=True), replace(pair.latest, across_accounts=True)
    )


@pytest.mark.parametrize("route", ROUTES)
def test_a_searchs_offers_ride_through_an_engine_answer_in_the_same_conversation(
    route: str,
) -> None:
    """M-3: the sealed offers of a gym search are bound to the conversation id; an engine answer in
    that same conversation hands them back, so a later booking still finds them."""
    stack = a_stack()
    engine_on(stack, Scripted(RESTAURANTS_IN_JULY, AND_IN_LEISURE, AND_IN_LEISURE))
    first = post(stack, route, "¿cuánto gasté en restaurantes en julio?")
    offers = {"slots": [], "agent_names": [], "contexts": [], "token": "o1.abc.def"}

    same = post(stack, route, "¿y en ocio?", {**first.conversation, "activity_offers": offers})
    elsewhere = post(
        stack,
        route,
        "¿y en ocio?",
        {"conversation_id": str(UUID(int=99)), "activity_offers": offers},
    )

    assert same.conversation["activity_offers"] == offers
    # A new conversation: offers sealed for another id would never verify, so none ride along.
    assert elsewhere.conversation["conversation_id"] != str(UUID(int=99))
    assert elsewhere.conversation["activity_offers"] is None


@pytest.mark.parametrize("field", ["engine_temperature", "engine_max_output_tokens"])
def test_the_engines_sampling_refuses_a_negative(field: str) -> None:
    """M-7: refused when the settings load, by name, rather than by the provider mid-turn."""
    with pytest.raises(ValidationError, match=field):
        Settings(**{field: -1})  # type: ignore[arg-type]
