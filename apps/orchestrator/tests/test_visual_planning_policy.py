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
from finguard_orchestrator.domain.models.visual_plan import ProposedEncoding as E
from finguard_orchestrator.domain.models.visual_plan import VisualMode, canonical
from finguard_orchestrator.domain.models.visual_plan import VisualPlanDraft as D
from finguard_orchestrator.domain.services.deterministic_visual_planner import (
    DeterministicVisualPlanner,
)
from finguard_orchestrator.domain.services.visual_plan_verifier import verify
from finguard_orchestrator.domain.services.visual_planning_policy import VisualPlanningPolicy
from finguard_orchestrator.domain.services.visual_profile import visual_profile_of

SIDEWAYS = D("sideways", (E("x", "dimension", "nominal"),))


def policy(model, mode, budget=1.0):
    return VisualPlanningPolicy(
        model=model,
        fallback=DeterministicVisualPlanner(),
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
    decision = await policy(ScriptedVisualPlanner(VERTICAL), "hybrid").decide(
        "q", PROFILE, DEFAULT_PRESENTATION
    )
    assert decision.accepted and decision.source == "llm"
    assert canonical(decision.spec) == canonical(verify(VERTICAL, PROFILE))


async def test_hybrid_falls_back_on_a_rejected_proposal_and_names_the_rule() -> None:
    decision = await policy(ScriptedVisualPlanner(SIDEWAYS), "hybrid").decide(
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


async def test_a_providers_own_timeout_is_told_apart_from_the_budget() -> None:
    """`TimeoutError` raised by the adapter itself (its own socket/connect timeout) must not be
    confused with the policy's own budget expiry: it reached the provider, so `proposed is True`,
    and it is a provider failure, not a `fallback_reason="timeout"` the budget alone earns."""
    decision = await policy(
        ScriptedVisualPlanner(TimeoutError("connect timed out")), "hybrid", budget=5.0
    ).decide("q", PROFILE, DEFAULT_PRESENTATION)
    assert decision.fallback_reason == "provider_error"
    assert decision.proposed is True


@pytest.mark.parametrize(
    ("outcome", "reason"),
    [
        (None, "abstention"),
        (ProviderFailedError("test-model", "down"), "provider_error"),
        (0.5, "timeout"),
    ],
)
async def test_the_three_failures_are_told_apart(outcome, reason) -> None:
    decision = await policy(ScriptedVisualPlanner(outcome), "hybrid", budget=0.05).decide(
        "q", PROFILE, DEFAULT_PRESENTATION
    )
    assert decision.source == "deterministic" and decision.fallback_reason == reason
    # A timeout never learns whether the provider was reached; the other two did reach it.
    assert decision.proposed is (reason != "timeout")


async def test_promoted_does_not_pay_for_the_baseline() -> None:
    decision = await policy(ScriptedVisualPlanner(VERTICAL), "promoted").decide(
        "q", PROFILE, DEFAULT_PRESENTATION
    )
    assert decision.accepted and decision.changed_from_baseline is None


async def test_a_single_row_never_reaches_the_policy() -> None:
    """The eligibility guard lives in the application; the policy still refuses to invent a chart
    the floor would not draw, and returns None like the floor does."""
    one = visual_profile_of(
        VisualDataset(metric="SUM", rows=(VisualRow("A", 1),)), total_minor=None, group_by="NONE"
    )
    model = ScriptedVisualPlanner(VERTICAL)
    assert await policy(model, "hybrid").decide("q", one, DEFAULT_PRESENTATION) is None
    assert model.received == []
