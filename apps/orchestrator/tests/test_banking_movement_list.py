from datetime import UTC, datetime
from uuid import UUID

import pytest

from finguard_orchestrator.domain.enums import Direction
from finguard_orchestrator.domain.value_objects.movement_list_plan import (
    MovementCursor,
    MovementListPlan,
)
from finguard_orchestrator.infrastructure.banking import http_financial_query
from finguard_orchestrator.infrastructure.banking.http_financial_query import HttpFinancialQuery

ACCOUNT = UUID("11111111-1111-1111-1111-111111111111")
OTHER = UUID("22222222-2222-2222-2222-222222222222")
TX = UUID("33333333-3333-3333-3333-333333333333")
AT = datetime(2026, 10, 9, 12, tzinfo=UTC)


class Response:
    status_code = 200

    def json(self) -> dict[str, object]:
        return {
            "accountId": str(ACCOUNT),
            "movements": [
                {
                    "transactionId": str(TX),
                    "occurredAt": AT.isoformat(),
                    "description": "Compra",
                    "merchantName": "Tienda",
                    "category": "GROCERIES",
                    "categoryIsCustom": False,
                    "categorySource": "BANK",
                    "isInternal": False,
                    "amountMinor": 1299,
                    "direction": "EXPENSE",
                    "currency": "EUR",
                }
            ],
            "hasMore": True,
            "nextCursor": {"occurredAt": AT.isoformat(), "transactionId": str(TX)},
        }


class Client:
    def __init__(self) -> None:
        self.request: tuple[str, dict[str, object]] | None = None

    async def __aenter__(self) -> "Client":
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def post(self, url: str, *, json: dict[str, object]) -> Response:
        self.request = (url, json)
        return Response()


@pytest.mark.asyncio
async def test_movement_list_adapter_keeps_the_newest_page_contract(monkeypatch) -> None:
    client = Client()
    monkeypatch.setattr(http_financial_query, "banking_client", lambda **_: client)
    plan = MovementListPlan(
        direction=Direction.EXPENSE,
        page_size=1,
        cursor=MovementCursor(AT, TX),
        account_ids=(OTHER,),
        categories=("GROCERIES",),
    )

    page = await HttpFinancialQuery(
        base_url="http://banking", timeout_seconds=1, supporting_movements=5
    ).list_movements(plan, account_id=ACCOUNT, reporting_timezone="Europe/Madrid")

    assert client.request is not None
    url, body = client.request
    assert url == f"http://banking/v1/accounts/{ACCOUNT}/movements/list"
    assert body["pageSize"] == 1
    assert "period" not in body and "dateRange" not in body
    assert page.movements[0].amount_minor == 1299
    assert page.movements[0].currency == "EUR"
    assert page.has_more
    assert page.next_cursor == MovementCursor(AT, TX)
