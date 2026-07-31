"""Resolves relative time expressions into a concrete date range.

The LLM names the period (`LAST_MONTH`), it does not compute it: calendar arithmetic
inside a generative model is exactly where silent off-by-one-month errors come from.
`resolve` is a pure function — "today" comes in as an argument — so the result never
depends on when it runs.
"""

from dataclasses import dataclass
from datetime import date, timedelta

from finguard_banking.domain.constants import MAX_PERIOD_DAYS, MAX_PERIOD_MONTHS
from finguard_banking.domain.enums import Period
from finguard_banking.domain.errors import InvalidValueError
from finguard_banking.domain.value_objects.date_range import DateRange

NEEDS_COUNT = (Period.LAST_N_DAYS, Period.LAST_N_MONTHS)


@dataclass(frozen=True)
class RelativePeriod:
    """`reference` pins what "today" means; without it, nothing is reproducible.

    The ceilings here are the domain's own protection. The limit a deployment actually
    allows is configurable and applied at the edge (see bootstrap/config.py): a hard
    ceiling that cannot be raised by configuration, plus a policy that can.
    """

    period: Period
    n: int | None = None
    reference: date | None = None

    def __post_init__(self) -> None:
        needed = self.period in NEEDS_COUNT
        if needed and self.n is None:
            raise InvalidValueError(f"{self.period} requires n")
        if not needed and self.n is not None:
            raise InvalidValueError(f"{self.period} does not accept n")
        if self.n is None:
            return
        if self.n < 1:
            raise InvalidValueError("n must be at least 1")
        ceiling = MAX_PERIOD_MONTHS if self.period is Period.LAST_N_MONTHS else MAX_PERIOD_DAYS
        if self.n > ceiling:
            raise InvalidValueError(f"{self.period} allows at most {ceiling}")


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
            if relative.n is None:  # pragma: no cover - the invariant prevents it
                raise InvalidValueError("LAST_N_DAYS requires n")
            # Today counts: "the last 7 days" are today and the 6 before it.
            return DateRange(start=reference - timedelta(days=relative.n - 1), end=reference)

        case Period.LAST_N_MONTHS:
            if relative.n is None:  # pragma: no cover - the invariant prevents it
                raise InvalidValueError("LAST_N_MONTHS requires n")
            # The current month counts, from its 1st.
            return DateRange(start=_months_back(reference, relative.n - 1), end=reference)
