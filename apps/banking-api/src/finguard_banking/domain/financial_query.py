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
    MAX_CATEGORY_LENGTH,
    MAX_SUPPORTING_MOVEMENTS,
)
from finguard_banking.domain.enums import Direction, GroupBy, Metric
from finguard_banking.domain.errors import InvalidValueError
from finguard_banking.domain.value_objects.date_range import DateRange


@dataclass(frozen=True)
class FinancialQuery:
    account_id: UUID
    date_range: DateRange
    metric: Metric = Metric.SUM
    direction: Direction = Direction.ALL
    categories: tuple[str, ...] = field(default_factory=tuple)
    group_by: GroupBy = GroupBy.NONE
    # How many movements to return as evidence for the figure.
    supporting_limit: int = DEFAULT_SUPPORTING_MOVEMENTS
    # The trace (compiled SQL and parameters) is opt-in: it never travels by default.
    explain: bool = False

    def __post_init__(self) -> None:
        if not 0 <= self.supporting_limit <= MAX_SUPPORTING_MOVEMENTS:
            raise InvalidValueError(
                f"supportingLimit must be between 0 and {MAX_SUPPORTING_MOVEMENTS}"
            )
        if len(self.categories) > MAX_CATEGORIES:
            raise InvalidValueError(f"at most {MAX_CATEGORIES} categories")
        for category in self.categories:
            if not category.isascii() or not category.replace("_", "").isalnum():
                raise InvalidValueError(f"invalid category: {category!r}")
            if len(category) > MAX_CATEGORY_LENGTH:
                raise InvalidValueError(f"category too long: {category!r}")
