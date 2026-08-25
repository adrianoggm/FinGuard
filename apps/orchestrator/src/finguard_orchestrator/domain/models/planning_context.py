"""Typed, read-only projections of conversational planning memory.

The planner should not have to know which nullable field in ``Conversation`` is
the active referent.  ``focus_of`` centralises that precedence while keeping
the conversation itself as the source of truth.
"""

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from finguard_orchestrator.domain.models.conversation import (
    AccountSelection,
    Conversation,
    PlannedQuery,
    Slot,
)
from finguard_orchestrator.domain.models.turn_analysis import TurnAnalysis, TurnRelation
from finguard_orchestrator.domain.services.intent_router import Intent
from finguard_orchestrator.domain.value_objects.comparison_plan import ComparisonPlan
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan
from finguard_orchestrator.domain.value_objects.ratio_plan import RatioDraft, RatioPlan


@dataclass(frozen=True)
class QueryFocus:
    current: PlannedQuery
    previous: PlannedQuery | None = None


@dataclass(frozen=True)
class RatioFocus:
    value: RatioPlan


@dataclass(frozen=True)
class PendingRatioFocus:
    value: RatioDraft


@dataclass(frozen=True)
class ComparisonFocus:
    value: ComparisonPlan


@dataclass(frozen=True)
class BalanceFocus:
    suspended_query: PlannedQuery | None = None


type PlanningFocus = QueryFocus | RatioFocus | PendingRatioFocus | ComparisonFocus | BalanceFocus


class ContinuationSource(StrEnum):
    LAST_ANSWER = "last_answer"
    ACTIVE_CLARIFICATION = "active_clarification"


@dataclass(frozen=True)
class ContinuationBase:
    plan: FinancialPlan
    source: ContinuationSource
    force_follow_up: bool = False


@dataclass(frozen=True)
class PlanningContext:
    today: date
    reporting_timezone: str
    intent: Intent
    relation: TurnRelation
    focus: PlanningFocus | None
    continuation: ContinuationBase | None
    awaiting: Slot | None
    account_selection: AccountSelection | None


def focus_of(conversation: Conversation) -> PlanningFocus | None:
    """Return the strongest valid conversational referent, deterministically."""
    if conversation.pending_ratio is not None:
        return PendingRatioFocus(conversation.pending_ratio)
    if conversation.last_ratio is not None:
        return RatioFocus(conversation.last_ratio)
    if conversation.last_comparison is not None:
        return ComparisonFocus(conversation.last_comparison)
    if conversation.answered_the_balance:
        return BalanceFocus(conversation.last_plan)
    if conversation.last_plan is not None:
        return QueryFocus(conversation.last_plan, conversation.prior_plan)
    return None


def planning_context(
    conversation: Conversation,
    *,
    today: date,
    reporting_timezone: str,
    analysis: TurnAnalysis,
    continuation_plan: FinancialPlan | None,
    continuation_source: ContinuationSource = ContinuationSource.LAST_ANSWER,
    force_follow_up: bool = False,
) -> PlanningContext:
    """Project only the typed facts planning needs; never expose mutable wire state."""
    focus = focus_of(conversation)
    can_continue = continuation_plan is not None and (
        continuation_source is ContinuationSource.ACTIVE_CLARIFICATION
        or (isinstance(focus, QueryFocus) and focus.current is continuation_plan)
    )
    continuation = (
        ContinuationBase(continuation_plan, continuation_source, force_follow_up)
        if can_continue and continuation_plan is not None
        else None
    )
    return PlanningContext(
        today=today,
        reporting_timezone=reporting_timezone,
        intent=analysis.intent,
        relation=analysis.relation,
        focus=focus,
        continuation=continuation,
        awaiting=conversation.awaiting or conversation.last_asked,
        account_selection=conversation.account_selection,
    )
