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
from finguard_orchestrator.domain.value_objects.financial_plan import (
    FinancialPlan,
    FinancialPlanDraft,
    RelativePeriod,
)
from finguard_orchestrator.domain.value_objects.plan_patch import (
    PatchField,
    PlanPatch,
    divisible_by,
)


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


def test_a_counterparty_patch_keeps_only_the_person_named() -> None:
    plan = FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.THIS_MONTH),
        counterparties=("Marta", "Javi"),
    )
    patch = PlanPatch(counterparties=("Marta",))

    assert patch.fields == frozenset({PatchField.COUNTERPARTIES})
    assert patch.apply_to(plan) == replace(plan, counterparties=("Marta",))


MONTH = RelativePeriod(Period.THIS_MONTH)


@pytest.mark.parametrize(
    ("plan", "grouping", "dropped"),
    [
        (
            FinancialPlan(direction=Direction.EXPENSE, period=MONTH, categories=("GROCERIES",)),
            GroupBy.CATEGORY,
            {"categories": ()},
        ),
        (
            FinancialPlan(direction=Direction.INCOME, period=MONTH, categories=("SALARY",)),
            GroupBy.CATEGORY,
            {"categories": ()},
        ),
        (
            FinancialPlan(direction=Direction.EXPENSE, period=MONTH, merchants=("Mercadona",)),
            GroupBy.COUNTERPARTY,
            {"merchants": ()},
        ),
        (
            FinancialPlanDraft(direction=Direction.EXPENSE, categories=("GROCERIES",)),
            GroupBy.CATEGORY,
            {"categories": ()},
        ),
    ],
)
def test_a_breakdown_drops_the_one_value_it_would_divide(
    plan: FinancialPlan | FinancialPlanDraft, grouping: GroupBy, dropped: dict[str, tuple[str, ...]]
) -> None:
    """Ruling R29/R36: one category by category, one shop by shop, is one row."""
    assert divisible_by(plan, grouping) == replace(plan, **dropped)


@pytest.mark.parametrize(
    ("plan", "grouping"),
    [
        # Every movement: nothing says which side divides.
        (
            FinancialPlan(direction=Direction.ALL, period=MONTH, categories=("GROCERIES",)),
            GroupBy.CATEGORY,
        ),
        # Something finer than the category.
        (
            FinancialPlan(
                direction=Direction.EXPENSE,
                period=MONTH,
                categories=("GROCERIES",),
                merchants=("Mercadona",),
            ),
            GroupBy.CATEGORY,
        ),
        (
            FinancialPlan(
                direction=Direction.EXPENSE,
                period=MONTH,
                categories=("GROCERIES",),
                merchant_terms=("mercadona",),
            ),
            GroupBy.CATEGORY,
        ),
        (
            FinancialPlan(
                direction=Direction.EXPENSE,
                period=MONTH,
                categories=("GROCERIES",),
                counterparties=("Marta",),
            ),
            GroupBy.CATEGORY,
        ),
        # Never a person, never a fragment of a shop's name.
        (
            FinancialPlan(direction=Direction.EXPENSE, period=MONTH, counterparties=("Marta",)),
            GroupBy.COUNTERPARTY,
        ),
        (
            FinancialPlan(direction=Direction.EXPENSE, period=MONTH, merchant_terms=("mercadona",)),
            GroupBy.COUNTERPARTY,
        ),
        # Two values already divide; a calendar breakdown divides by itself.
        (
            FinancialPlan(
                direction=Direction.EXPENSE, period=MONTH, categories=("GROCERIES", "RESTAURANTS")
            ),
            GroupBy.CATEGORY,
        ),
        (
            FinancialPlan(direction=Direction.EXPENSE, period=MONTH, categories=("GROCERIES",)),
            GroupBy.MONTH,
        ),
    ],
)
def test_a_breakdown_keeps_every_filter_otherwise(plan: FinancialPlan, grouping: GroupBy) -> None:
    assert divisible_by(plan, grouping) == plan


def test_the_regroup_patch_drops_the_one_value_and_keeps_the_rest() -> None:
    plan = FinancialPlan(
        direction=Direction.EXPENSE,
        period=MONTH,
        categories=("GROCERIES",),
        excluded_merchants=("Lidl",),
        group_by=GroupBy.COUNTERPARTY,
        group_limit=5,
    )

    regrouped = PlanPatch(group_by=GroupBy.CATEGORY).apply_to(plan)

    assert regrouped == replace(plan, categories=(), group_by=GroupBy.CATEGORY)
