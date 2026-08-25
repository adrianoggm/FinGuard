from datetime import date

import pytest

from finguard_orchestrator.application.contextual_query_planner import LegacyPlannerInContext
from finguard_orchestrator.domain.enums import Direction, Period
from finguard_orchestrator.domain.models.planning_context import (
    ContinuationBase,
    ContinuationSource,
    PlanningContext,
)
from finguard_orchestrator.domain.models.planning_result import PlanningResult
from finguard_orchestrator.domain.models.turn_analysis import TurnRelation
from finguard_orchestrator.domain.services.intent_router import Intent
from finguard_orchestrator.domain.value_objects.financial_plan import (
    FinancialPlan,
    RelativePeriod,
)

TODAY = date(2026, 8, 25)
PLAN = FinancialPlan(
    direction=Direction.EXPENSE,
    period=RelativePeriod(Period.LAST_MONTH),
)
RESULT = PlanningResult.planned(PLAN)


class LegacySpy:
    def __init__(self, *, failure: Exception | None = None) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.failure = failure

    async def plan(self, sentence: str, **kwargs: object) -> PlanningResult:
        self.calls.append((sentence, kwargs))
        if self.failure is not None:
            raise self.failure
        return RESULT


def context(continuation: ContinuationBase | None = None) -> PlanningContext:
    return PlanningContext(
        today=TODAY,
        reporting_timezone="Europe/Madrid",
        intent=Intent.FINANCIAL_QUERY,
        relation=TurnRelation.NEW_REQUEST,
        focus=None,
        continuation=continuation,
        awaiting=None,
        account_selection=None,
    )


@pytest.mark.asyncio
async def test_without_continuation_preserves_the_legacy_call_shape() -> None:
    legacy = LegacySpy()

    result = await LegacyPlannerInContext(legacy).plan("pregunta", context=context())

    assert result is RESULT
    assert legacy.calls == [("pregunta", {"today": TODAY, "previous": None})]


@pytest.mark.asyncio
async def test_continuation_propagates_the_exact_previous_plan() -> None:
    legacy = LegacySpy()
    continuation = ContinuationBase(PLAN, ContinuationSource.LAST_ANSWER)

    await LegacyPlannerInContext(legacy).plan("y por meses", context=context(continuation))

    assert legacy.calls == [("y por meses", {"today": TODAY, "previous": PLAN})]
    assert legacy.calls[0][1]["previous"] is PLAN


@pytest.mark.asyncio
async def test_only_forced_continuation_passes_force_follow_up() -> None:
    legacy = LegacySpy()
    continuation = ContinuationBase(
        PLAN,
        ContinuationSource.ACTIVE_CLARIFICATION,
        force_follow_up=True,
    )

    await LegacyPlannerInContext(legacy).plan("este año", context=context(continuation))

    assert legacy.calls == [
        ("este año", {"today": TODAY, "previous": PLAN, "force_follow_up": True})
    ]


@pytest.mark.asyncio
async def test_legacy_exception_is_not_caught_or_replaced() -> None:
    failure = RuntimeError("planner failed")
    legacy = LegacySpy(failure=failure)

    with pytest.raises(RuntimeError) as raised:
        await LegacyPlannerInContext(legacy).plan("pregunta", context=context())

    assert raised.value is failure
    assert len(legacy.calls) == 1
