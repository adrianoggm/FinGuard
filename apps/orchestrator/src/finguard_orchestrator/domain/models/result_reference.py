"""A result referent bound to the exact query that produced it.

``Conversation`` still owns the backwards-compatible wire fields.  This value is the
read-side projection: consumers cannot accidentally combine a row from one answer with
the query currently in focus.
"""

from dataclasses import dataclass, replace
from uuid import UUID

from finguard_orchestrator.domain.constants import (
    MAX_MERCHANT_LENGTH,
    MAX_SELECTED_TRANSACTIONS,
)
from finguard_orchestrator.domain.enums import GroupBy
from finguard_orchestrator.domain.errors import InvalidValueError
from finguard_orchestrator.domain.models.conversation import Conversation, PlannedQuery
from finguard_orchestrator.domain.models.planning_context import (
    BalanceFocus,
    PlanningFocus,
    QueryFocus,
)
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan

_REFERENCEABLE_GROUPS = {
    GroupBy.CATEGORY,
    GroupBy.MERCHANT,
    GroupBy.COUNTERPARTY,
    GroupBy.ACCOUNT,
}


@dataclass(frozen=True)
class ResultReference:
    """The row and evidence selected from one immutable source query."""

    source_query: PlannedQuery
    category: str | None = None
    group_by: GroupBy | None = None
    group_key: str | None = None
    transaction_ids: tuple[UUID, ...] = ()

    def __post_init__(self) -> None:
        if (self.group_by is None) != (self.group_key is None):
            raise InvalidValueError("a result reference needs both its dimension and key")
        if self.group_by not in {None, *_REFERENCEABLE_GROUPS}:
            raise InvalidValueError("the result reference dimension is not referenceable")
        if self.category is not None:
            if not self.category.strip():
                raise InvalidValueError("a result reference category cannot be blank")
            if len(self.category) > MAX_MERCHANT_LENGTH:
                raise InvalidValueError("result reference category is too long")
        if self.group_key is not None:
            if not self.group_key.strip():
                raise InvalidValueError("a result reference key cannot be blank")
            if len(self.group_key) > MAX_MERCHANT_LENGTH:
                raise InvalidValueError("result reference key is too long")
        if len(set(self.transaction_ids)) != len(self.transaction_ids):
            raise InvalidValueError("result reference transaction ids must be unique")
        if len(self.transaction_ids) > MAX_SELECTED_TRANSACTIONS:
            raise InvalidValueError("too many result reference transaction ids")
        if (
            self.group_by is GroupBy.CATEGORY
            and self.category is not None
            and self.category != self.group_key
        ):
            raise InvalidValueError("the selected category and category row disagree")
        if (
            self.category is not None
            and self.group_by not in {None, GroupBy.CATEGORY}
            and (
                not isinstance(self.source_query, FinancialPlan)
                or self.category not in self.source_query.categories
            )
        ):
            raise InvalidValueError("the selected category is not grounded by its source query")


def answered_with_result(conversation: Conversation, reference: ResultReference) -> Conversation:
    """Remember one answer; the canonical reference is the sole projection."""
    answered = conversation.answered(reference.source_query)
    return replace(answered, result_reference=reference)


def focused_on_result(conversation: Conversation, reference: ResultReference) -> Conversation:
    """Select another visible row without changing the query or its other conversation state."""
    if conversation.last_plan is not reference.source_query:
        raise InvalidValueError("a result can only be focused on its exact source query")
    return replace(conversation, result_reference=reference)


def result_reference_of(
    conversation: Conversation,
    focus: PlanningFocus | None,
    *,
    returned_from_balance: bool = False,
) -> ResultReference | None:
    """Return canonical memory only when ``focus`` authorises its source query.

    Ratios, pending ratios and comparisons deliberately have no projection: their legacy
    ``last_plan`` is merely one operand.  A balance keeps the previous query suspended, but
    its rows become readable only after an explicit return to that query.
    """
    if isinstance(focus, QueryFocus):
        source_query = focus.current
    elif (
        returned_from_balance
        and isinstance(focus, BalanceFocus)
        and focus.suspended_query is not None
    ):
        source_query = focus.suspended_query
    else:
        return None

    if conversation.last_plan is not source_query:
        return None

    if conversation.result_reference is not None:
        return conversation.result_reference

    if isinstance(source_query, FinancialPlan):
        category = source_query.categories[0] if len(source_query.categories) == 1 else None
        if category is not None or source_query.transaction_ids:
            return ResultReference(
                source_query=source_query,
                category=category,
                transaction_ids=source_query.transaction_ids,
            )
    return None
