"""The one contract a visual planner fulfils. See the design's Section B."""

from typing import Protocol, runtime_checkable

from finguard_orchestrator.domain.models.chart import PresentationContext, VisualProfile
from finguard_orchestrator.domain.models.visual_plan import VisualPlanDraft


@runtime_checkable
class VisualPlannerPort(Protocol):
    async def plan(
        self,
        question: str,
        profile: VisualProfile,
        presentation: PresentationContext,
        *,
        budget_seconds: float,
    ) -> VisualPlanDraft | None:
        """Propose a chart for this profile, or return `None` to abstain.

        `None` means abstention and nothing else. A provider that cannot answer raises
        `ProviderFailedError`; a call that outlives `budget_seconds` is cancelled by the policy,
        which is the only place that holds the floor to fall back to. The question is an argument
        and never a profile field: the model sees it, nothing that round-trips does.
        """
        ...
