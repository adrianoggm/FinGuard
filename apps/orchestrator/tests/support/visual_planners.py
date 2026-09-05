"""A visual planner a test can script: a draft, an abstention, a failure or a stall."""

import asyncio
from dataclasses import dataclass, field

from finguard_orchestrator.domain.models.chart import (
    PresentationContext,
    VisualDataset,
    VisualProfile,
    VisualRow,
)
from finguard_orchestrator.domain.models.visual_plan import (
    ProposedEncoding,
    ProposedSort,
    VisualPlanDraft,
)
from finguard_orchestrator.domain.services.visual_profile import visual_profile_of

# Shared by the policy and seam tests, so a change to "what a breakdown looks like" is one edit.
BREAKDOWN_PROFILE = visual_profile_of(
    VisualDataset(metric="SUM", rows=(VisualRow("ALQUILER", 51_300), VisualRow("OCIO", 4_000))),
    total_minor=None,
    group_by="NONE",
)
# The floor draws a horizontal ranked bar; this is the same chart turned vertical -- valid, and
# semantically different, which is what changed_from_baseline exists to see.
VERTICAL_DRAFT = VisualPlanDraft(
    "bar",
    (
        ProposedEncoding("x", "dimension", "nominal", ProposedSort("descending", "value_minor")),
        ProposedEncoding("y", "value_minor", "quantitative"),
    ),
)


@dataclass
class ScriptedVisualPlanner:
    outcome: VisualPlanDraft | BaseException | float | None
    received: list[tuple[str, VisualProfile, PresentationContext, float]] = field(
        default_factory=list
    )

    async def plan(
        self,
        question: str,
        profile: VisualProfile,
        presentation: PresentationContext,
        *,
        budget_seconds: float,
    ) -> VisualPlanDraft | None:
        self.received.append((question, profile, presentation, budget_seconds))
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        if isinstance(self.outcome, float):
            await asyncio.sleep(self.outcome)
            return None
        return self.outcome
