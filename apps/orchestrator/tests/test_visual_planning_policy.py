"""Every row of the design's mode table, plus the budget and the four fallback reasons."""

from typing import get_args

import pytest
from support.visual_planners import (
    BREAKDOWN_PROFILE as PROFILE,
)
from support.visual_planners import (
    VERTICAL_DRAFT as VERTICAL,
)
from support.visual_planners import (
    ScriptedVisualPlanner,
)

from finguard_orchestrator.domain.errors import ProviderFailedError
from finguard_orchestrator.domain.models.chart import DEFAULT_PRESENTATION, VisualDataset, VisualRow
from finguard_orchestrator.domain.models.visual_plan import FallbackReason, VisualMode, canonical
from finguard_orchestrator.domain.models.visual_plan import ProposedEncoding as E
from finguard_orchestrator.domain.models.visual_plan import VisualPlanDraft as D
from finguard_orchestrator.domain.services.deterministic_visual_planner import (
    DeterministicVisualPlanner,
)
from finguard_orchestrator.domain.services.visual_plan_verifier import verify
from finguard_orchestrator.domain.services.visual_planning_policy import VisualPlanningPolicy
from finguard_orchestrator.domain.services.visual_profile import visual_profile_of

SIDEWAYS = D("sideways", (E("x", "dimension", "nominal"),))
# The three modes that attempt the model at all -- `deterministic` never reaches the try block,
# so a `fallback_reason` assertion against one of the four outcomes would never apply to it.
MODES_THAT_ATTEMPT_THE_MODEL = tuple(
    mode for mode in get_args(VisualMode) if mode != "deterministic"
)


class _CountingFloor(DeterministicVisualPlanner):
    """Proves R2's eager/lazy split by counting, not by inferring it from a side effect."""

    def __init__(self) -> None:
        self.calls = 0

    def plan(self, profile, presentation):
        self.calls += 1
        return super().plan(profile, presentation)


def policy(model, mode, budget=1.0, fallback=None):
    return VisualPlanningPolicy(
        model=model,
        fallback=fallback if fallback is not None else DeterministicVisualPlanner(),
        verifier=verify,
        mode=mode,
        budget_seconds=budget,
    )


def test_an_unsupported_mode_is_refused() -> None:
    with pytest.raises(ValueError, match="unsupported visual planning mode"):
        policy(ScriptedVisualPlanner(None), "hybird")


@pytest.mark.parametrize("mode", get_args(VisualMode))
def test_every_declared_mode_constructs(mode) -> None:
    policy(ScriptedVisualPlanner(None), mode)


def test_a_non_positive_budget_is_refused() -> None:
    with pytest.raises(ValueError, match="visual budget must be positive"):
        policy(ScriptedVisualPlanner(None), "hybrid", budget=0.0)


async def test_deterministic_never_runs_the_model() -> None:
    model = ScriptedVisualPlanner(VERTICAL)
    decision = await policy(model, "deterministic").decide("q", PROFILE, DEFAULT_PRESENTATION)
    assert model.received == []
    assert (
        decision.source == "deterministic"
        and decision.proposed is False
        and decision.accepted is False
    )
    assert canonical(decision.spec) == canonical(
        DeterministicVisualPlanner().plan(PROFILE, DEFAULT_PRESENTATION)
    )
    # This is the row where every optional field stays at its default: nothing was attempted.
    assert (
        decision.valid,
        decision.latency_ms,
        decision.proposed_spec,
        decision.changed_from_baseline,
        decision.fallback_reason,
    ) == (False, None, None, None, None)


@pytest.mark.parametrize("mode", get_args(VisualMode))
async def test_without_a_model_every_mode_shows_the_floor(mode) -> None:
    """`promoted` is the one mode that otherwise never computes the floor (R2) -- with no model to
    ask, there is nothing else to show, in every mode alike."""
    decision = await policy(None, mode).decide("q", PROFILE, DEFAULT_PRESENTATION)
    floor = DeterministicVisualPlanner().plan(PROFILE, DEFAULT_PRESENTATION)
    assert decision.source == "deterministic"
    assert decision.proposed is False
    assert decision.latency_ms is None
    assert canonical(decision.spec) == canonical(floor)


async def test_shadow_runs_both_and_shows_the_floor() -> None:
    decision = await policy(ScriptedVisualPlanner(VERTICAL), "shadow").decide(
        "q", PROFILE, DEFAULT_PRESENTATION
    )
    floor = DeterministicVisualPlanner().plan(PROFILE, DEFAULT_PRESENTATION)
    assert canonical(decision.spec) == canonical(floor)  # the user gets the floor
    assert decision.proposed and decision.valid and not decision.accepted
    assert decision.source == "deterministic" and decision.fallback_reason is None
    assert decision.changed_from_baseline is True  # and the comparison was collected
    assert canonical(decision.proposed_spec) == canonical(verify(VERTICAL, PROFILE))
    assert decision.latency_ms is not None


async def test_hybrid_shows_a_valid_proposal() -> None:
    floor = _CountingFloor()
    decision = await policy(ScriptedVisualPlanner(VERTICAL), "hybrid", fallback=floor).decide(
        "q", PROFILE, DEFAULT_PRESENTATION
    )
    assert decision.accepted and decision.source == "llm"
    assert canonical(decision.spec) == canonical(verify(VERTICAL, PROFILE))
    # R2's eager half: `hybrid` always computes the floor, even when it accepts the model's own
    # chart -- the baseline number Section G measures must exist on every accepted turn too.
    assert decision.changed_from_baseline is True
    assert decision.valid is True
    assert canonical(decision.proposed_spec) == canonical(decision.spec)
    assert floor.calls == 1


async def test_hybrid_falls_back_on_a_rejected_proposal_and_names_the_rule() -> None:
    decision = await policy(ScriptedVisualPlanner(SIDEWAYS), "hybrid").decide(
        "q", PROFILE, DEFAULT_PRESENTATION
    )
    assert decision.source == "deterministic" and decision.fallback_reason == "invalid_proposal"
    assert decision.rejected_rule == "structure" and decision.proposed_spec is None


async def test_a_contradictory_abstention_is_invalid_proposal_never_a_clean_one() -> None:
    """U-7 (Wave 3 hardening): `{"mark": null, "limit": 3}` -- a model that both declined to
    propose and proposed a row cut in the same breath -- used to reach here as `None`, the port's
    own "abstention and nothing else" value, and be recorded exactly like a model that said
    nothing at all. `llm_visual_planner._draft` now hands the contradiction to this policy as a
    draft with `mark=None` instead, and `verify()`'s own `_structure` rule (a `ChartSpec` mark
    outside `get_args(Mark)`) refuses it -- the same `invalid_proposal:structure` an ordinary
    malformed proposal (`test_hybrid_falls_back_on_a_rejected_proposal_and_names_the_rule`'s
    `SIDEWAYS`) already gets, not a fifth reason invented for this one shape."""
    contradictory_abstention = D(None, (), limit=3)  # type: ignore[arg-type]
    decision = await policy(ScriptedVisualPlanner(contradictory_abstention), "hybrid").decide(
        "q", PROFILE, DEFAULT_PRESENTATION
    )
    assert decision.source == "deterministic" and decision.fallback_reason == "invalid_proposal"
    assert decision.rejected_rule == "structure" and decision.proposed_spec is None


async def test_an_adapter_that_raises_anything_falls_back() -> None:
    """The adapter that parses a model's answer does not exist yet; whatever it throws while
    doing so is a fact about the experiment, not this turn, and the floor is still shown."""
    decision = await policy(ScriptedVisualPlanner(ValueError("boom")), "hybrid").decide(
        "q", PROFILE, DEFAULT_PRESENTATION
    )
    floor = DeterministicVisualPlanner().plan(PROFILE, DEFAULT_PRESENTATION)
    assert decision.source == "deterministic" and decision.fallback_reason == "provider_error"
    assert decision.proposed is True
    assert canonical(decision.spec) == canonical(floor)
    # U-8 (Wave 3 hardening): still `provider_error` (the closed four stay four), but named as a
    # bug in this adapter, not the provider itself.
    assert decision.error_detail == "code_error:ValueError"


async def test_a_bug_in_the_adapter_is_told_apart_from_a_real_provider_failure(caplog) -> None:
    """U-8: a `TypeError` -- the shape a real bug in the not-yet-written parsing adapter would
    take, unlike `ProviderFailedError`/`TimeoutError`, the port's own two named failures -- still
    falls back to the floor and still records `provider_error` (`FallbackReason` stays the four
    the wire seals), but `error_detail` says which kind of `provider_error` this was, and the
    exception is logged at ERROR with its own type and traceback, not silently swallowed."""
    with caplog.at_level("ERROR"):
        decision = await policy(ScriptedVisualPlanner(TypeError("boom")), "hybrid").decide(
            "q", PROFILE, DEFAULT_PRESENTATION
        )
    assert decision.source == "deterministic" and decision.fallback_reason == "provider_error"
    assert decision.error_detail == "code_error:TypeError"
    assert any(
        record.levelname == "ERROR" and "code_error" in record.getMessage()
        for record in caplog.records
    )


async def test_a_providers_own_timeout_is_told_apart_from_the_budget() -> None:
    """`TimeoutError` raised by the adapter itself (its own socket/connect timeout) must not be
    confused with the policy's own budget expiry: it reached the provider, so `proposed is True`,
    and it is a provider failure, not a `fallback_reason="timeout"` the budget alone earns."""
    decision = await policy(
        ScriptedVisualPlanner(TimeoutError("connect timed out")), "hybrid", budget=5.0
    ).decide("q", PROFILE, DEFAULT_PRESENTATION)
    assert decision.fallback_reason == "provider_error"
    assert decision.proposed is True


@pytest.mark.parametrize("mode", MODES_THAT_ATTEMPT_THE_MODEL)
@pytest.mark.parametrize(
    ("outcome", "reason"),
    [
        (None, "abstention"),
        (ProviderFailedError("test-model", "down"), "provider_error"),
        (0.5, "timeout"),
    ],
)
async def test_the_three_failures_are_told_apart(outcome, reason, mode) -> None:
    """Nine rows: each of the three causes, in each of the three modes that attempt the model at
    all. `shadow` and `promoted` were previously untested here -- only `hybrid` was -- so
    `promoted`'s lazy floor recovery (the only reason that code path exists) had zero coverage.

    A-4 (Wave 3 hardening): `shadow` never shows anything but the floor (`accept` is never `True`
    there), so nothing in this mode ever falls back -- its own `fallback_reason` is `None`
    regardless of `reason`, unlike `hybrid`/`promoted`, which really did just fall back to the
    floor and say so."""
    decision = await policy(ScriptedVisualPlanner(outcome), mode, budget=0.05).decide(
        "q", PROFILE, DEFAULT_PRESENTATION
    )
    floor = DeterministicVisualPlanner().plan(PROFILE, DEFAULT_PRESENTATION)
    expected_reason = None if mode == "shadow" else reason
    assert decision.source == "deterministic" and decision.fallback_reason == expected_reason
    # A timeout never learns whether the provider was reached; the other two did reach it.
    assert decision.proposed is (reason != "timeout")
    assert decision.proposed_spec is None
    assert decision.valid is False
    assert decision.changed_from_baseline is None
    assert canonical(decision.spec) == canonical(floor)


async def test_every_fallback_reason_is_reachable() -> None:
    """One turn per `FallbackReason` value, collected into a set and checked against `get_args` --
    a fifth reason string invented elsewhere, or one of these four silently dropped, shows up as a
    set mismatch instead of nothing at all."""
    outcomes = (None, ProviderFailedError("test-model", "down"), 0.5, SIDEWAYS)
    reasons = set()
    for outcome in outcomes:
        decision = await policy(ScriptedVisualPlanner(outcome), "hybrid", budget=0.05).decide(
            "q", PROFILE, DEFAULT_PRESENTATION
        )
        reasons.add(decision.fallback_reason)
    assert reasons == set(get_args(FallbackReason))


async def test_promoted_pays_for_the_baseline_to_measure_change() -> None:
    """A-5 (Wave 3 hardening): R2's lazy half traded away the one number `promoted` exists to be
    measured by -- reversed here. An accepted proposal now computes the floor too, so
    `changed_from_baseline` is measurable in the mode it exists to justify, at the cost of one
    extra floor call per accepted turn."""
    floor = _CountingFloor()
    decision = await policy(ScriptedVisualPlanner(VERTICAL), "promoted", fallback=floor).decide(
        "q", PROFILE, DEFAULT_PRESENTATION
    )
    assert decision.accepted and decision.changed_from_baseline is True
    assert floor.calls == 1


async def test_promoted_pays_for_the_baseline_when_the_model_fails() -> None:
    """R2's other half: `promoted` computes the floor exactly when it needs one to fall back to,
    never eagerly."""
    floor = _CountingFloor()
    decision = await policy(ScriptedVisualPlanner(None), "promoted", fallback=floor).decide(
        "q", PROFILE, DEFAULT_PRESENTATION
    )
    assert decision.source == "deterministic" and decision.fallback_reason == "abstention"
    assert floor.calls == 1


async def test_promoted_trusts_the_eligibility_guard() -> None:
    """`promoted` never computes the floor unless the model fails (R2), so the one-row decline
    the floor would otherwise offer never runs -- a profile the floor itself refuses still gets
    the model's own chart in this one mode. The eligibility guard is the application's job
    (Section E states it as a precondition of entering the policy at all); this pins the exception
    rather than leaving it silent, per `test_a_single_row_never_reaches_the_policy` below."""
    one = visual_profile_of(
        VisualDataset(metric="SUM", rows=(VisualRow("A", 1),)), total_minor=None, group_by="NONE"
    )
    decision = await policy(ScriptedVisualPlanner(VERTICAL), "promoted").decide(
        "q", one, DEFAULT_PRESENTATION
    )
    assert decision.source == "llm"


async def test_a_single_row_never_reaches_the_policy() -> None:
    """The eligibility guard lives in the application; in `deterministic`, `shadow` and `hybrid` --
    the three modes that compute the floor -- the policy still refuses to invent a chart the floor
    would not draw, and returns None like the floor does. `promoted` does not: see
    `test_promoted_trusts_the_eligibility_guard` above."""
    one = visual_profile_of(
        VisualDataset(metric="SUM", rows=(VisualRow("A", 1),)), total_minor=None, group_by="NONE"
    )
    model = ScriptedVisualPlanner(VERTICAL)
    assert await policy(model, "hybrid").decide("q", one, DEFAULT_PRESENTATION) is None
    assert model.received == []
