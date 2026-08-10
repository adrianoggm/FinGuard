"""A typed comparison of two compatible scalar financial queries."""

from dataclasses import dataclass, replace

from finguard_orchestrator.domain.constants import MAX_PERIOD_DAYS
from finguard_orchestrator.domain.enums import GroupBy, Metric
from finguard_orchestrator.domain.errors import InvalidValueError
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan

COMPARABLE_METRICS = frozenset({Metric.SUM, Metric.COUNT, Metric.AVERAGE, Metric.PERIOD_AVERAGE})

_SAME_SCOPE = (
    "direction",
    "categories",
    "excluded_categories",
    "merchants",
    "merchant_terms",
    "excluded_merchants",
    "counterparties",
    "excluded_counterparties",
    "counterparty_types",
    "group_by",
    "metric",
    "include_internal",
    "internal_only",
    "across_accounts",
    "only_account_id",
    "only_account_ids",
    "transaction_ids",
    "transaction_types",
    "group_limit",
)


@dataclass(frozen=True)
class ComparisonPlan:
    """Two windows measuring exactly the same thing.

    The order is semantic: ``earlier`` is the reference and ``latest`` is the side whose signed
    change is exposed to clients. Construction refuses mixed metrics or scopes, so execution and
    percentage follow-ups no longer need to rediscover whether the pair is safe.
    """

    earlier: FinancialPlan
    latest: FinancialPlan

    def __post_init__(self) -> None:
        if self.earlier.metric not in COMPARABLE_METRICS:
            raise InvalidValueError(f"metric {self.earlier.metric} is not scalar-comparable")
        if self.earlier.transaction_ids or self.latest.transaction_ids:
            raise InvalidValueError("movement selections are not period comparisons")
        for operand in (self.earlier, self.latest):
            if (
                operand.date_range is not None
                and (operand.date_range.end - operand.date_range.start).days + 1 > MAX_PERIOD_DAYS
            ):
                raise InvalidValueError(f"comparison ranges allow at most {MAX_PERIOD_DAYS} days")
        if (self.earlier.period, self.earlier.date_range) == (
            self.latest.period,
            self.latest.date_range,
        ):
            raise InvalidValueError("a comparison needs two different windows")
        different = tuple(
            field_name
            for field_name in _SAME_SCOPE
            if getattr(self.earlier, field_name) != getattr(self.latest, field_name)
        )
        if different:
            raise InvalidValueError(
                "comparison operands differ outside their window: " + ", ".join(different)
            )

    @property
    def metric(self) -> Metric:
        return self.latest.metric

    @classmethod
    def between(cls, earlier: FinancialPlan, latest: FinancialPlan) -> "ComparisonPlan | None":
        """Normalise presentation-only grouping, then validate one comparable scalar."""
        normalised_earlier = _scalar_plan(earlier)
        normalised_latest = _scalar_plan(latest)
        if normalised_earlier is None or normalised_latest is None:
            return None
        try:
            return cls(earlier=normalised_earlier, latest=normalised_latest)
        except InvalidValueError:
            return None


def _scalar_plan(plan: FinancialPlan) -> FinancialPlan | None:
    if plan.metric not in COMPARABLE_METRICS:
        return None
    if plan.metric is Metric.PERIOD_AVERAGE:
        if plan.group_by not in {GroupBy.DAY, GroupBy.WEEK, GroupBy.MONTH}:
            return None
        return plan
    if plan.group_by is GroupBy.ACCOUNT:
        # Account rows are the comparison itself, rather than presentation around one scalar.
        return plan
    return replace(plan, group_by=GroupBy.NONE)
