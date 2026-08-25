"""Compatibility between typed planning context and the legacy planner contract."""

from dataclasses import dataclass

from finguard_orchestrator.domain.models.planning_context import PlanningContext
from finguard_orchestrator.domain.models.planning_result import PlanningResult
from finguard_orchestrator.domain.ports.query_planner import QueryPlannerPort


@dataclass(frozen=True)
class LegacyPlannerInContext:
    """Expose a legacy planner through the contextual application contract."""

    planner: QueryPlannerPort

    async def plan(
        self,
        sentence: str,
        *,
        context: PlanningContext,
    ) -> PlanningResult:
        continuation = context.continuation
        if continuation is None:
            return await self.planner.plan(sentence, today=context.today, previous=None)
        if continuation.force_follow_up:
            return await self.planner.plan(
                sentence,
                today=context.today,
                previous=continuation.plan,
                force_follow_up=True,
            )
        return await self.planner.plan(
            sentence,
            today=context.today,
            previous=continuation.plan,
        )
