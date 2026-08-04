"""The ranking over HTTP.

Its own route rather than a flag on the query endpoint, and the tests say why: the two answers have
nothing in common. One is a total with groups and supporting movements, the other is a single
movement or nothing, and a shared response would carry half its fields null on either side — the
shape this session has spent its time undoing everywhere else.
"""

import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient

from finguard_banking.application import RankedMovementQuery, RankedMovementResult
from finguard_banking.application.queries.movement_summary import SummaryMovement
from finguard_banking.bootstrap.main import app

ACCOUNT = uuid.uuid4()
TRANSACTION = uuid.uuid4()

CEPSA = SummaryMovement(
    transaction_id=TRANSACTION,
    occurred_at=__import__("datetime").datetime(2026, 3, 14, 10, 0),
    description="Repostaje",
    merchant_name="Cepsa",
    category="TRANSPORT_FUEL",
    amount_minor=8240,
    direction="EXPENSE",
)


class FakeRanking:
    def __init__(self, movement: SummaryMovement | None) -> None:
        self._movement = movement
        self.asked: RankedMovementQuery | None = None

    async def handle(self, query: RankedMovementQuery) -> RankedMovementResult:
        self.asked = query
        return RankedMovementResult(
            account_id=query.plan.account_id,
            rank=query.rank,
            movement=self._movement,
            currency="USD" if self._movement is not None else None,
        )


@pytest.fixture
def client_and_handler(monkeypatch):
    handler = FakeRanking(CEPSA)
    bus = app.state.query_bus
    original = bus._handlers.get(RankedMovementQuery)  # noqa: SLF001 - swapping one registration
    bus._handlers[RankedMovementQuery] = handler  # noqa: SLF001
    yield TestClient(app), handler
    if original is not None:
        bus._handlers[RankedMovementQuery] = original  # noqa: SLF001


def body(**over: object) -> dict[str, object]:
    request: dict[str, object] = {
        "accountId": str(ACCOUNT),
        "direction": "EXPENSE",
        "categories": ["TRANSPORT_FUEL"],
        "period": {"period": "THIS_YEAR"},
        "rank": 1,
    }
    return request | over


def test_the_largest_movement_comes_back_whole(client_and_handler) -> None:
    """Not a figure: the movement. A maximum has a date and a shop, and an answer that named
    neither would send the user to the movement list to work out which charge it was."""
    client, _ = client_and_handler

    response = client.post("/v1/financial-queries/ranked", json=body())

    assert response.status_code == 200
    found = response.json()["movement"]
    assert found["amountMinor"] == 8240
    assert found["merchantName"] == "Cepsa"
    assert found["occurredAt"].startswith("2026-03-14")


def test_the_rank_reaches_the_use_case(client_and_handler) -> None:
    client, handler = client_and_handler

    client.post("/v1/financial-queries/ranked", json=body(rank=2))

    assert handler.asked is not None
    assert handler.asked.rank == 2


def test_the_window_is_resolved_at_the_edge(client_and_handler) -> None:
    """A relative period becomes absolute here, like every other query: the domain sees dates."""
    client, handler = client_and_handler

    client.post("/v1/financial-queries/ranked", json=body())

    assert handler.asked is not None
    assert handler.asked.plan.date_range.start == date(date.today().year, 1, 1)


def test_a_period_with_fewer_movements_answers_with_none(client_and_handler) -> None:
    """200 with no movement rather than 404: the account exists and the question is fine, there is
    simply no fifth largest expense. A 404 would say the account is missing."""
    client, handler = client_and_handler
    handler._movement = None  # noqa: SLF001 - the point of the test

    response = client.post("/v1/financial-queries/ranked", json=body(rank=5))

    assert response.status_code == 200
    assert response.json()["movement"] is None
    assert response.json()["rank"] == 5


@pytest.mark.parametrize("rank", [0, -1, 4000])
def test_a_rank_outside_its_bounds_is_refused(client_and_handler, rank: int) -> None:
    """It becomes an OFFSET. "El gasto número 4000" is a scan, not a question."""
    client, _ = client_and_handler

    assert client.post("/v1/financial-queries/ranked", json=body(rank=rank)).status_code == 422


def test_a_ranking_needs_a_window_like_everything_else(client_and_handler) -> None:
    client, _ = client_and_handler
    request = body()
    del request["period"]

    assert client.post("/v1/financial-queries/ranked", json=request).status_code == 422


def test_the_answer_says_what_the_amount_is_denominated_in(client_and_handler) -> None:
    """Beside the movement, like the aggregate's currency is beside its total.

    Without it the caller had nothing to write next to the figure, so the assistant wrote "EUR" and
    a charge on a dollar account was labelled in euros beside a number that was perfectly correct.
    A wrong currency on a right amount is the hardest kind of wrong answer to notice.
    """
    client, _ = client_and_handler

    answered = client.post("/v1/financial-queries/ranked", json=body()).json()

    assert answered["currency"] == "USD"


def test_nothing_found_denominates_nothing(client_and_handler) -> None:
    """There is no amount, so there is no currency. A default here would be a fact invented about
    an account nobody read."""
    client, handler = client_and_handler
    handler._movement = None  # noqa: SLF001 - the fixture's one knob

    answered = client.post("/v1/financial-queries/ranked", json=body()).json()

    assert answered["movement"] is None
    assert answered["currency"] is None
