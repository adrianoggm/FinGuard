"""The route's shape, without a database.

`test_holder_isolation` covers what it returns and, more importantly, what it refuses. This covers
the contract: the names on the wire, and that the kind survives as far as the client — because the
kind is what tells a caller whether a figure may be added into a total, and a mapper that dropped
it would still produce a response that parses.
"""

import uuid

import pytest
from fastapi.testclient import TestClient

from finguard_banking.application.bus.query_bus import QueryBus
from finguard_banking.application.queries.list_accounts import (
    ListAccountsHandler,
    ListAccountsQuery,
)
from finguard_banking.bootstrap.dependencies import get_query_bus
from finguard_banking.bootstrap.main import app
from finguard_banking.domain.enums import AccountKind, AccountStatus
from finguard_banking.domain.models.holding import Holding
from finguard_banking.domain.value_objects.money import Money

CURRENT = uuid.UUID("11111111-1111-1111-1111-111111111111")
PIGGY = uuid.UUID("22222222-2222-2222-2222-222222222222")

HOLDINGS = (
    Holding(
        CURRENT,
        AccountKind.CURRENT,
        "Cuenta corriente",
        Money(227_042, "EUR"),
        AccountStatus.ACTIVE,
    ),
    Holding(PIGGY, AccountKind.SAVINGS, "Hucha", Money(548_550, "EUR"), AccountStatus.ACTIVE),
)


class FakeDirectory:
    def __init__(self, holdings: tuple[Holding, ...]) -> None:
        self._holdings = holdings

    async def all_of(self, account_id: uuid.UUID) -> tuple[Holding, ...]:
        return self._holdings


def _with(holdings: tuple[Holding, ...]) -> TestClient:
    bus = QueryBus()
    bus.register(ListAccountsQuery, ListAccountsHandler(FakeDirectory(holdings)))
    app.dependency_overrides[get_query_bus] = lambda: bus
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides() -> None:
    app.dependency_overrides.clear()


def test_it_answers_with_the_contract_names() -> None:
    """camelCase on the wire, and the amount/currency pair together. An amount whose currency is
    implicit is an amount somebody eventually prints as euros."""
    response = _with(HOLDINGS).get(f"/v1/accounts/{CURRENT}/holder-accounts")

    assert response.status_code == 200
    assert response.json()["accounts"][0] == {
        "accountId": str(CURRENT),
        "kind": "CURRENT",
        "displayName": "Cuenta corriente",
        "currency": "EUR",
        "bookedMinor": 227_042,
        "status": "ACTIVE",
    }


def test_the_kind_reaches_the_client() -> None:
    """The field the answer turns on. An ASSET balance is a cost basis and not a valuation, so a
    client that could not tell the kinds apart would sum all three and call it what you have."""
    response = _with(HOLDINGS).get(f"/v1/accounts/{CURRENT}/holder-accounts")

    assert [a["kind"] for a in response.json()["accounts"]] == ["CURRENT", "SAVINGS"]


def test_an_account_nobody_named_has_no_name_rather_than_a_made_up_one() -> None:
    """`display_name` is nullable in the schema and stays nullable here. Falling back to the
    reference — "ES-0001-2" — would put a string nobody recognises where a name goes."""
    unnamed = Holding(CURRENT, AccountKind.CURRENT, None, Money(0, "EUR"), AccountStatus.ACTIVE)

    response = _with((unnamed,)).get(f"/v1/accounts/{CURRENT}/holder-accounts")

    assert response.json()["accounts"][0]["displayName"] is None


def test_there_is_no_total_on_the_response() -> None:
    """Deliberate. Accounts in different currencies do not have one, and a field here would have to
    be filled with something — so the caller decides, being the only one that knows whether it is
    about to print the number or compare it."""
    response = _with(HOLDINGS).get(f"/v1/accounts/{CURRENT}/holder-accounts")

    assert set(response.json()) == {"accounts"}
