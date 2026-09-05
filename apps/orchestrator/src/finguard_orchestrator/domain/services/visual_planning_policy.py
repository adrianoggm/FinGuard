"""The floor is availability, fallback and experimental baseline at once -- so it is never the
port's default implementation, and this is the one place that holds both planners.

`None` from the port is abstention and nothing else; the other three failures arrive as a timeout
the policy imposes, a `ProviderFailedError`, or a `Rejection` from the verifier. Each becomes a
different `fallback_reason`, because a single value for all four would make "why did it fall back"
unwritable -- `PlanningOutcome`'s own argument.
"""

import asyncio
import time
from collections.abc import Callable
from typing import Literal

from finguard_orchestrator.domain.errors import ProviderFailedError
from finguard_orchestrator.domain.models.chart import ChartSpec, PresentationContext, VisualProfile
from finguard_orchestrator.domain.models.visual_plan import Rejection, VisualPlanDraft, canonical
from finguard_orchestrator.domain.ports.visual_planner import VisualPlannerPort
from finguard_orchestrator.domain.services.deterministic_visual_planner import (
    DeterministicVisualPlanner,
    VisualDecision,
)

VisualMode = Literal["deterministic", "shadow", "hybrid", "promoted"]
FallbackReason = Literal["abstention", "timeout", "provider_error", "invalid_proposal"]
Verifier = Callable[[VisualPlanDraft, VisualProfile], ChartSpec | Rejection]


class VisualPlanningPolicy:
    def __init__(
        self,
        *,
        model: VisualPlannerPort | None,
        fallback: DeterministicVisualPlanner,
        verifier: Verifier,
        mode: VisualMode,
        budget_seconds: float,
    ) -> None:
        self._model = model
        self._fallback = fallback
        self._verifier = verifier
        self._mode = mode
        self._budget = budget_seconds

    async def decide(
        self, question: str, profile: VisualProfile, presentation: PresentationContext
    ) -> VisualDecision | None:
        floor = None
        if self._mode != "promoted":
            floor = self._fallback.plan(profile, presentation)
            if floor is None:
                return None  # the floor declines (one row, no measure): nothing to fall back to
        if self._mode == "deterministic" or self._model is None:
            assert floor is not None
            return VisualDecision(spec=floor, profile=profile, source="deterministic")

        started = time.monotonic()
        proposed_spec: ChartSpec | None = None
        rejected: Rejection | None = None
        reason: FallbackReason | None = None
        proposed = False
        try:
            draft = await asyncio.wait_for(
                self._model.plan(question, profile, presentation, budget_seconds=self._budget),
                timeout=self._budget,
            )
            proposed = True
            if draft is None:
                reason = "abstention"
            else:
                verified = self._verifier(draft, profile)
                if isinstance(verified, Rejection):
                    rejected, reason = verified, "invalid_proposal"
                else:
                    proposed_spec = verified
        except TimeoutError:
            reason = "timeout"
        except ProviderFailedError:
            proposed = True
            reason = "provider_error"
        latency_ms = int((time.monotonic() - started) * 1000)

        if floor is None and proposed_spec is None:
            floor = self._fallback.plan(profile, presentation)
            if floor is None:
                return None
        changed = (
            canonical(proposed_spec) != canonical(floor)
            if proposed_spec is not None and floor is not None
            else None
        )
        accept = proposed_spec is not None and self._mode in {"hybrid", "promoted"}
        shown = proposed_spec if accept else floor
        assert shown is not None
        return VisualDecision(
            spec=shown,
            profile=profile,
            source="llm" if accept else "deterministic",
            fallback_reason=reason,
            proposed=proposed,
            valid=proposed_spec is not None,
            accepted=accept,
            changed_from_baseline=changed,
            latency_ms=latency_ms,
            proposed_spec=proposed_spec,
            rejected_rule=rejected.rule if rejected is not None else None,
        )
