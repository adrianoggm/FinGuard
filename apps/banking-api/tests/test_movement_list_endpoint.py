from datetime import UTC, datetime
from uuid import UUID

from fastapi.testclient import TestClient

from finguard_banking.application import ListMovementsQuery
from finguard_banking.bootstrap.main import app
from finguard_banking.domain.enums import Direction
from finguard_banking.domain.models.financial_query_result import Movement
from finguard_banking.domain.models.movement_list import MovementCursor, MovementListPage
from finguard_banking.domain.value_objects.money import Money

ACCOUNT = UUID("11111111-1111-1111-1111-111111111111")
TRANSACTION = UUID("33333333-3333-3333-3333-333333333333")
AT = datetime(2026, 10, 9, 12, tzinfo=UTC)


class FakeList:
    def __init__(self) -> None:
        self.asked: ListMovementsQuery | None = None

    async def handle(self, query: ListMovementsQuery) -> MovementListPage:
        self.asked = query
        movement = Movement(
            transaction_id=TRANSACTION,
            occurred_at=AT,
            description="Compra",
            merchant_name="Tienda",
            category=None,
            amount=Money(1299, "EUR"),
            direction="EXPENSE",
        )
        return MovementListPage(
            account_id=query.plan.account_id,
            movements=(movement,),
            has_more=True,
            next_cursor=MovementCursor(AT, TRANSACTION),
        )


def test_periodless_movement_list_route_uses_path_account_and_returns_cursor(monkeypatch) -> None:
    handler = FakeList()
    bus = app.state.query_bus
    original = bus._handlers.get(ListMovementsQuery)  # noqa: SLF001
    bus._handlers[ListMovementsQuery] = handler  # noqa: SLF001
    try:
        response = TestClient(app).post(
            f"/v1/accounts/{ACCOUNT}/movements/list",
            json={"direction": Direction.EXPENSE.value, "pageSize": 1},
        )
    finally:
        if original is None:
            bus._handlers.pop(ListMovementsQuery, None)  # noqa: SLF001
        else:
            bus._handlers[ListMovementsQuery] = original  # noqa: SLF001

    assert response.status_code == 200
    assert response.json()["movements"][0]["amountMinor"] == 1299
    assert response.json()["movements"][0]["currency"] == "EUR"
    assert response.json()["hasMore"] is True
    assert response.json()["nextCursor"]["transactionId"] == str(TRANSACTION)
    assert handler.asked is not None
    assert handler.asked.plan.account_id == ACCOUNT
    assert handler.asked.plan.date_from is None
