"""A card window belongs to whoever is asking, not to wherever this is deployed.

The two-clocks bug, third instance. It was fixed for relative periods — `reference` and `timezone`
travel together in a query body and win over this service's clock — and the card routes never got
the same treatment. They resolved "the last thirty days" against the deployment's day and then
binned the charges in a zone hardcoded on the query object, so:

    chat and movements    3 de agosto, Atlantic/Canary
    card screen           4 de agosto, Europe/Madrid

for the same holder on the same phone, between midnight and 01:00 peninsular time. Both figures are
real, which is what makes it worth a test rather than a comment.

These go through the route, because the route is where the two values were being thrown away — the
repository has taken a zone since it was written, and `test_cards` already covers what it does with
one.
"""

import uuid
from datetime import date

import pytest
from fastapi.testclient import TestClient

from finguard_banking.application.bus.query_bus import QueryBus
from finguard_banking.application.queries.list_card_charges import (
    ListCardChargesHandler,
    ListCardChargesQuery,
)
from finguard_banking.application.queries.list_cards import ListCardsHandler, ListCardsQuery
from finguard_banking.bootstrap.dependencies import (
    get_query_bus,
    reporting_today,
    reporting_zone,
)
from finguard_banking.bootstrap.main import app

ACCOUNT = uuid.UUID("11111111-1111-1111-1111-111111111111")
CARD = uuid.UUID("33333333-3333-3333-3333-333333333333")

# What this deployment thinks. Deliberately a different day and zone from the caller's below, so a
# route that quietly used its own would be answering about 4 August in Madrid.
DEPLOYMENT_TODAY = date(2026, 8, 4)
DEPLOYMENT_ZONE = "Europe/Madrid"

CANARIAN_TODAY = date(2026, 8, 3)
CANARIAN_ZONE = "Atlantic/Canary"


class Records:
    """Stands in for the card directory and keeps the window it was asked about."""

    def __init__(self) -> None:
        self.since: date | None = None
        self.timezone: str | None = None

    async def all_of(self, account_id: uuid.UUID, *, since: date, timezone: str) -> tuple[()]:
        self.since, self.timezone = since, timezone
        return ()

    async def charges_on(
        self, account_id: uuid.UUID, card_id: uuid.UUID, *, since: date, timezone: str, limit: int
    ) -> tuple[()]:
        self.since, self.timezone = since, timezone
        return ()


@pytest.fixture(autouse=True)
def _clear_overrides() -> None:
    app.dependency_overrides.clear()


def a_deployment_in_madrid() -> tuple[TestClient, Records]:
    cards = Records()
    bus = QueryBus()
    bus.register(ListCardsQuery, ListCardsHandler(cards))  # type: ignore[arg-type]
    bus.register(ListCardChargesQuery, ListCardChargesHandler(cards))  # type: ignore[arg-type]
    app.dependency_overrides[get_query_bus] = lambda: bus
    app.dependency_overrides[reporting_today] = lambda: DEPLOYMENT_TODAY
    app.dependency_overrides[reporting_zone] = lambda: DEPLOYMENT_ZONE
    return TestClient(app), cards


# --- what the caller says wins -----------------------------------------------------------


def test_the_window_starts_from_the_callers_day_and_not_this_ones() -> None:
    client, cards = a_deployment_in_madrid()

    response = client.get(
        f"/v1/accounts/{ACCOUNT}/cards",
        params={"days": 30, "reference": CANARIAN_TODAY.isoformat(), "timezone": CANARIAN_ZONE},
    )

    assert response.status_code == 200
    assert cards.since == date(2026, 7, 5), "thirty days ending on the caller's 3 August"


def test_the_callers_zone_is_the_zone_the_charges_are_binned_in() -> None:
    """The half that actually moves a figure. `since` is a date; which charges fall on the wrong
    side of it is decided by the zone the timestamps are truncated in, one layer down."""
    client, cards = a_deployment_in_madrid()

    client.get(
        f"/v1/accounts/{ACCOUNT}/cards",
        params={"days": 30, "reference": CANARIAN_TODAY.isoformat(), "timezone": CANARIAN_ZONE},
    )

    assert cards.timezone == CANARIAN_ZONE


def test_the_charges_behind_a_card_use_the_same_window() -> None:
    """A total and the movements behind it that disagree is the failure this endpoint exists to
    prevent — the screen states a figure and then cannot show it."""
    client, cards = a_deployment_in_madrid()

    response = client.get(
        f"/v1/accounts/{ACCOUNT}/cards/{CARD}/charges",
        params={"days": 30, "reference": CANARIAN_TODAY.isoformat(), "timezone": CANARIAN_ZONE},
    )

    assert response.status_code == 200
    assert (cards.since, cards.timezone) == (date(2026, 7, 5), CANARIAN_ZONE)


# --- what happens when it does not -------------------------------------------------------


def test_a_caller_that_says_nothing_gets_the_deployments_day_and_zone() -> None:
    """The contract stays backward compatible, exactly as it did for relative periods: a caller
    sending only a day count is not broken by the pair arriving."""
    client, cards = a_deployment_in_madrid()

    client.get(f"/v1/accounts/{ACCOUNT}/cards", params={"days": 30})

    assert cards.since == date(2026, 7, 6)
    assert cards.timezone == DEPLOYMENT_ZONE


def test_the_zone_is_never_the_one_that_used_to_be_hardcoded() -> None:
    """`ListCardsQuery.reporting_timezone` defaulted to "Europe/Madrid" and the controller never
    passed anything, so that string was the answer whatever the deployment had been configured
    with. A default that cannot be overridden reads as configuration and is a constant."""
    cards = Records()
    bus = QueryBus()
    bus.register(ListCardsQuery, ListCardsHandler(cards))  # type: ignore[arg-type]
    app.dependency_overrides[get_query_bus] = lambda: bus
    app.dependency_overrides[reporting_today] = lambda: DEPLOYMENT_TODAY
    app.dependency_overrides[reporting_zone] = lambda: "America/Bogota"

    TestClient(app).get(f"/v1/accounts/{ACCOUNT}/cards", params={"days": 30})

    assert cards.timezone == "America/Bogota"


def test_an_unknown_zone_is_refused_rather_than_ignored() -> None:
    """Falling back would answer about peninsular days a caller never asked about, with a real
    figure and no error. The same rule the query endpoint already applies to a period's zone."""
    client, cards = a_deployment_in_madrid()

    response = client.get(
        f"/v1/accounts/{ACCOUNT}/cards", params={"days": 30, "timezone": "Mars/Olympus_Mons"}
    )

    assert response.status_code == 422
    assert cards.timezone is None, "it reached the handler anyway"


def test_a_window_of_n_days_is_n_days_and_not_n_plus_one() -> None:
    """`since = reference - days` counts the reference day twice over.

    Both ends are included — the charges on `since` are in the window and so are the ones on the
    caller's own day — so subtracting thirty produced thirty-one distinct dates. A card total
    labelled "últimos 30 días" carried a thirty-first day of charges, and on the 1st of a month that
    thirty-first day is the whole of the previous month's last day.

    One day is the case that shows it without arithmetic: asking for a single day has to mean today.
    """
    client, cards = a_deployment_in_madrid()

    client.get(
        f"/v1/accounts/{ACCOUNT}/cards",
        params={"days": 1, "reference": CANARIAN_TODAY.isoformat(), "timezone": CANARIAN_ZONE},
    )

    assert cards.since == CANARIAN_TODAY

    client.get(
        f"/v1/accounts/{ACCOUNT}/cards/{CARD}/charges",
        params={"days": 7, "reference": CANARIAN_TODAY.isoformat(), "timezone": CANARIAN_ZONE},
    )

    assert cards.since == date(2026, 7, 28), "a week ending on 3 August starts on 28 July"
