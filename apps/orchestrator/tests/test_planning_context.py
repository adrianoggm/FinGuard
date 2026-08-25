from datetime import date
from typing import cast

import pytest

from finguard_orchestrator.domain.enums import Direction, Period
from finguard_orchestrator.domain.errors import InvalidValueError
from finguard_orchestrator.domain.models.conversation import Conversation, PlannedQuery
from finguard_orchestrator.domain.models.planning_context import (
    BalanceFocus,
    ComparisonFocus,
    ContinuationBase,
    ContinuationSource,
    PendingRatioFocus,
    QueryFocus,
    RatioFocus,
    focus_of,
    planning_context,
)
from finguard_orchestrator.domain.models.turn_analysis import (
    TurnAnalysis,
    TurnFocus,
    TurnRelation,
)
from finguard_orchestrator.domain.services.intent_router import Intent
from finguard_orchestrator.domain.value_objects.comparison_plan import ComparisonPlan
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan, RelativePeriod
from finguard_orchestrator.domain.value_objects.ratio_plan import RatioDraft, RatioPlan


def test_focus_precedence_prefers_pending_ratio_then_ratio_then_comparison() -> None:
    pending = cast(RatioDraft, object())
    ratio = cast(RatioPlan, object())
    comparison = cast(ComparisonPlan, object())
    conversation = Conversation(
        pending_ratio=pending,
        last_ratio=ratio,
        last_comparison=comparison,
        answered_the_balance=True,
    )

    assert isinstance(focus_of(conversation), PendingRatioFocus)

    assert isinstance(focus_of(Conversation(last_ratio=ratio)), RatioFocus)
    assert isinstance(focus_of(Conversation(last_comparison=comparison)), ComparisonFocus)


def test_focus_prefers_balance_over_query_memory() -> None:
    query = cast(PlannedQuery, object())
    conversation = Conversation(answered_the_balance=True, last_plan=query)

    focus = focus_of(conversation)
    assert isinstance(focus, BalanceFocus)
    assert focus.suspended_query is query


def test_focus_is_absent_without_conversational_memory() -> None:
    assert focus_of(Conversation()) is None


def test_context_exposes_query_memory_when_no_special_focus_exists() -> None:
    latest = cast(PlannedQuery, object())
    prior = cast(PlannedQuery, object())
    conversation = Conversation(last_plan=latest, prior_plan=prior)

    plan = cast(FinancialPlan, latest)
    context = planning_context(
        conversation,
        today=date(2026, 8, 25),
        reporting_timezone="Atlantic/Canary",
        analysis=TurnAnalysis(Intent.CORRECTION, TurnRelation.CORRECTION, TurnFocus.LAST_ANSWER),
        continuation=ContinuationBase(
            plan,
            ContinuationSource.LAST_ANSWER,
            force_follow_up=True,
        ),
    )

    assert context.today == date(2026, 8, 25)
    assert context.reporting_timezone == "Atlantic/Canary"
    assert context.intent is Intent.CORRECTION
    assert context.relation is TurnRelation.CORRECTION
    assert context.continuation is not None
    assert context.continuation.source is ContinuationSource.LAST_ANSWER
    assert context.continuation.plan is plan
    assert context.continuation.force_follow_up
    assert isinstance(context.focus, QueryFocus)
    assert context.focus.current is latest
    assert context.focus.previous is prior


@pytest.mark.parametrize("special", ["balance", "ratio", "pending", "comparison"])
def test_last_answer_base_is_hidden_behind_a_stronger_focus(special: str) -> None:
    plan = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.LAST_MONTH),
    )
    fields: dict[str, object] = {"last_plan": plan}
    if special == "balance":
        fields["answered_the_balance"] = True
    elif special == "ratio":
        fields["last_ratio"] = cast(RatioPlan, object())
    elif special == "pending":
        fields["pending_ratio"] = cast(RatioDraft, object())
    else:
        fields["last_comparison"] = cast(ComparisonPlan, object())
    conversation = Conversation(**fields)  # type: ignore[arg-type]

    context = planning_context(
        conversation,
        today=date(2026, 8, 25),
        reporting_timezone="Europe/Madrid",
        analysis=TurnAnalysis(
            Intent.FINANCIAL_QUERY, TurnRelation.FOLLOW_UP, TurnFocus.LAST_ANSWER
        ),
        continuation=ContinuationBase(plan, ContinuationSource.LAST_ANSWER),
    )

    assert context.continuation is None


def test_last_answer_continuation_requires_the_exact_focused_plan() -> None:
    focused = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.LAST_MONTH),
    )
    equal_copy = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.LAST_MONTH),
    )

    context = planning_context(
        Conversation(last_plan=focused),
        today=date(2026, 8, 25),
        reporting_timezone="Europe/Madrid",
        analysis=TurnAnalysis(
            Intent.FINANCIAL_QUERY, TurnRelation.FOLLOW_UP, TurnFocus.LAST_ANSWER
        ),
        continuation=ContinuationBase(equal_copy, ContinuationSource.LAST_ANSWER),
    )

    assert equal_copy == focused and equal_copy is not focused
    assert context.continuation is None


def test_active_clarification_must_be_a_forced_follow_up() -> None:
    plan = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.LAST_MONTH),
    )

    with pytest.raises(InvalidValueError, match="must force"):
        ContinuationBase(plan, ContinuationSource.ACTIVE_CLARIFICATION)


def test_active_clarification_is_not_hidden_by_a_special_focus() -> None:
    plan = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.LAST_MONTH),
    )
    continuation = ContinuationBase(
        plan,
        ContinuationSource.ACTIVE_CLARIFICATION,
        force_follow_up=True,
    )

    context = planning_context(
        Conversation(answered_the_balance=True, last_plan=plan),
        today=date(2026, 8, 25),
        reporting_timezone="Europe/Madrid",
        analysis=TurnAnalysis(
            Intent.CORRECTION, TurnRelation.CORRECTION, TurnFocus.ACTIVE_WORKFLOW
        ),
        continuation=continuation,
    )

    assert context.continuation is continuation
