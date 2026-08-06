"""The chat answers about the holder's day, like every screen does.

`AskFinancialQuestionQuery` has carried `reporting_timezone` since the two-clocks bug was fixed, and
the summary, movements and breakdown endpoints all pass `who.reporting_timezone`. The two question
endpoints did not, so the conversation used the default while the screens beside it used the
holder's zone:

    movements screen   3 de agosto in the Canaries
    chat               4 de agosto in Madrid

between midnight and 01:00 peninsular time. Same phone, same account, two different days — which
affects "hoy", "esta semana", "este mes", the turn of the year, daily groupings and every ranking.

The zone comes from the identity rather than from the device, deliberately. Somebody travelling
still wants their account read in the zone their account is kept in.
"""

import json
import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient

from finguard_orchestrator.application import (
    AskFinancialQuestionQuery,
    AskFinancialQuestionResult,
    QueryBus,
)
from finguard_orchestrator.bootstrap.dependencies import (
    authenticated_caller,
    get_ask_handler,
    get_query_bus,
    reporting_today,
)
from finguard_orchestrator.bootstrap.main import app
from finguard_orchestrator.domain.value_objects.caller import Caller
from finguard_orchestrator.domain.value_objects.scope import Scope

ACCOUNT = uuid.UUID("11111111-1111-1111-1111-111111111111")
# The peninsular date. In the Canaries between 00:00 and 01:00 it is still the day before, which is
# exactly why the zone has to travel with it.
TODAY = date(2026, 8, 4)


class RecordingHandler:
    """Stands in for the ask handler on both routes, and remembers the query it was given."""

    def __init__(self) -> None:
        self.queries: list[AskFinancialQuestionQuery] = []

    async def handle(self, query: AskFinancialQuestionQuery) -> AskFinancialQuestionResult:
        self.queries.append(query)
        return AskFinancialQuestionResult(outcome="PLANNED", text="12,00 € hoy.")

    async def stream(self, query: AskFinancialQuestionQuery):
        yield await self.handle(query)


@pytest.fixture(autouse=True)
def _restore():
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def canarian_client() -> tuple[TestClient, RecordingHandler]:
    handler = RecordingHandler()
    bus = QueryBus()
    bus.register(AskFinancialQuestionQuery, handler)
    app.dependency_overrides[get_query_bus] = lambda: bus
    app.dependency_overrides[get_ask_handler] = lambda: handler
    app.dependency_overrides[reporting_today] = lambda: TODAY
    app.dependency_overrides[authenticated_caller] = lambda: Caller(
        holder_reference="ES-0002",
        account_id=ACCOUNT,
        scopes=frozenset({Scope.QUESTIONS_READ}),
        reporting_timezone="Atlantic/Canary",
    )
    app.state.questions_enabled = True
    return TestClient(app), handler


def test_the_question_endpoint_asks_in_the_holders_zone() -> None:
    client, handler = canarian_client()

    client.post("/v1/questions", json={"question": "¿cuánto gasté hoy?"})

    assert handler.queries[0].reporting_timezone == "Atlantic/Canary"


def test_the_streaming_endpoint_asks_in_the_holders_zone() -> None:
    """The browser types into this one, so a zone that only reached the other route was a zone the
    conversation never used."""
    client, handler = canarian_client()

    response = client.post("/v1/questions/stream", json={"question": "¿cuánto gasté hoy?"})

    assert response.status_code == 200
    assert [json.loads(line.removeprefix("data: ")) for line in response.text.splitlines() if line]
    assert handler.queries[0].reporting_timezone == "Atlantic/Canary"


def test_both_endpoints_agree_about_the_day_and_the_zone() -> None:
    """Two routes answering the same sentence differently is the failure mode the whole streaming
    duplication exists to avoid, and the zone is one more field it applies to."""
    client, handler = canarian_client()

    client.post("/v1/questions", json={"question": "¿cuánto gasté hoy?"})
    client.post("/v1/questions/stream", json={"question": "¿cuánto gasté hoy?"})

    direct, streamed = handler.queries
    assert direct.reporting_timezone == streamed.reporting_timezone
    assert direct.today == streamed.today == TODAY
