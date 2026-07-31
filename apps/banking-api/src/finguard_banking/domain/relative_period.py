"""Resolves relative time expressions into a concrete date range.

The LLM names the period (`LAST_MONTH`), it does not compute it: calendar arithmetic
inside a generative model is exactly where silent off-by-one-month errors come from.
Here it is a pure function, so the result never depends on when it runs.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

from finguard_banking.domain.constants import MAX_PERIOD_DAYS, MAX_PERIOD_MONTHS
from finguard_banking.domain.enums import Period
from finguard_banking.domain.financial_query import DateRange

NEEDS_COUNT = (Period.LAST_N_DAYS, Period.LAST_N_MONTHS)


class RelativePeriod(BaseModel):
    """A relative period. `reference` pins what "today" means; without it, nothing
    is reproducible."""

    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, frozen=True, extra="forbid"
    )

    period: Period
    n: int | None = Field(default=None, ge=1, le=MAX_PERIOD_DAYS)
    reference: date | None = None

    @model_validator(mode="after")
    def _consistent_count(self) -> Self:
        needed = self.period in NEEDS_COUNT
        if needed and self.n is None:
            raise ValueError(f"{self.period} requires n")
        if not needed and self.n is not None:
            raise ValueError(f"{self.period} does not accept n")
        if (
            self.period is Period.LAST_N_MONTHS
            and self.n is not None
            and self.n > MAX_PERIOD_MONTHS
        ):
            raise ValueError(f"at most {MAX_PERIOD_MONTHS} months")
        return self


def _first_of_month(day: date) -> date:
    return day.replace(day=1)


def _months_back(day: date, months: int) -> date:
    """First day of the month `months` before. Avoids inventing a 31st of February."""
    total = day.year * 12 + (day.month - 1) - months
    return date(total // 12, total % 12 + 1, 1)


def resolve(relative: RelativePeriod, today: date) -> DateRange:
    """Turns the period into a closed range, both ends included."""
    reference = relative.reference or today

    match relative.period:
        case Period.TODAY:
            return DateRange(start=reference, end=reference)

        case Period.YESTERDAY:
            yesterday = reference - timedelta(days=1)
            return DateRange(start=yesterday, end=yesterday)

        case Period.THIS_WEEK:
            # The week starts on Monday and ends today: nobody asks about the money
            # they will spend tomorrow.
            return DateRange(start=reference - timedelta(days=reference.weekday()), end=reference)

        case Period.LAST_WEEK:
            monday = reference - timedelta(days=reference.weekday() + 7)
            return DateRange(start=monday, end=monday + timedelta(days=6))

        case Period.THIS_MONTH:
            return DateRange(start=_first_of_month(reference), end=reference)

        case Period.LAST_MONTH:
            return DateRange(
                start=_months_back(reference, 1),
                end=_first_of_month(reference) - timedelta(days=1),
            )

        case Period.THIS_YEAR:
            return DateRange(start=date(reference.year, 1, 1), end=reference)

        case Period.LAST_N_DAYS:
            if relative.n is None:
                raise ValueError("LAST_N_DAYS requires n")
            # Today counts: "the last 7 days" are today and the 6 before it.
            return DateRange(start=reference - timedelta(days=relative.n - 1), end=reference)

        case Period.LAST_N_MONTHS:
            if relative.n is None:
                raise ValueError("LAST_N_MONTHS requires n")
            # The current month counts, from its 1st.
            return DateRange(start=_months_back(reference, relative.n - 1), end=reference)
