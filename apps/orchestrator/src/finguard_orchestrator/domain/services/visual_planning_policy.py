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

from finguard_orchestrator.domain.errors import InvalidValueError, ProviderFailedError
from finguard_orchestrator.domain.models.chart import (
    ChartSpec,
    PresentationContext,
    VisualDecision,
    VisualProfile,
)
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
)
from finguard_orchestrator.observability.decisions import Step, Verdict, record

Verifier = Callable[[VisualPlanDraft, VisualProfile], ChartSpec | Rejection]

logger = logging.getLogger(__name__)


def _record_choice(
    mode: str,
    accepted: bool,
    reason: "FallbackReason | None",
    rejected: Rejection | None,
    shown: ChartSpec,
) -> None:
    """Who chose the chart on screen, and why the rules' chart when it was theirs.

    `agreed` is the model's chart, verified and shown. `overrode` is the rules refusing the model's
    proposal and showing their own -- a real substitution, so `changed`. `failed` is no proposal in
    time or from a working provider, and `declined` the model choosing not to propose: in both the
    rules' chart is shown, but nothing of the model's was replaced. The exact reason rides on the
    span; the counter's labels stay the bounded three.
    """
    if mode == "shadow":
        verdict, detail = Verdict.AGREED, "shadow"
    elif accepted:
        verdict, detail = Verdict.AGREED, "llm"
    elif reason == "invalid_proposal":
        verdict = Verdict.OVERRODE
        detail = f"invalid_proposal:{rejected.rule}" if rejected is not None else "invalid_proposal"
    elif reason == "abstention":
        verdict, detail = Verdict.DECLINED, "abstention"
    else:
        verdict, detail = Verdict.FAILED, reason or "no_proposal"
    record(
        Step.VISUAL_PLANNER,
        verdict,
        changed=verdict is Verdict.OVERRODE,
        now=shown.mark,
        detail=detail,
    )


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
            raise InvalidValueError("unsupported visual planning mode")
        if budget_seconds <= 0:
            raise InvalidValueError("the visual budget must be positive")
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
            # `claimed`, not `agreed`: no model was asked, so the rules took the choice outright,
            # and a share of `agreed` charts has to mean the model's.
            record(
                Step.VISUAL_PLANNER, Verdict.CLAIMED, changed=False, now=floor.mark, detail="rules"
            )
            return VisualDecision(spec=floor, profile=profile, source="deterministic")

        # `self._model` is narrowed non-None by the branch above; captured into a local so the
        # nested coroutine below closes over a plain `VisualPlannerPort`, not an `Optional` mypy
        # would widen `self._model` back to inside a new function scope.
        model = self._model
        started = time.monotonic()
        proposed_spec: ChartSpec | None = None
        rejected: Rejection | None = None
        reason: FallbackReason | None = None
        error_detail: str | None = None
        proposed = False
        draft: VisualPlanDraft | None = None

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
        except TimeoutError:
            reason = "timeout"
        except ProviderFailedError:
            proposed = True
            reason = "provider_error"
        except Exception as exc:
            # U-8 (Wave 3 hardening): this used to be one broad `except Exception` covering both
            # "the adapter that parses a model's answer does not exist yet" (a fact about the
            # experiment) *and* a real bug in that adapter -- and recorded both as an
            # indistinguishable `provider_error`, with the traceback thrown away. `FallbackReason`
            # stays the four the wire seals (Task 1 of 4.5): still `provider_error`, the floor
            # still shows -- but logged at ERROR with the exception's own type and traceback, and
            # `error_detail` carries `"code_error:<ExcType>"` into the promotion row, so a bug in
            # this adapter is no longer indistinguishable from the provider actually being down.
            # Safe for `CancelledError`, a `BaseException` since 3.8, which this clause never sees.
            proposed = True
            reason = "provider_error"
            error_detail = f"code_error:{type(exc).__name__}"
            logger.error("visual_planner.code_error type=%s", type(exc).__name__, exc_info=exc)
        latency_ms = int((time.monotonic() - started) * 1000)

        # A-15 (design audit): `verify()` used to run inside the try block above, so a bug in the
        # *verifier* -- not the model adapter -- was caught by the same broad `except Exception`
        # and misreported as a provider fallback. Nothing here catches it: a verifier bug is now
        # an exception that reaches the caller as itself, the way U-8 already treats a bug in the
        # adapter as a fact about this turn's code rather than about the provider.
        if reason is None:
            if draft is None:
                reason = "abstention"
            else:
                verified = self._verifier(draft, profile)
                if isinstance(verified, Rejection):
                    rejected, reason = verified, "invalid_proposal"
                else:
                    proposed_spec = verified

        if floor is None and proposed_spec is None:
            floor = self._fallback.plan(profile, presentation)
            if floor is None:
                return None
        if floor is None and proposed_spec is not None and self._mode == "promoted":
            # D-H-4 (fix wave, minor): `self._mode == "promoted"` made explicit rather than left
            # to be true by construction -- `floor` reaches this line still `None` only when the
            # early guard above (`if self._mode != "promoted":`) did not run, and that guard is
            # the only place `floor` is ever left unset, so this condition used to be correct by
            # control-flow tracing rather than by anything this branch itself said. Stated here,
            # it also stops meaning something different the day a fifth mode is added: a future
            # mode that also leaves `floor` unset for a different reason would silently recompute
            # it here otherwise, rather than leaving `changed_from_baseline` honestly `None`.
            #
            # A-5 (Wave 3 hardening): `promoted`'s own accepted-proposal branch used to skip the
            # floor entirely (R2), which is exactly why `changed_from_baseline` could never be
            # measured in the one mode it exists to justify. Computed here too now, but only for
            # the comparison below -- never gating on the result the way the eligibility guard
            # does above: a floor that declines (a one-row profile) must not take back a valid
            # model proposal `promoted` already decided to show
            # (`test_promoted_trusts_the_eligibility_guard`). `floor is None` stays a legitimate
            # outcome here, read by `changed` below as "nothing to compare against".
            floor = self._fallback.plan(profile, presentation)
        changed = (
            canonical(proposed_spec) != canonical(floor)
            if proposed_spec is not None and floor is not None
            else None
        )
        accept = proposed_spec is not None and self._mode in {"hybrid", "promoted"}
        shown = proposed_spec if accept else floor
        assert shown is not None
        _record_choice(self._mode, accept, reason, rejected, shown)
        return VisualDecision(
            spec=shown,
            profile=profile,
            source="llm" if accept else "deterministic",
            # `draft.rationale` is never read: it is model-written free text that can echo the
            # question back, and the question must never reach a record (Section E's guarantee).
            #
            # A-4 (Wave 3 hardening): `None` in `shadow` regardless of what `reason` holds -- the
            # floor is always what shadow shows (`accept` above is never `True` in this mode), so
            # nothing here ever fell back to it; recording a reason anyway told the holder's own
            # explanation (`visual_explanation._provenance`) that a fallback happened when the
            # floor was the intended selection all along, and corrupted the very rate Task 9 reads
            # as promotion evidence.
            fallback_reason=None if self._mode == "shadow" else reason,
            proposed=proposed,
            valid=proposed_spec is not None,
            accepted=accept,
            changed_from_baseline=changed,
            latency_ms=latency_ms,
            proposed_spec=proposed_spec,
            rejected_rule=rejected.rule if rejected is not None else None,
            error_detail=None if self._mode == "shadow" else error_detail,
        )
