"""The card screen answers about the holder's days, like every screen beside it.

`test_chat_uses_the_holder_timezone` fixed the conversation; the card routes were the path it did
not reach, and they lost the holder's day twice over:

    the route      resolved `since` from the holder's day, correctly
    this client    threw it away and recomputed the window from `date.today()`
    banking        resolved it again against *its* clock and binned in a hardcoded zone

So a holder in the Canaries read the chat on their day and their card charges on Madrid's, and both
figures were real. Two of those three are here; the third is `test_card_window_zone` over there.

The lesson is in the adapter. A client that reads a clock is a second opinion about what day it is,
and the whole point of resolving the date at the edge is that there should only be one.
"""

import uuid
from datetime import date

import httpx2 as httpx
import pytest
from fastapi.testclient import TestClient

from finguard_orchestrator.application.bus.query_bus import QueryBus
from finguard_orchestrator.application.queries.list_cards import (
    GetCardChargesHandler,
    GetCardChargesQuery,
    ListCardsHandler,
    ListCardsQuery,
)
from finguard_orchestrator.bootstrap.main import app
from finguard_orchestrator.domain.models.card import Wallet
from finguard_orchestrator.domain.value_objects.caller import Caller
from finguard_orchestrator.domain.value_objects.scope import Scope
from finguard_orchestrator.infrastructure.banking.http_cards import HttpCards
from finguard_orchestrator.infrastructure.http.dependencies import (
    authenticated_caller,
    get_query_bus,
    reporting_today,
)

ACCOUNT = uuid.UUID("11111111-1111-1111-1111-111111111111")
CARD = uuid.UUID("33333333-3333-3333-3333-333333333333")

# The holder's day. Never today, on purpose: an adapter that goes back to reading the clock passes
# every assertion about the zone and fails these.
CANARIAN_TODAY = date(2026, 8, 3)
CANARIAN_ZONE = "Atlantic/Canary"

WALLET = {"since": "2026-07-04", "cards": []}
CHARGES: dict[str, list[object]] = {"charges": []}


# --- the client: what goes on the wire ---------------------------------------------------


def _asking(monkeypatch, seen: list[httpx.URL], body: object) -> HttpCards:  # type: ignore[no-untyped-def]
    real = httpx.AsyncClient

    def transport(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        return httpx.Response(200, json=body)

    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(transport))
    )
    return HttpCards(base_url="http://banking.invalid", timeout_seconds=1.0)


async def test_the_card_list_sends_the_holders_day_and_zone(
    monkeypatch: "pytest.MonkeyPatch",
) -> None:
    seen: list[httpx.URL] = []

    await _asking(monkeypatch, seen, WALLET).all_of(
        ACCOUNT, since=date(2026, 7, 5), today=CANARIAN_TODAY, timezone=CANARIAN_ZONE
    )

    assert seen[0].params["reference"] == "2026-08-03"
    assert seen[0].params["timezone"] == CANARIAN_ZONE
    assert seen[0].params["days"] == "30"


async def test_the_charges_send_them_too(monkeypatch: "pytest.MonkeyPatch") -> None:
    """The list and the movements behind it resolve the same window or the screen contradicts
    itself: a total for thirty days with the charges of twenty-nine under it."""
    seen: list[httpx.URL] = []

    await _asking(monkeypatch, seen, CHARGES).charges_on(
        ACCOUNT,
        CARD,
        since=date(2026, 7, 5),
        today=CANARIAN_TODAY,
        timezone=CANARIAN_ZONE,
        limit=50,
    )

    assert seen[0].params["reference"] == "2026-08-03"
    assert seen[0].params["timezone"] == CANARIAN_ZONE
    assert seen[0].params["limit"] == "50"


async def test_a_zone_survives_being_put_in_a_url(monkeypatch: "pytest.MonkeyPatch") -> None:
    """A zone name has a slash in it. Interpolated raw it becomes a path segment banking never
    sees, and the request quietly falls back to the deployment's zone — which is the bug this
    replaces, wearing a fix."""
    seen: list[httpx.URL] = []

    await _asking(monkeypatch, seen, WALLET).all_of(
        ACCOUNT, since=date(2026, 7, 5), today=CANARIAN_TODAY, timezone=CANARIAN_ZONE
    )

    assert seen[0].path == f"/v1/accounts/{ACCOUNT}/cards"


# --- the route: where the zone comes from ------------------------------------------------


class Remembers:
    """Stands in for the card directory and keeps what the handler asked it."""

    def __init__(self) -> None:
        self.today: date | None = None
        self.timezone: str | None = None
        self.since: date | None = None

    async def all_of(self, account_id: uuid.UUID, *, since: date, today: date, timezone: str):  # type: ignore[no-untyped-def]
        self.since, self.today, self.timezone = since, today, timezone
        return Wallet(since=since, cards=())

    async def charges_on(  # type: ignore[no-untyped-def]
        self,
        account_id: uuid.UUID,
        card_id: uuid.UUID,
        *,
        since: date,
        today: date,
        timezone: str,
        limit: int,
    ):
        self.since, self.today, self.timezone = since, today, timezone
        return ()


@pytest.fixture(autouse=True)
def _restore():  # type: ignore[no-untyped-def]
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def canarian_client() -> tuple[TestClient, Remembers]:
    cards = Remembers()
    bus = QueryBus()
    bus.register(ListCardsQuery, ListCardsHandler(cards))
    bus.register(GetCardChargesQuery, GetCardChargesHandler(cards))
    app.dependency_overrides[get_query_bus] = lambda: bus
    app.dependency_overrides[reporting_today] = lambda: CANARIAN_TODAY
    app.dependency_overrides[authenticated_caller] = lambda: Caller(
        holder_reference="ES-0002",
        account_id=ACCOUNT,
        scopes=frozenset({Scope.QUESTIONS_READ}),
        reporting_timezone=CANARIAN_ZONE,
    )
    app.state.questions_enabled = True
    return TestClient(app), cards


def test_the_cards_route_asks_in_the_holders_zone() -> None:
    client, cards = canarian_client()

    response = client.get("/v1/cards", params={"days": 30})

    assert response.status_code == 200
    assert cards.timezone == CANARIAN_ZONE
    assert cards.today == CANARIAN_TODAY
    assert cards.since == date(2026, 7, 5)


def test_the_charges_route_asks_in_the_holders_zone() -> None:
    client, cards = canarian_client()

    response = client.get(f"/v1/cards/{CARD}/charges", params={"days": 30})

    assert response.status_code == 200
    assert cards.timezone == CANARIAN_ZONE
    assert cards.today == CANARIAN_TODAY


def test_the_zone_is_the_credentials_and_not_a_default() -> None:
    """It comes from the identity, like the chat's. A default here would be a zone nobody chose
    agreeing with the deployment and disagreeing with the holder."""
    client, cards = canarian_client()

    client.get("/v1/cards", params={"days": 30})

    assert cards.timezone != Caller(holder_reference="x", account_id=ACCOUNT).reporting_timezone


async def test_the_day_count_counts_both_ends_of_the_window(
    monkeypatch: "pytest.MonkeyPatch",
) -> None:
    """`since` and `today` are two dates; the span between them is their difference plus one.

    Banking takes a count and resolves it back into a date the same way, so sending the bare
    difference asked the far side for a window starting one day after the one computed here — the
    cards were read over a shorter period than the chat beside them, both labelled the same. The
    single day is the case that needs no arithmetic: from today to today is one day, not zero.
    """
    seen: list[httpx.URL] = []

    await _asking(monkeypatch, seen, WALLET).all_of(
        ACCOUNT, since=CANARIAN_TODAY, today=CANARIAN_TODAY, timezone=CANARIAN_ZONE
    )

    assert seen[0].params["days"] == "1"

    await _asking(monkeypatch, seen, WALLET).all_of(
        ACCOUNT, since=date(2026, 7, 28), today=CANARIAN_TODAY, timezone=CANARIAN_ZONE
    )

    assert seen[1].params["days"] == "7", "28 July to 3 August inclusive is a week"
