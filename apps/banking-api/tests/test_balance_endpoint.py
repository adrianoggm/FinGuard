"""The §13.1 endpoint, without a database: only its shape is checked here."""

import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from finguard_banking.application.bus.query_bus import QueryBus
from finguard_banking.application.queries.get_balance import (
    GetBalanceHandler,
    GetBalanceQuery,
)
from finguard_banking.bootstrap.dependencies import get_query_bus
from finguard_banking.bootstrap.main import app
from finguard_banking.domain.balance import Balance
from finguard_banking.domain.errors import AccountNotFoundError
from finguard_banking.domain.value_objects.money import Money

ACCOUNT = uuid.UUID("11111111-1111-1111-1111-111111111111")
MOMENT = datetime(2026, 7, 30, 9, 30, tzinfo=UTC)


class FakeRepository:
    def __init__(self, balance: Balance | None) -> None:
        self._balance = balance

    async def balance_of(self, account_id: uuid.UUID) -> Balance:
        if self._balance is None:
            raise AccountNotFoundError(account_id)
        return self._balance


def _with_repository(balance: Balance | None) -> TestClient:
    # A bus with one fake handler: the controller cannot tell the difference, and no
    # database is involved.
    bus = QueryBus()
    bus.register(GetBalanceQuery, GetBalanceHandler(FakeRepository(balance)))
    app.dependency_overrides[get_query_bus] = lambda: bus
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides() -> None:
    app.dependency_overrides.clear()


def test_it_returns_the_balance_with_the_contract_names() -> None:
    balance = Balance(
        account_id=ACCOUNT,
        available=Money(146035, "EUR"),
        booked=Money(146035, "EUR"),
        as_of=MOMENT,
    )

    response = _with_repository(balance).get(f"/v1/accounts/{ACCOUNT}/balance")

    assert response.status_code == 200
    assert response.json() == {
        "accountId": str(ACCOUNT),
        "availableMinor": 146035,
        "bookedMinor": 146035,
        "currency": "EUR",
        "asOf": "2026-07-30T09:30:00Z",
    }


def test_an_unknown_account_returns_404() -> None:
    response = _with_repository(None).get(f"/v1/accounts/{ACCOUNT}/balance")

    assert response.status_code == 404


def test_an_identifier_that_is_not_a_uuid_never_reaches_the_domain() -> None:
    response = _with_repository(None).get("/v1/accounts/not-a-uuid/balance")

    assert response.status_code == 422
