from dataclasses import replace

import pytest

from finguard_orchestrator.domain.enums import (
    CounterpartyType,
    Direction,
    GroupBy,
    Metric,
    Period,
    TransactionType,
)
from finguard_orchestrator.domain.errors import InvalidValueError
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan, RelativePeriod
from finguard_orchestrator.domain.value_objects.plan_patch import PatchField, PlanPatch


def test_merchant_exclusions_are_case_insensitive_against_the_base_and_idempotent() -> None:
    plan = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.LAST_MONTH),
        merchants=("Lidl",),
        excluded_merchants=("Mercadona",),
    )
    patch = PlanPatch(excluded_merchants=("mercadona", "AMAZON"))

    applied = patch.apply_to(plan)

    assert applied.excluded_merchants == ("Mercadona", "AMAZON")
    assert patch.apply_to(applied) == applied


def test_temporal_group_patch_clears_a_non_temporal_ranking_limit() -> None:
    plan = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.LAST_MONTH),
        group_by=GroupBy.CATEGORY,
        group_limit=5,
    )

    applied = PlanPatch(group_by=GroupBy.MONTH).apply_to(plan)

    assert applied.group_by is GroupBy.MONTH
    assert applied.group_limit is None


def test_patch_rejects_a_group_incompatible_with_the_existing_metric() -> None:
    plan = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.LAST_MONTH),
        group_by=GroupBy.MONTH,
        metric=Metric.CUMULATIVE,
    )

    with pytest.raises(InvalidValueError):
        PlanPatch(group_by=GroupBy.CATEGORY).apply_to(plan)


def test_a_direction_patch_turns_only_the_direction() -> None:
    """«¿Y ella a mí?»: the person, the window and the kind of movement are the question asked."""
    plan = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.THIS_MONTH),
        counterparties=("Marta",),
        counterparty_types=(CounterpartyType.PERSON,),
        transaction_types=(TransactionType.TRANSFER,),
    )
    patch = PlanPatch(direction=Direction.INCOME)

    assert patch.fields == frozenset({PatchField.DIRECTION})
    assert patch.apply_to(plan) == replace(plan, direction=Direction.INCOME)
