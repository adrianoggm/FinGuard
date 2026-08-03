"""The validated query plan.

The intermediate representation of vision.md §9.2: the LLM proposes this, never SQL.
Everything arriving here is untrusted input, so the invariants are deliberately strict.

No pydantic and no aliases: `from` and `to` are the shape of the §13.2 HTTP contract,
not of the business, and §4.4 forbids the domain from depending on protocols. The DTOs
in infrastructure/dto/ own the wire format; a mapper stands between.
"""

from dataclasses import dataclass, field
from uuid import UUID

from finguard_banking.domain.constants import (
    DEFAULT_SUPPORTING_MOVEMENTS,
    MAX_CATEGORIES,
    MAX_SUPPORTING_MOVEMENTS,
)
from finguard_banking.domain.enums import Direction, GroupBy, Metric
from finguard_banking.domain.errors import InvalidValueError
from finguard_banking.domain.value_objects.category import Category
from finguard_banking.domain.value_objects.date_range import DateRange


@dataclass(frozen=True)
class FinancialQuery:
    account_id: UUID
    date_range: DateRange
    metric: Metric = Metric.SUM
    direction: Direction = Direction.ALL
    # Every category validated itself on construction: the plan only has to bound how
    # many of them there are.
    categories: tuple[Category, ...] = field(default_factory=tuple)
    group_by: GroupBy = GroupBy.NONE
    # How many movements to return as evidence for the figure.
    supporting_limit: int = DEFAULT_SUPPORTING_MOVEMENTS
    # The trace (compiled SQL and parameters) is opt-in: it never travels by default.
    explain: bool = False
    # The zone the caller's day was measured in, when the caller said. None means the
    # deployment's own, which is the right fallback and the wrong default to rely on: a
    # date without a zone does not say what "hoy" means, and two services each using their
    # own setting can bin the same purchase on different days.
    reporting_timezone: str | None = None

    def __post_init__(self) -> None:
        if not 0 <= self.supporting_limit <= MAX_SUPPORTING_MOVEMENTS:
            raise InvalidValueError(
                f"supportingLimit must be between 0 and {MAX_SUPPORTING_MOVEMENTS}"
            )
        if len(self.categories) > MAX_CATEGORIES:
            raise InvalidValueError(f"at most {MAX_CATEGORIES} categories")

    def category_values(self) -> list[str]:
        """The plain strings, for the one place that needs them: a SQL parameter."""
        return [category.value for category in self.categories]
