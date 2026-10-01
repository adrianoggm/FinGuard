"""I-2 (fix wave): the evidence turn asks the ledger for exactly the movements it re-lists.

Before this, `self._answer(query, referenced_result)` passed no `movements=` at all, so the real
adapter (`infrastructure/banking/http_financial_query.py`) substituted the deployment's own
`supporting_movements` default (10, `compose.yaml`) against up to `MAX_SELECTED_TRANSACTIONS` (50)
ids the drill had just selected -- «Enséñame los movimientos responsables.» re-listed at most ten
of the up-to-fifty rows the drill showed, not "that evidence in one call" as promised. The same
`_referenced_result_plan` branch also answers a pre-existing «esos movimientos» reference with no
drill involved at all, so this fix serves both -- one line, one test file for the two shapes.
"""

from __future__ import annotations

from uuid import UUID

from test_decisions_call_sites import turn_for

from finguard_orchestrator.domain.enums import Direction, GroupBy, Period
from finguard_orchestrator.domain.models.conversation import Conversation
from finguard_orchestrator.domain.models.query_answer import QueryAnswer
from finguard_orchestrator.domain.models.result_reference import (
    ResultReference,
    answered_with_result,
)
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan, RelativePeriod

SOURCE = FinancialPlan(
    direction=Direction.EXPENSE,
    period=RelativePeriod(Period.THIS_YEAR),
    categories=("GROCERIES",),
    group_by=GroupBy.CATEGORY,
)


class RecordingLedger:
    """Every `run(...)` call's own kwargs -- what `movements=` this turn actually asked for."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def run(
        self,
        plan: object,
        *,
        account_id: object,
        today: object,
        reporting_timezone: str = "Europe/Madrid",
        **kwargs: object,
    ) -> QueryAnswer:
        self.calls.append(kwargs)
        return QueryAnswer(currency="EUR", total_minor=18_000, movement_count=3)

    async def ranked(
        self, ranking: object, *, account_id: object, today: object, reporting_timezone: str
    ) -> None:
        return None


def _referenced(
    *, transaction_ids: tuple[UUID, ...] = (), group_by: GroupBy = GroupBy.CATEGORY
) -> Conversation:
    return answered_with_result(
        Conversation(),
        ResultReference(
            SOURCE,
            category="GROCERIES",
            group_by=group_by,
            group_key="GROCERIES",
            transaction_ids=transaction_ids,
        ),
    )


async def test_the_evidence_turn_asks_for_exactly_its_ids_count() -> None:
    ledger = RecordingLedger()
    selected = (UUID(int=1), UUID(int=2), UUID(int=3))

    result, _ = await turn_for(
        "Cuánto suman esos movimientos?",
        SOURCE,
        ledger=ledger,
        conversation=_referenced(transaction_ids=selected),
    )

    assert len(ledger.calls) == 1
    assert ledger.calls[0]["movements"] == 3
    assert result.outcome == "PLANNED"


async def test_a_referenced_path_without_ids_asks_for_no_movements_cap() -> None:
    """The category-reference branch («esa categoría») clears `transaction_ids` on its own plan --
    it is not the evidence path -- so this line changes nothing for it."""
    ledger = RecordingLedger()

    result, _ = await turn_for(
        "Cuánto gasté en esa categoría?",
        SOURCE,
        ledger=ledger,
        conversation=_referenced(),
    )

    assert len(ledger.calls) == 1
    assert "movements" not in ledger.calls[0]
    assert result.outcome == "PLANNED"
