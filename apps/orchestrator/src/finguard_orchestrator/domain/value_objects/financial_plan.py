"""The plan the assistant produces and sends to banking.

Validating here is not distrust of banking — banking is the authority and validates
again. It is that a plan rejected locally is an INVALID_DOMAIN outcome we can count and
show in the evaluation, whereas the same plan sent blind comes back as a 422 that has
already cost a round trip and reads like a bug in the caller.

The invariants mirror the contract deliberately and no further: no calendar arithmetic
lives here. What `LAST_MONTH` covers is banking's business, and two implementations of
that would be one too many.
"""

from dataclasses import dataclass, field
from datetime import date

from finguard_orchestrator.domain.constants import (
    MAX_CATEGORIES,
    MAX_CATEGORY_LENGTH,
    MAX_MERCHANT_LENGTH,
    MAX_MERCHANTS,
    MAX_PERIOD_DAYS,
    MAX_PERIOD_MONTHS,
)
from finguard_orchestrator.domain.enums import (
    PERIODS_NEEDING_COUNT,
    Direction,
    GroupBy,
    Metric,
    Period,
)
from finguard_orchestrator.domain.errors import InvalidValueError


@dataclass(frozen=True)
class RelativePeriod:
    """A named window. `n` belongs to the counted periods and to no other."""

    period: Period
    n: int | None = None

    def __post_init__(self) -> None:
        needs_count = self.period in PERIODS_NEEDING_COUNT
        if needs_count and self.n is None:
            raise InvalidValueError(f"{self.period} requires n")
        if not needs_count and self.n is not None:
            raise InvalidValueError(f"{self.period} does not accept n")
        if self.n is None:
            return
        if self.n < 1:
            raise InvalidValueError("n must be at least 1")
        ceiling = MAX_PERIOD_MONTHS if self.period is Period.LAST_N_MONTHS else MAX_PERIOD_DAYS
        if self.n > ceiling:
            raise InvalidValueError(f"{self.period} allows at most {ceiling}")


@dataclass(frozen=True)
class AbsoluteRange:
    """Both ends included. For what a period cannot name — "during January"."""

    start: date
    end: date

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise InvalidValueError(f"range ends before it starts: {self.start}..{self.end}")


@dataclass(frozen=True)
class FinancialPlan:
    """What the sentence turned into.

    Exactly one of `period` and `date_range`: a plan carrying both is not
    over-specified, it is two different questions, and which one wins would depend on
    the reader.
    """

    direction: Direction
    period: RelativePeriod | None = None
    date_range: AbsoluteRange | None = None
    categories: tuple[str, ...] = field(default_factory=tuple)
    # The shops the question is about. Free text, unlike a category — the first field here
    # whose values the schema cannot enumerate, because the ledger's merchant names are data.
    merchants: tuple[str, ...] = field(default_factory=tuple)
    group_by: GroupBy = GroupBy.NONE
    metric: Metric = Metric.SUM
    explain: bool = False

    def __post_init__(self) -> None:
        if (self.period is None) == (self.date_range is None):
            raise InvalidValueError("a plan needs exactly one of period and dateRange")
        if len(self.categories) > MAX_CATEGORIES:
            raise InvalidValueError(f"at most {MAX_CATEGORIES} categories")
        for category in self.categories:
            _check_category(category)
        if len(self.merchants) > MAX_MERCHANTS:
            raise InvalidValueError(f"at most {MAX_MERCHANTS} merchants")
        for merchant in self.merchants:
            # Shape only: a name is free text and its content is banking's to match. What is
            # checked is that it is a name at all, and bounded — an empty one filters nothing
            # and would silently widen the query, which is the failure this file exists for.
            if not merchant.strip():
                raise InvalidValueError("a merchant name cannot be empty")
            if len(merchant) > MAX_MERCHANT_LENGTH:
                raise InvalidValueError(f"merchant name too long: {merchant[:20]!r}...")


def _check_category(value: str) -> None:
    """A category arrives from a language model, so its shape is checked rather than
    assumed — even though the compiler on the other side passes it as a parameter and
    never as concatenated SQL. Two locks on the same door."""
    if not value:
        raise InvalidValueError("a category cannot be empty")
    if len(value) > MAX_CATEGORY_LENGTH:
        raise InvalidValueError(f"category too long: {value!r}")
    if not value.isascii() or not value.replace("_", "").isalnum():
        raise InvalidValueError(f"invalid category: {value!r}")
