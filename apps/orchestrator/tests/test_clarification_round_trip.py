"""Two turns over HTTP, which is where the draft has to survive.

The completion is tested against the handler elsewhere. What is not obvious from there is that
the draft makes it *out* of one response and *back into* the next one — it crosses the wire
twice, through a Pydantic model on each side, and either crossing can silently drop it.

The client is the one holding it, deliberately: this service keeps no conversation, so a
half-answered question lives in the caller's hands exactly like an answered one does. The
consequence is that the payload has to be complete enough to rebuild from, and typed enough
that a caller cannot send anything else — the same argument that stopped the plan description
from travelling as text.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from finguard_orchestrator.application import AskFinancialQuestionQuery
from finguard_orchestrator.application.bus.query_bus import QueryBus
from finguard_orchestrator.application.queries.ask_financial_question import (
    AskFinancialQuestionResult,
    SpokenPlan,
)
from finguard_orchestrator.bootstrap.dependencies import authenticated_caller, get_query_bus
from finguard_orchestrator.bootstrap.main import app
from finguard_orchestrator.domain.value_objects.caller import Caller
from finguard_orchestrator.domain.value_objects.scope import Scope

ACCOUNT = uuid.UUID("11111111-1111-1111-1111-111111111111")

DRAFT = SpokenPlan(direction="EXPENSE", categories=["HOUSING_RENT"], metric="SUM")

ASKING_BACK = AskFinancialQuestionResult(
    outcome="NEEDS_CLARIFICATION",
    text="¿De qué periodo?",
    question="¿De qué periodo?",
    pending_context=DRAFT,
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


def test_the_question_back_carries_the_draft_to_the_client() -> None:
    client, _ = client_for(ASKING_BACK)

    body = client.post("/v1/questions", json={"question": "¿cuánto pago de alquiler?"}).json()

    assert body["outcome"] == "NEEDS_CLARIFICATION"
    assert body["pending_context"]["categories"] == ["HOUSING_RENT"]
    assert body["pending_context"]["direction"] == "EXPENSE"
    # The empty window is the whole point: it is what the question is asking for.
    assert body["pending_context"]["period"] is None


def test_the_echoed_draft_arrives_as_a_draft() -> None:
    """Rebuilt at the edge, like the follow-up plan beside it. What reaches the handler is a
    domain object, not a payload it would have to trust."""
    client, asking = client_for(ASKING_BACK)
    handed_out = client.post("/v1/questions", json={"question": "¿cuánto pago?"}).json()

    client.post(
        "/v1/questions",
        json={"question": "este año", "pending_draft": handed_out["pending_context"]},
    )

    assert asking.received is not None
    draft = asking.received.pending_draft
    assert draft is not None
    assert draft.categories == ("HOUSING_RENT",)
    assert draft.needs_a_window is True


def test_an_answer_carries_no_draft() -> None:
    """Absent rather than empty, so a client can tell "nothing pending" from "pending with no
    filters" — which are different conversations."""
    client, _ = client_for(
        AskFinancialQuestionResult(outcome="PLANNED", text="Has gastado 159,46 €")
    )

    body = client.post("/v1/questions", json={"question": "¿cuánto gasté?"}).json()

    assert body["pending_context"] is None


def test_a_draft_with_both_windows_is_a_bad_request() -> None:
    """It cannot have come from here. Refusing at the edge is the same treatment the echoed
    plan gets: a 422 says what happened, where accepting it would put a plan the domain
    refuses one layer deeper before failing."""
    client, asking = client_for(ASKING_BACK)

    response = client.post(
        "/v1/questions",
        json={
            "question": "este año",
            "pending_draft": {
                "direction": "EXPENSE",
                "period": "LAST_MONTH",
                "start": "2026-01-01",
                "end": "2026-01-31",
            },
        },
    )

    assert response.status_code == 422
    assert asking.received is None


def test_a_draft_carrying_something_that_is_not_a_category_is_a_bad_request() -> None:
    """The draft is input on the way back, so it gets input's treatment — the same shape check
    the echoed plan gets, at the same edge.

    Shape, not membership: this refuses a value that is not a category at all. A well-formed
    identifier that happens to name nothing is banking's to return nothing for, which is the
    same division of labour the plan path has.
    """
    client, asking = client_for(ASKING_BACK)

    response = client.post(
        "/v1/questions",
        json={
            "question": "este año",
            "pending_draft": {"direction": "EXPENSE", "categories": ["'; DROP TABLE --"]},
        },
    )

    assert response.status_code == 422
    assert asking.received is None
