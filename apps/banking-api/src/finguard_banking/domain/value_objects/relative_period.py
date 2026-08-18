"""Resolves relative time expressions into a concrete date range.

The LLM names the period (`LAST_MONTH`), it does not compute it: calendar arithmetic
inside a generative model is exactly where silent off-by-one-month errors come from.
`resolve` is a pure function — "today" comes in as an argument — so the result never
depends on when it runs.
"""

from calendar import monthrange
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


def _same_day_months_back(day: date, months: int) -> date:
    """The same date `months` earlier, clamped to the end of a shorter month.

    31 March minus one month is 28 February, because 31 February is not a date and refusing to
    answer would be worse. Every calendar makes the same choice.
    """
    total = day.year * 12 + (day.month - 1) - months
    year, month = total // 12, total % 12 + 1
    return date(year, month, min(day.day, monthrange(year, month)[1]))


def _quarter_start(day: date) -> date:
    return date(day.year, ((day.month - 1) // 3) * 3 + 1, 1)


def _semester_start(day: date) -> date:
    return date(day.year, 1 if day.month <= 6 else 7, 1)


def _fortnight_start(day: date) -> date:
    return day.replace(day=1 if day.day <= 15 else 16)


def _previous_fortnight(day: date) -> DateRange:
    if day.day > 15:
        return DateRange(start=day.replace(day=1), end=day.replace(day=15))
    previous_end = day.replace(day=1) - timedelta(days=1)
    return DateRange(start=previous_end.replace(day=16), end=previous_end)


def _previous_weekend(day: date) -> DateRange:
    if day.weekday() <= 4:
        sunday = day - timedelta(days=day.weekday() + 1)
    else:
        current_saturday = day - timedelta(days=day.weekday() - 5)
        sunday = current_saturday - timedelta(days=6)
    return DateRange(start=sunday - timedelta(days=1), end=sunday)


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

        case Period.THIS_QUARTER:
            return DateRange(start=_quarter_start(reference), end=reference)

        case Period.LAST_QUARTER:
            end = _quarter_start(reference) - timedelta(days=1)
            return DateRange(start=_quarter_start(end), end=end)

        case Period.THIS_SEMESTER:
            return DateRange(start=_semester_start(reference), end=reference)

        case Period.LAST_SEMESTER:
            end = _semester_start(reference) - timedelta(days=1)
            return DateRange(start=_semester_start(end), end=end)

        case Period.THIS_FORTNIGHT:
            return DateRange(start=_fortnight_start(reference), end=reference)

        case Period.LAST_FORTNIGHT:
            return _previous_fortnight(reference)

        case Period.THIS_WEEKEND:
            if reference.weekday() < 5:
                raise InvalidValueError("THIS_WEEKEND has not started yet")
            saturday = reference - timedelta(days=reference.weekday() - 5)
            return DateRange(start=saturday, end=reference)

        case Period.LAST_WEEKEND:
            return _previous_weekend(reference)

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
            # Rolling back from today, like LAST_N_DAYS beside it.
            #
            # It used to count the *current* month as one of the N and start on its 1st, which
            # made `n=1` on the 4th of a month a window of four days — and exactly equal to
            # THIS_MONTH, so two different questions resolved to one range. A holder asked
            # "¿en el último mes?" about a category the assistant had just reported 53,71 € of,
            # and was told there were no movements. Both answers were true of their own window and
            # the conversation contradicted itself.
            #
            # Rolling also makes the pair coherent: "los últimos 7 días" ends today and starts
            # seven days ago, so "los últimos 3 meses" ends today and starts three months ago. The
            # calendar alignment was the odd one out and it shortened every window by most of a
            # month.
            return DateRange(start=_same_day_months_back(reference, relative.n), end=reference)
