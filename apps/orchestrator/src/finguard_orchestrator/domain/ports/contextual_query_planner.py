from typing import Protocol

from finguard_orchestrator.domain.models.planning_context import PlanningContext
from finguard_orchestrator.domain.models.planning_result import PlanningResult


class ContextualQueryPlannerPort(Protocol):
    """Plans one sentence from the application's validated conversational context."""

    async def plan(
        self,
        sentence: str,
        *,
        context: PlanningContext,
    ) -> PlanningResult: ...
