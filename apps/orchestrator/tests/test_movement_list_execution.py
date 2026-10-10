from datetime import UTC, datetime
from uuid import UUID

import pytest

from finguard_orchestrator.application.engine.executor import MovementListRun, execute
from finguard_orchestrator.domain.engine.transitions import Work
from finguard_orchestrator.domain.enums import Direction
from finguard_orchestrator.domain.models.query_answer import Movement, MovementListAnswer
from finguard_orchestrator.domain.value_objects.movement_list_plan import (
    MovementCursor,
    MovementListPlan,
)

ACCOUNT = UUID("11111111-1111-1111-1111-111111111111")
TX = UUID("33333333-3333-3333-3333-333333333333")
AT = datetime(2026, 10, 7, 9, tzinfo=UTC)


class Queries:
    def __init__(self) -> None:
        self.plan: MovementListPlan | None = None

    async def list_movements(self, plan: MovementListPlan, **_: object) -> MovementListAnswer:
        self.plan = plan
        return MovementListAnswer(
            account_id=ACCOUNT,
            movements=(
                Movement(
                    occurred_at=AT,
                    description="Compra",
                    amount_minor=1200,
                    direction="EXPENSE",
                    transaction_id=str(TX),
                    currency="EUR",
                ),
            ),
            has_more=True,
            next_cursor=MovementCursor(AT, TX),
        )


class UnusedSummary:
    async def of(self, *_: object, **__: object) -> None:
        raise AssertionError("a movement list must not query balances")


@pytest.mark.asyncio
async def test_executor_runs_periodless_list_through_its_dedicated_port_method() -> None:
    plan = MovementListPlan(direction=Direction.EXPENSE, page_size=1)
    queries = Queries()

    run = await execute(
        Work("MOVEMENT_LIST", plan),
        queries=queries,
        summary=UnusedSummary(),
        account_id=ACCOUNT,
        today=AT.date(),
        reporting_timezone="Europe/Madrid",
    )

    assert isinstance(run, MovementListRun)
    assert queries.plan == plan
    assert run.answer.movements[0].transaction_id == str(TX)
    assert run.answer.next_cursor == MovementCursor(AT, TX)
