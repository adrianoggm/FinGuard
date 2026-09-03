from datetime import date
from uuid import UUID

import pytest

from finguard_orchestrator.domain.enums import Direction, GroupBy, Metric, Period
from finguard_orchestrator.domain.errors import InvalidValueError
from finguard_orchestrator.domain.services.continuation_patch import (
    GroundedContinuation,
    PatchStatus,
    apply,
    read,
)
from finguard_orchestrator.domain.value_objects.financial_plan import (
    AbsoluteRange,
    FinancialPlan,
    RelativePeriod,
)
from finguard_orchestrator.domain.value_objects.plan_patch import PatchField

TODAY = date(2026, 8, 25)


def plan() -> FinancialPlan:
    return FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(Period.LAST_MONTH),
        categories=("FOOD",),
        only_account_id=UUID("11111111-1111-1111-1111-111111111111"),
        counterparties=("marta",),
        transaction_ids=(UUID("22222222-2222-2222-2222-222222222222"),),
        internal_only=True,
        include_internal=True,
    )


@pytest.mark.parametrize(
    ("sentence", "status"),
    [
        ("Sin el alquiler", PatchStatus.APPLIED),
        ("Excepto Mercadona", PatchStatus.APPLIED),
        ("No, durante todo 2025", PatchStatus.APPLIED),
        ("No, durante 2025 en Amazon", PatchStatus.AMBIGUOUS),
        ("No, durante 2025 Amazon", PatchStatus.AMBIGUOUS),
        ("Y en 2025 Amazon", PatchStatus.AMBIGUOUS),
        ("No, durante 2025 Marta", PatchStatus.AMBIGUOUS),
        ("No, el mes pasado Amazon", PatchStatus.AMBIGUOUS),
        ("Y este mes Mercadona", PatchStatus.AMBIGUOUS),
        ("No, en abril Amazon", PatchStatus.AMBIGUOUS),
        ("No, durante abril Marta", PatchStatus.AMBIGUOUS),
        ("No, durante 2025 por Amazon", PatchStatus.AMBIGUOUS),
        ("No, durante 2025 completo Amazon", PatchStatus.AMBIGUOUS),
        ("No, en 2025", PatchStatus.APPLIED),
        ("No, el mes pasado", PatchStatus.APPLIED),
        ("Y este mes", PatchStatus.APPLIED),
        ("No, en abril", PatchStatus.APPLIED),
        ("No, durante abril", PatchStatus.APPLIED),
        ("No, durante 2025 completo", PatchStatus.APPLIED),
        ("No, me refería a este año", PatchStatus.APPLIED),
        ("No, durante 2025 en ocio", PatchStatus.AMBIGUOUS),
        ("No, durante 2025 en Hucha", PatchStatus.AMBIGUOUS),
        ("No, durante 2025 ingresos", PatchStatus.AMBIGUOUS),
        ("No, durante 2025 en la cuenta Hucha", PatchStatus.AMBIGUOUS),
        ("Y por meses", PatchStatus.APPLIED),
        ("¿Cuánto gasté en 2025?", PatchStatus.NOT_APPLICABLE),
        ("Y eso, ¿cuánto?", PatchStatus.AMBIGUOUS),
    ],
)
def test_table_driven(sentence: str, status: PatchStatus) -> None:
    result = read(sentence, today=TODAY)
    assert result.status is status


def test_canonical_reader_does_not_need_a_base_plan() -> None:
    result = read("Sin el alquiler", today=TODAY)

    assert result.status is PatchStatus.APPLIED
    assert result.patch is not None
    assert result.changed == frozenset({PatchField.EXCLUDED_CATEGORIES})
    assert result.patch.apply_to(plan()).excluded_categories == ("HOUSING_RENT",)


def test_status_and_patch_must_agree() -> None:
    with pytest.raises(InvalidValueError):
        GroundedContinuation(PatchStatus.APPLIED)
    with pytest.raises(InvalidValueError):
        GroundedContinuation(PatchStatus.AMBIGUOUS, read("Sin alquiler").patch)


def test_patch_preserves_sensitive_scope_and_is_idempotent() -> None:
    previous = plan()
    result = apply("Y por meses", previous, today=TODAY)
    assert result.plan is not None
    assert result.plan.group_by is GroupBy.MONTH
    assert result.plan.only_account_id == previous.only_account_id
    assert result.plan.counterparties == previous.counterparties
    assert result.plan.transaction_ids == previous.transaction_ids
    assert result.plan.internal_only == previous.internal_only
    again = apply("Y por meses", result.plan, today=TODAY)
    assert again.plan == result.plan


def test_exclude_category_keeps_window_and_scope() -> None:
    result = apply("Sin el alquiler", plan(), today=TODAY)
    assert result.status is PatchStatus.APPLIED
    assert result.plan is not None
    assert result.plan.period == RelativePeriod(Period.LAST_MONTH)
    assert result.plan.group_by is GroupBy.NONE
    assert result.plan.excluded_categories == ("HOUSING_RENT",)
    assert result.plan.categories == ("FOOD",)
    assert result.changed == frozenset({PatchField.EXCLUDED_CATEGORIES})


def test_exclude_merchant_keeps_window_and_scope() -> None:
    result = apply("Excepto Mercadona", plan(), today=TODAY)
    assert result.status is PatchStatus.APPLIED
    assert result.plan is not None
    assert result.plan.period == RelativePeriod(Period.LAST_MONTH)
    assert result.plan.group_by is GroupBy.NONE
    assert result.plan.excluded_merchants == ("mercadona",)
    assert result.plan.categories == ("FOOD",)


def test_year_correction_is_absolute_and_retains_every_filter() -> None:
    previous = plan()
    result = apply("No, durante todo 2025", previous, today=TODAY)
    assert result.status is PatchStatus.APPLIED
    assert result.plan is not None
    assert result.plan.date_range == AbsoluteRange(date(2025, 1, 1), date(2025, 12, 31))
    assert result.plan.period is None
    assert result.plan.categories == previous.categories
    assert result.plan.counterparties == previous.counterparties
    assert result.plan.only_account_id == previous.only_account_id
    assert result.plan.transaction_ids == previous.transaction_ids
    assert result.plan.internal_only is previous.internal_only
    assert result.plan.include_internal is previous.include_internal
    assert result.changed == frozenset({PatchField.WINDOW})


def test_metric_continuation_changes_only_metric() -> None:
    previous = plan()
    result = apply("Y cuántos movimientos", previous, today=TODAY)
    assert result.status is PatchStatus.APPLIED
    assert result.plan is not None
    assert result.plan.metric is Metric.COUNT
    assert result.plan.period == previous.period
    assert result.plan.group_by == previous.group_by
    assert result.plan.categories == previous.categories
    assert result.plan.counterparties == previous.counterparties
