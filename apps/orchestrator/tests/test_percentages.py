"""Percentages are relationships between two verified ledger reads, never model arithmetic."""

from finguard_orchestrator.domain.enums import CounterpartyType, Direction, Period
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan, RelativePeriod
from finguard_orchestrator.domain.value_objects.ratio_plan import RatioKind, RatioPlan


def test_changing_a_ratio_window_preserves_counterparty_filters() -> None:
    window = RelativePeriod(Period.THIS_YEAR)
    part = FinancialPlan(
        direction=Direction.EXPENSE,
        period=window,
        counterparties=("Iberdrola",),
        excluded_counterparties=("Endesa",),
        counterparty_types=(CounterpartyType.BILLER,),
    )
    ratio = RatioPlan(
        part=part,
        whole=FinancialPlan(direction=Direction.EXPENSE, period=window),
        kind=RatioKind.SHARE,
    )

    changed = ratio.with_window(RelativePeriod(Period.LAST_MONTH))

    assert changed.part.counterparties == part.counterparties
    assert changed.part.excluded_counterparties == part.excluded_counterparties
    assert changed.part.counterparty_types == part.counterparty_types
