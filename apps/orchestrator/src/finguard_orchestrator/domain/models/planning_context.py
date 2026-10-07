"""Typed, read-only projections of conversational planning memory.

The planner should not have to know which nullable field in ``Conversation`` is
the active referent.  ``focus_of`` centralises that precedence while keeping
the conversation itself as the source of truth.
"""

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from finguard_orchestrator.domain.errors import InvalidValueError
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

    def __post_init__(self) -> None:
        if self.source is ContinuationSource.ACTIVE_CLARIFICATION and not self.force_follow_up:
            raise InvalidValueError("an active clarification must force the follow-up")


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
    continuation: ContinuationBase | None,
) -> PlanningContext:
    """Project only the typed facts planning needs; never expose mutable wire state."""
    focus = focus_of(conversation)
    # A comparison is a base too, and deliberately not one the planner continues: it is handed one
    # plan, and either operand alone would answer half the comparison with a real figure on it.
    # «¿Y en gasolina?» after one is patched on both sides before the planner is reached
    # (`AskFinancialQuestionHandler._steps`, `_the_comparison_continued`); a turn that patch cannot
    # read reaches the planner with no base, as a question of its own.
    can_continue = continuation is not None and (
        continuation.source is ContinuationSource.ACTIVE_CLARIFICATION
        or (isinstance(focus, QueryFocus) and focus.current is continuation.plan)
    )
    return PlanningContext(
        today=today,
        reporting_timezone=reporting_timezone,
        intent=analysis.intent,
        relation=analysis.relation,
        focus=focus,
        continuation=continuation if can_continue else None,
        awaiting=conversation.awaiting or conversation.last_asked,
        account_selection=conversation.account_selection,
    )
