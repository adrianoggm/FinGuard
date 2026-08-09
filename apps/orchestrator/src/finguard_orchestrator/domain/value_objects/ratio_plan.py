"""Two validated financial reads whose relationship is the answer.

A percentage is not another setting of `FinancialPlan`: banking can aggregate each side, but the
division belongs to the orchestrator and must never be delegated to a model. Keeping the pair as a
value also gives a follow-up something exact to patch — "¿y el mes pasado?" changes both windows,
not whichever of the two plans happened to be remembered last.
"""

from dataclasses import dataclass
from enum import StrEnum

from finguard_orchestrator.domain.enums import GroupBy, Metric
from finguard_orchestrator.domain.errors import InvalidValueError
from finguard_orchestrator.domain.value_objects.financial_plan import (
    AbsoluteRange,
    FinancialPlan,
    FinancialPlanDraft,
    RelativePeriod,
)

type Window = RelativePeriod | AbsoluteRange


class RatioKind(StrEnum):
    SHARE = "SHARE"
    SAVINGS_RATE = "SAVINGS_RATE"
    PERCENT_CHANGE = "PERCENT_CHANGE"


@dataclass(frozen=True)
class RatioPlan:
    """The two exact ledger queries and the arithmetic that relates them."""

    part: FinancialPlan
    whole: FinancialPlan
    kind: RatioKind

    def __post_init__(self) -> None:
        if self.kind is RatioKind.PERCENT_CHANGE:
            if self.part.metric is not self.whole.metric:
                raise InvalidValueError("percentage change operands must use the same metric")
            if (self.part.period, self.part.date_range) == (
                self.whole.period,
                self.whole.date_range,
            ):
                raise InvalidValueError("percentage change operands need different windows")
            comparable = (
                "direction",
                "categories",
                "excluded_categories",
                "merchants",
                "excluded_merchants",
                "group_by",
                "include_internal",
                "internal_only",
                "across_accounts",
                "only_account_id",
                "only_account_ids",
                "transaction_ids",
            )
            if any(getattr(self.part, field) != getattr(self.whole, field) for field in comparable):
                raise InvalidValueError("percentage change operands must describe the same figure")
            return
        for plan in (self.part, self.whole):
            if plan.metric is not Metric.SUM or plan.group_by is not GroupBy.NONE:
                raise InvalidValueError("ratio operands must be ungrouped sums")
        if (self.part.period, self.part.date_range) != (
            self.whole.period,
            self.whole.date_range,
        ):
            raise InvalidValueError("ratio operands must use the same window")

    def with_window(self, window: Window) -> "RatioPlan":
        return RatioPlan(
            part=_with_window(self.part, window),
            whole=_with_window(self.whole, window),
            kind=self.kind,
        )


@dataclass(frozen=True)
class RatioDraft:
    """A ratio whose only missing fact is the shared reporting window."""

    part: FinancialPlanDraft
    whole: FinancialPlanDraft
    kind: RatioKind

    def __post_init__(self) -> None:
        if self.kind is RatioKind.PERCENT_CHANGE:
            raise InvalidValueError("a percentage change needs two explicit windows")
        for plan in (self.part, self.whole):
            if plan.metric is not Metric.SUM or plan.group_by is not GroupBy.NONE:
                raise InvalidValueError("ratio operands must be ungrouped sums")
            if not plan.needs_a_window:
                raise InvalidValueError("a pending ratio must be waiting for its window")

    def with_window(self, window: Window) -> RatioPlan:
        return RatioPlan(
            part=self.part.with_window(window),
            whole=self.whole.with_window(window),
            kind=self.kind,
        )


def _with_window(plan: FinancialPlan, window: Window) -> FinancialPlan:
    return FinancialPlan(
        direction=plan.direction,
        period=window if isinstance(window, RelativePeriod) else None,
        date_range=window if isinstance(window, AbsoluteRange) else None,
        categories=plan.categories,
        excluded_categories=plan.excluded_categories,
        merchants=plan.merchants,
        excluded_merchants=plan.excluded_merchants,
        group_by=plan.group_by,
        metric=plan.metric,
        group_limit=plan.group_limit,
        include_internal=plan.include_internal,
        internal_only=plan.internal_only,
        across_accounts=plan.across_accounts,
        only_account_id=plan.only_account_id,
        only_account_ids=plan.only_account_ids,
        transaction_ids=plan.transaction_ids,
        transaction_types=plan.transaction_types,
    )
