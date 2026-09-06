"""The floor is availability, fallback and experimental baseline at once -- so it is never the
port's default implementation, and this is the one place that holds both planners.

`None` from the port is abstention and nothing else; the other three failures arrive as a timeout
the policy imposes, a `ProviderFailedError`, or a `Rejection` from the verifier. Each becomes a
different `fallback_reason`, because a single value for all four would make "why did it fall back"
unwritable -- `PlanningOutcome`'s own argument.
"""

import asyncio
import logging
import time
from collections.abc import Callable
from typing import get_args

from finguard_orchestrator.domain.errors import ProviderFailedError
from finguard_orchestrator.domain.models.chart import ChartSpec, PresentationContext, VisualProfile
from finguard_orchestrator.domain.models.visual_plan import (
    FallbackReason,
    Rejection,
    VisualMode,
    VisualPlanDraft,
    canonical,
)
from finguard_orchestrator.domain.ports.visual_planner import VisualPlannerPort
from finguard_orchestrator.domain.services.deterministic_visual_planner import (
    DeterministicVisualPlanner,
    VisualDecision,
)

Verifier = Callable[[VisualPlanDraft, VisualProfile], ChartSpec | Rejection]

logger = logging.getLogger(__name__)


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
        if mode not in get_args(VisualMode):
            raise ValueError("unsupported visual planning mode")
        if budget_seconds <= 0:
            raise ValueError("the visual budget must be positive")
        if mode != "deterministic" and model is None:
            # Unreachable through `main.py` today -- it always builds the adapter off
            # `deterministic` -- but a `hybrid`/`promoted` deployment whose model failed to wire
            # would otherwise record `not_run` forever, indistinguishable from `deterministic`,
            # with nothing telling the operator their mode setting is not doing anything.
            logger.warning("visual_planner.no_model mode=%s", mode)
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
            # `promoted` trades the floor's own decline (a one-row or measure-less profile) for
            # not paying for the baseline (R2): the eligibility guard that would normally refuse
            # such a profile runs only in the other three modes here, and is the application's
            # precondition of entering the policy at all, not this method's job to re-derive.
            floor = self._fallback.plan(profile, presentation)
            if floor is None:
                return None  # the floor declines (one row, no measure): nothing to fall back to
        if self._mode == "deterministic" or self._model is None:
            # `promoted` left `floor` unset above (R2); the no-model path still has to show
            # something, so it is computed here rather than asserted into existence -- the state
            # is reachable (`promoted` with no model wired), not a bug an assert should catch.
            if floor is None:
                floor = self._fallback.plan(profile, presentation)
                if floor is None:
                    return None
            return VisualDecision(spec=floor, profile=profile, source="deterministic")

        # `self._model` is narrowed non-None by the branch above; captured into a local so the
        # nested coroutine below closes over a plain `VisualPlannerPort`, not an `Optional` mypy
        # would widen `self._model` back to inside a new function scope.
        model = self._model
        started = time.monotonic()
        proposed_spec: ChartSpec | None = None
        rejected: Rejection | None = None
        reason: FallbackReason | None = None
        proposed = False

        async def _call() -> VisualPlanDraft | None:
            try:
                return await model.plan(
                    question, profile, presentation, budget_seconds=self._budget
                )
            except TimeoutError as exc:
                # The provider's own timeout, never the budget's: `wait_for`'s cancellation
                # reaches this coroutine as `CancelledError`, not `TimeoutError`, so this cannot
                # mask a real budget expiry -- only a `TimeoutError` the adapter itself raises
                # (its own socket/connect timeout) is caught here.
                raise ProviderFailedError("visual", str(exc)) from exc

        try:
            draft = await asyncio.wait_for(_call(), timeout=self._budget)
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
        except Exception:
            # Deliberately broad, mirroring `turn_interpreters.py:116`: the adapter that parses a
            # model's answer does not exist yet, and whatever it throws while doing so -- malformed
            # JSON, a missing key -- is a fact about the experiment, not about this turn. The floor
            # is the availability guarantee, and no adapter exception may take it down. Safe for
            # `CancelledError`, a `BaseException` since 3.8, which this clause never sees.
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
            # `draft.rationale` is never read: it is model-written free text that can echo the
            # question back, and the question must never reach a record (Section E's guarantee).
            fallback_reason=reason,
            proposed=proposed,
            valid=proposed_spec is not None,
            accepted=accept,
            changed_from_baseline=changed,
            latency_ms=latency_ms,
            proposed_spec=proposed_spec,
            rejected_rule=rejected.rule if rejected is not None else None,
        )
