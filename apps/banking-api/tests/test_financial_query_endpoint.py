"""The §13.2 endpoint: the shape of the contract, and which bodies it rejects."""

import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from finguard_banking.application.bus.query_bus import QueryBus
from finguard_banking.application.queries.movement_summary import (
    MovementSummaryHandler,
    MovementSummaryQuery,
)
from finguard_banking.bootstrap.dependencies import get_query_bus
from finguard_banking.bootstrap.main import app
from finguard_banking.domain.errors import AccountNotFoundError
from finguard_banking.domain.models.financial_query import FinancialQuery
from finguard_banking.domain.models.financial_query_result import (
    FinancialQueryResult,
    Movement,
    QueryGroup,
)
from finguard_banking.domain.value_objects.money import Money

ACCOUNT = uuid.UUID("11111111-1111-1111-1111-111111111111")
TRANSACTION = uuid.UUID("22222222-2222-2222-2222-222222222222")

BODY = {
    "accountId": str(ACCOUNT),
    "metric": "SUM",
    "direction": "EXPENSE",
    "categories": ["TRANSPORT_FUEL"],
    "dateRange": {"from": "2026-07-01", "to": "2026-07-31"},
    "groupBy": "WEEK",
}

RESULT = FinancialQueryResult(
    account_id=ACCOUNT,
    total=Money(12_743, "EUR"),
    movement_count=3,
    groups=[QueryGroup(key="2026-07-06", total=Money(3_420, "EUR"), movement_count=1)],
    supporting_movements=[
        Movement(
            transaction_id=TRANSACTION,
            occurred_at=datetime(2026, 7, 7, 10, 30, tzinfo=UTC),
            description="Compra en Repsol",
            merchant_name="Repsol",
            category="TRANSPORT_FUEL",
            amount=Money(3_420, "EUR"),
            direction="EXPENSE",
        )
    ],
)


class FakeRepository:
    def __init__(self, result: FinancialQueryResult | None) -> None:
        self._result = result
        self.received: FinancialQuery | None = None

    async def run(self, query: FinancialQuery) -> FinancialQueryResult:
        self.received = query
        if self._result is None:
            raise AccountNotFoundError(query.account_id)
        return self._result


def _bus_with(repository: FakeRepository) -> QueryBus:
    bus = QueryBus()
    bus.register(MovementSummaryQuery, MovementSummaryHandler(repository))
    return bus


def _with_repository(result: FinancialQueryResult | None) -> TestClient:
    app.dependency_overrides[get_query_bus] = lambda: _bus_with(FakeRepository(result))
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clear_overrides() -> None:
    app.dependency_overrides.clear()


def test_it_answers_with_the_contract_names() -> None:
    response = _with_repository(RESULT).post("/v1/financial-queries", json=BODY)

    assert response.status_code == 200
    assert response.json() == {
        "accountId": str(ACCOUNT),
        "currency": "EUR",
        "totalMinor": 12_743,
        "movementCount": 3,
        "periodStart": "2026-07-01",
        "periodEnd": "2026-07-31",
        "groups": [{"key": "2026-07-06", "totalMinor": 3_420, "movementCount": 1}],
        "supportingMovements": [
            {
                "transactionId": str(TRANSACTION),
                "occurredAt": "2026-07-07T10:30:00Z",
                "description": "Compra en Repsol",
                "merchantName": "Repsol",
                "category": "TRANSPORT_FUEL",
                # False here because this fake movement carries no override. The flag is on the
                # contract so a screen can say whose opinion the category is.
                "categoryIsCustom": False,
                # Which of the four layers decided it, and whether the money left the household.
                # NONE and false on a fake that sets neither — and the defaults are what an older
                # caller sees, which is the same answer it saw before these existed.
                "categorySource": "NONE",
                "isInternal": False,
                "amountMinor": 3_420,
                "direction": "EXPENSE",
            }
        ],
        "trace": None,
    }


def test_an_unknown_account_returns_404() -> None:
    response = _with_repository(None).post("/v1/financial-queries", json=BODY)

    assert response.status_code == 404


def test_average_and_a_selected_account_subset_reach_the_domain() -> None:
    repository = FakeRepository(RESULT)
    app.dependency_overrides[get_query_bus] = lambda: _bus_with(repository)
    selected = [uuid.UUID(int=3), uuid.UUID(int=4)]

    response = TestClient(app).post(
        "/v1/financial-queries",
        json={
            **BODY,
            "metric": "AVERAGE",
            "onlyAccountIds": [str(account_id) for account_id in selected],
        },
    )

    assert response.status_code == 200
    assert repository.received is not None
    assert repository.received.metric.value == "AVERAGE"
    assert repository.received.only_account_ids == tuple(selected)


def test_internal_only_reaches_the_domain_only_with_its_explicit_opt_in() -> None:
    repository = FakeRepository(RESULT)
    app.dependency_overrides[get_query_bus] = lambda: _bus_with(repository)

    response = TestClient(app).post(
        "/v1/financial-queries",
        json={**BODY, "includeInternal": True, "internalOnly": True},
    )

    assert response.status_code == 200
    assert repository.received is not None
    assert repository.received.include_internal is True
    assert repository.received.internal_only is True


def test_internal_only_without_internal_opt_in_is_rejected() -> None:
    response = _with_repository(RESULT).post(
        "/v1/financial-queries", json={**BODY, "internalOnly": True}
    )

    assert response.status_code == 422


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("groupBy", "QUARTER"),
        ("direction", "EVERYTHING"),
        ("metric", "MEDIAN"),
        ("supportingLimit", 5_000),
        ("categories", ["fuel; DROP TABLE accounts"]),
    ],
)
def test_a_plan_outside_the_contract_is_rejected(field: str, value: object) -> None:
    response = _with_repository(RESULT).post("/v1/financial-queries", json={**BODY, field: value})

    assert response.status_code == 422


def test_sql_cannot_be_smuggled_in_as_an_extra_field() -> None:
    response = _with_repository(RESULT).post(
        "/v1/financial-queries", json={**BODY, "sql": "SELECT * FROM ledger_entries"}
    )

    assert response.status_code == 422


def test_a_relative_period_is_resolved_before_reaching_the_domain() -> None:
    repository = FakeRepository(RESULT)
    app.dependency_overrides[get_query_bus] = lambda: _bus_with(repository)
    body = {k: v for k, v in BODY.items() if k != "dateRange"}

    response = TestClient(app).post(
        "/v1/financial-queries",
        json={**body, "period": {"period": "LAST_MONTH", "reference": "2026-07-15"}},
    )

    assert response.status_code == 200
    assert repository.received is not None
    # The domain only ever sees absolute dates: resolution happens at the edge.
    assert repository.received.date_range.start.isoformat() == "2026-06-01"
    assert repository.received.date_range.end.isoformat() == "2026-06-30"


def test_the_period_must_be_given_exactly_one_way() -> None:
    client = _with_repository(RESULT)
    without_range = {k: v for k, v in BODY.items() if k != "dateRange"}

    both = client.post("/v1/financial-queries", json={**BODY, "period": {"period": "LAST_MONTH"}})
    neither = client.post("/v1/financial-queries", json=without_range)

    assert both.status_code == 422
    assert neither.status_code == 422


def test_a_backwards_range_is_rejected() -> None:
    response = _with_repository(RESULT).post(
        "/v1/financial-queries",
        json={**BODY, "dateRange": {"from": "2026-07-31", "to": "2026-07-01"}},
    )

    assert response.status_code == 422
