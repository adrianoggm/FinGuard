"""Asking for one movement by size.

The capability the assistant refused until now. Refusing was right — `Metric` is SUM or COUNT, so a
total in place of a maximum is a real number about a different question — and this is what replaces
the refusal rather than making it politer.

A separate value object rather than a wider `FinancialPlan`, for the reason banking's side already
records: a maximum is **one row** with an identity, a date and a shop, while an aggregate is a total
with groups. It shares the filters and nothing else — `group_by` and `metric` are absent because
they mean nothing here, and a field with no meaning is a field something will eventually set.

The rank is what makes the follow-up cheap. "Y el segundo" changes one number and re-derives nothing
about the question, which is what stops it depending on how the continuation happens to be phrased.
"""

from dataclasses import dataclass, field, replace
from typing import Final

from finguard_orchestrator.domain.enums import Direction
from finguard_orchestrator.domain.errors import InvalidValueError
from finguard_orchestrator.domain.value_objects.financial_plan import (
    AbsoluteRange,
    RelativePeriod,
    check_filters,
)

# How far down the ranking anybody may ask. It becomes an OFFSET in banking, which is the difference
# between a question and a scan — banking bounds it too, at the same number, and disagreeing would
# mean a plan this service accepts and that one rejects.
MAX_RANK: Final = 100


@dataclass(frozen=True)
class TopMovements:
    """The nth largest movement matching these filters."""

    direction: Direction
    period: RelativePeriod | None = None
    date_range: AbsoluteRange | None = None
    categories: tuple[str, ...] = field(default_factory=tuple)
    merchants: tuple[str, ...] = field(default_factory=tuple)
    # 1 is the largest. Not zero-based: "el primero" is what a person says, and an off-by-one here
    # would answer "the largest" with the second largest.
    rank: int = 1

    def __post_init__(self) -> None:
        if (self.period is None) == (self.date_range is None):
            raise InvalidValueError("a ranking needs exactly one of period and dateRange")
        if isinstance(self.rank, bool) or not isinstance(self.rank, int):
            raise InvalidValueError("rank must be a whole number")
        if not 1 <= self.rank <= MAX_RANK:
            raise InvalidValueError(f"rank must be between 1 and {MAX_RANK}, got {self.rank}")
        check_filters(self.categories, self.merchants)

    def next_rank(self) -> "TopMovements":
        """The next one down, which is what a follow-up asks for.

        Raises past the ceiling rather than clamping: somebody who has walked a hundred movements
        down a list wants the list, and silently repeating the hundredth would look like an answer.
        """
        return replace(self, rank=self.rank + 1)
