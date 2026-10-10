"""Echoed plan payloads, rebuilt as domain plans.

A leaf: both the HTTP mapper and the conversation mapper read plans back, so this module imports
no other mapper and neither of them has to import the other.
"""

from finguard_orchestrator.domain.value_objects.financial_plan import (
    AbsoluteRange,
    FinancialPlan,
    FinancialPlanDraft,
    RelativePeriod,
)
from finguard_orchestrator.infrastructure.dto.llm_dto import PlanPayload


def plan_to_domain(payload: PlanPayload) -> FinancialPlan:
    """The echoed plan, rebuilt as a plan.

    Pydantic has already refused anything that is not one of the vocabulary's values;
    the domain now refuses the combinations that are individually valid and jointly
    wrong — both ends of a range, neither window, `n` on a period that has no count.
    An InvalidValueError from here is a 422, which is the honest answer: what came back
    is not something this service ever handed out.
    """
    absolute = payload.start is not None and payload.end is not None
    return FinancialPlan(
        direction=payload.direction,
        period=RelativePeriod(period=payload.period, n=payload.n) if payload.period else None,
        date_range=AbsoluteRange(start=payload.start, end=payload.end) if absolute else None,  # type: ignore[arg-type]
        categories=tuple(payload.categories),
        excluded_categories=tuple(payload.excluded_categories),
        merchants=tuple(payload.merchants),
        merchant_terms=tuple(payload.merchant_terms),
        excluded_merchants=tuple(payload.excluded_merchants),
        counterparties=tuple(payload.counterparties),
        excluded_counterparties=tuple(payload.excluded_counterparties),
        counterparty_types=tuple(payload.counterparty_types),
        group_by=payload.group_by,
        metric=payload.metric,
        group_limit=payload.group_limit,
        include_internal=payload.include_internal,
        internal_only=payload.internal_only,
        across_accounts=payload.across_accounts,
        only_account_id=payload.only_account_id,
        only_account_ids=tuple(payload.only_account_ids),
        transaction_ids=tuple(payload.transaction_ids),
        transaction_types=tuple(payload.transaction_types),
    )


def draft_to_domain(payload: PlanPayload) -> FinancialPlanDraft:
    """The echoed draft, rebuilt as a draft.

    The same payload as a plan and a different type on the way out: a draft is allowed to
    carry no window, which is the one invariant a plan will not relax. Everything else is
    checked identically — Pydantic has already refused values outside the vocabulary, and
    two windows at once is as wrong here as it is there.
    """
    absolute = payload.start is not None and payload.end is not None
    return FinancialPlanDraft(
        direction=payload.direction,
        period=RelativePeriod(period=payload.period, n=payload.n) if payload.period else None,
        date_range=AbsoluteRange(start=payload.start, end=payload.end) if absolute else None,  # type: ignore[arg-type]
        categories=tuple(payload.categories),
        excluded_categories=tuple(payload.excluded_categories),
        merchants=tuple(payload.merchants),
        merchant_terms=tuple(payload.merchant_terms),
        excluded_merchants=tuple(payload.excluded_merchants),
        counterparties=tuple(payload.counterparties),
        excluded_counterparties=tuple(payload.excluded_counterparties),
        counterparty_types=tuple(payload.counterparty_types),
        group_by=payload.group_by,
        metric=payload.metric,
        group_limit=payload.group_limit,
        include_internal=payload.include_internal,
        internal_only=payload.internal_only,
        across_accounts=payload.across_accounts,
        only_account_id=payload.only_account_id,
        only_account_ids=tuple(payload.only_account_ids),
        transaction_ids=tuple(payload.transaction_ids),
        transaction_types=tuple(payload.transaction_types),
    )
