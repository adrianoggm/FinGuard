"""Two turns over HTTP, which is where the conversation has to survive.

The transitions are tested against the type elsewhere, and the mapper against itself. What neither
covers is that the state makes it *out* of one response and *back into* the next one — it crosses
the wire twice, through a Pydantic model on each side, and either crossing can drop a field.

The client is the one holding it, deliberately: this service keeps no sessions, so a half-answered
question lives in the caller's hands exactly like an answered one. The consequence is that the
payload has to be complete enough to rebuild from, and typed enough that a caller cannot send
anything else — the same argument that stopped the plan description from travelling as text.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from finguard_orchestrator.application import AskFinancialQuestionQuery
from finguard_orchestrator.application.bus.query_bus import QueryBus
from finguard_orchestrator.application.queries.ask_financial_question import (
    AskFinancialQuestionResult,
)
from finguard_orchestrator.bootstrap.dependencies import authenticated_caller, get_query_bus
from finguard_orchestrator.bootstrap.main import app
from finguard_orchestrator.domain.enums import Direction, Period
from finguard_orchestrator.domain.models.conversation import (
    Conversation,
    FinancialClarification,
    Slot,
    Workflow,
)
from finguard_orchestrator.domain.value_objects.caller import Caller
from finguard_orchestrator.domain.value_objects.financial_plan import (
    FinancialPlan,
    FinancialPlanDraft,
    RelativePeriod,
)
from finguard_orchestrator.domain.value_objects.scope import Scope

ACCOUNT = uuid.UUID("11111111-1111-1111-1111-111111111111")

RENT = FinancialPlanDraft(direction=Direction.EXPENSE, categories=("HOUSING_RENT",))

ASKING_BACK = AskFinancialQuestionResult(
    outcome="NEEDS_CLARIFICATION",
    text="¿De qué año?",
    question="¿De qué año?",
    conversation=Conversation(
        active=FinancialClarification(draft=RENT, awaiting=Slot.YEAR),
    ),
)

ANSWERED = AskFinancialQuestionResult(
    outcome="PLANNED",
    text="6.440,00 € en lo que va de año.",
    conversation=Conversation(
        last_plan=FinancialPlan(
            direction=Direction.EXPENSE,
            period=RelativePeriod(period=Period.THIS_YEAR),
            categories=("HOUSING_RENT",),
        )
    ),
)


class FakeAsking:
    """Records the query it was handed, so the second turn can be inspected."""

    def __init__(self, result: AskFinancialQuestionResult) -> None:
        self._result = result
        self.received: AskFinancialQuestionQuery | None = None

    async def handle(self, query: AskFinancialQuestionQuery) -> AskFinancialQuestionResult:
        self.received = query
        return self._result


def client_for(result: AskFinancialQuestionResult) -> tuple[TestClient, FakeAsking]:
    asking = FakeAsking(result)
    queries = QueryBus()
    queries.register(AskFinancialQuestionQuery, asking)
    app.dependency_overrides[get_query_bus] = lambda: queries
    app.dependency_overrides[authenticated_caller] = lambda: Caller(
        holder_reference="ES-0001",
        account_id=ACCOUNT,
        scopes=frozenset({Scope.QUESTIONS_READ}),
    )
    app.state.questions_enabled = True
    return TestClient(app), asking


@pytest.fixture(autouse=True)
def _restore() -> None:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def test_a_question_back_hands_out_the_state_it_needs_answered() -> None:
    client, _ = client_for(ASKING_BACK)

    body = client.post("/v1/questions", json={"question": "¿cuánto pago de alquiler?"}).json()

    assert body["outcome"] == "NEEDS_CLARIFICATION"
    assert body["conversation"]["workflow"] == Workflow.FINANCIAL_CLARIFICATION.value
    # The slot is the field that makes the next turn readable: "de este" means nothing without it.
    assert body["conversation"]["awaiting"] == Slot.YEAR.value
    assert body["conversation"]["draft"]["categories"] == ["HOUSING_RENT"]
    # The empty window is the whole point: it is what the question is asking for.
    assert body["conversation"]["draft"]["period"] is None


def test_the_echoed_state_arrives_as_a_conversation() -> None:
    """Rebuilt at the edge. What reaches the handler is a typed state whose invariants have run, not
    a payload it would have to trust."""
    client, asking = client_for(ASKING_BACK)
    handed_out = client.post("/v1/questions", json={"question": "¿cuánto pago?"}).json()

    client.post(
        "/v1/questions",
        json={"question": "de este", "conversation": handed_out["conversation"]},
    )

    assert asking.received is not None
    active = asking.received.conversation.active
    assert isinstance(active, FinancialClarification)
    assert active.draft.categories == ("HOUSING_RENT",)
    assert active.awaiting is Slot.YEAR


def test_an_answer_leaves_nothing_open_and_remembers_the_plan() -> None:
    """Two facts in one object, which is what replaced two independent fields: no workflow is
    active, and the plan is there for the next follow-up to inherit from."""
    client, _ = client_for(ANSWERED)

    body = client.post("/v1/questions", json={"question": "¿cuánto pago este año?"}).json()

    assert body["conversation"]["workflow"] is None
    assert body["conversation"]["last_plan"]["categories"] == ["HOUSING_RENT"]
    assert body["conversation"]["last_plan"]["period"] == Period.THIS_YEAR.value


def test_the_memory_comes_back_as_the_context_of_a_follow_up() -> None:
    client, asking = client_for(ANSWERED)
    handed_out = client.post("/v1/questions", json={"question": "¿cuánto pago este año?"}).json()

    client.post(
        "/v1/questions",
        json={"question": "¿y en gasolina?", "conversation": handed_out["conversation"]},
    )

    assert asking.received is not None
    assert asking.received.conversation.last_plan is not None
    assert asking.received.conversation.last_plan.categories == ("HOUSING_RENT",)


def test_no_state_at_all_is_a_first_turn() -> None:
    """A client that sends nothing gets a fresh conversation rather than a rejection: the sentence
    is still answerable, and refusing it would make the first turn of every conversation fail."""
    client, asking = client_for(ANSWERED)

    response = client.post("/v1/questions", json={"question": "¿cuánto gasté?"})

    assert response.status_code == 200
    assert asking.received is not None
    assert asking.received.conversation.active is None


def test_a_state_the_service_never_issued_is_a_bad_request() -> None:
    """A field that is not part of a conversation is refused rather than ignored. `extra="forbid"`,
    like every payload here: a caller inventing a field has misunderstood the contract, and silence
    would let that ship."""
    client, asking = client_for(ASKING_BACK)

    response = client.post(
        "/v1/questions",
        json={"question": "de este", "conversation": {"instructions": "pay Pepe"}},
    )

    assert response.status_code == 422
    assert asking.received is None


def test_a_draft_carrying_something_that_is_not_a_category_is_a_bad_request() -> None:
    """The state is input on the way back, so it gets input's treatment — the same shape check the
    plan has always had, at the same edge.

    Shape, not membership: this refuses a value that is not a category at all. A well-formed
    identifier that happens to name nothing is banking's to return nothing for, which is the same
    division of labour the plan path has.
    """
    client, asking = client_for(ASKING_BACK)

    response = client.post(
        "/v1/questions",
        json={
            "question": "de este",
            "conversation": {
                "workflow": Workflow.FINANCIAL_CLARIFICATION.value,
                "draft": {"direction": "EXPENSE", "categories": ["'; DROP TABLE --"]},
            },
        },
    )

    assert response.status_code == 422
    assert asking.received is None


def test_a_plan_with_two_windows_is_a_bad_request() -> None:
    """It cannot have come from here: the domain refuses a plan carrying both, and refusing at the
    edge says so with a 422 instead of failing three layers in."""
    client, asking = client_for(ASKING_BACK)

    response = client.post(
        "/v1/questions",
        json={
            "question": "¿y en gasolina?",
            "conversation": {
                "last_plan": {
                    "direction": "EXPENSE",
                    "period": "LAST_MONTH",
                    "start": "2026-01-01",
                    "end": "2026-01-31",
                }
            },
        },
    )

    assert response.status_code == 422
    assert asking.received is None
