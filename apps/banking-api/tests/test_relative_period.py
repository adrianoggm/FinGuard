"""Calendar arithmetic: where off-by-one-month errors slip in unnoticed."""

from datetime import date

import pytest
from pydantic import ValidationError

from finguard_banking.domain.relative_period import Period, RelativePeriod, resolve

# A Wednesday, so "this week" and "last week" do not line up with the month.
WEDNESDAY = date(2026, 7, 15)


def relative(period: Period, **kwargs: object) -> RelativePeriod:
    return RelativePeriod(period=period, **kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("period", "extra", "reference", "expected"),
    [
        (Period.TODAY, {}, WEDNESDAY, ("2026-07-15", "2026-07-15")),
        (Period.YESTERDAY, {}, WEDNESDAY, ("2026-07-14", "2026-07-14")),
        # Monday to today: nobody asks about the money they will spend tomorrow.
        (Period.THIS_WEEK, {}, WEDNESDAY, ("2026-07-13", "2026-07-15")),
        (Period.LAST_WEEK, {}, WEDNESDAY, ("2026-07-06", "2026-07-12")),
        (Period.THIS_MONTH, {}, WEDNESDAY, ("2026-07-01", "2026-07-15")),
        (Period.LAST_MONTH, {}, WEDNESDAY, ("2026-06-01", "2026-06-30")),
        (Period.THIS_YEAR, {}, WEDNESDAY, ("2026-01-01", "2026-07-15")),
        # Today counts: it is today and the six days before it.
        (Period.LAST_N_DAYS, {"n": 7}, WEDNESDAY, ("2026-07-09", "2026-07-15")),
        (Period.LAST_N_DAYS, {"n": 1}, WEDNESDAY, ("2026-07-15", "2026-07-15")),
        # The current month counts from its 1st: July, June and May.
        (Period.LAST_N_MONTHS, {"n": 3}, WEDNESDAY, ("2026-05-01", "2026-07-15")),
        # On a Monday, "this week" is that day alone.
        (Period.THIS_WEEK, {}, date(2026, 7, 13), ("2026-07-13", "2026-07-13")),
        # On the 1st, "this month" is that day alone, and last month is complete.
        (Period.THIS_MONTH, {}, date(2026, 7, 1), ("2026-07-01", "2026-07-01")),
        (Period.LAST_MONTH, {}, date(2026, 7, 1), ("2026-06-01", "2026-06-30")),
        # 31 March: the previous month has 28 days, not 31.
        (Period.LAST_MONTH, {}, date(2026, 3, 31), ("2026-02-01", "2026-02-28")),
        # Leap year.
        (Period.LAST_MONTH, {}, date(2024, 3, 15), ("2024-02-01", "2024-02-29")),
        # Crossing back over a year boundary.
        (Period.LAST_MONTH, {}, date(2026, 1, 10), ("2025-12-01", "2025-12-31")),
        (Period.LAST_N_MONTHS, {"n": 3}, date(2026, 1, 10), ("2025-11-01", "2026-01-10")),
        (Period.LAST_WEEK, {}, date(2026, 1, 1), ("2025-12-22", "2025-12-28")),
        (Period.YESTERDAY, {}, date(2026, 1, 1), ("2025-12-31", "2025-12-31")),
    ],
)
def test_it_resolves_the_range(
    period: Period, extra: dict[str, int], reference: date, expected: tuple[str, str]
) -> None:
    date_range = resolve(relative(period, reference=reference, **extra), today=date(2000, 1, 1))

    assert (date_range.start.isoformat(), date_range.end.isoformat()) == expected


def test_without_a_reference_it_uses_the_today_it_is_given() -> None:
    # The function is pure: "today" comes in as an argument, it is never read.
    date_range = resolve(relative(Period.TODAY), today=date(2026, 2, 3))

    assert date_range.start == date(2026, 2, 3)


def test_a_period_with_a_count_requires_it() -> None:
    with pytest.raises(ValidationError, match="requires n"):
        relative(Period.LAST_N_DAYS)


def test_a_period_without_a_count_does_not_accept_one() -> None:
    # LAST_MONTH with n=3 would be ambiguous: last month, or the last three?
    with pytest.raises(ValidationError, match="does not accept n"):
        relative(Period.LAST_MONTH, n=3)


@pytest.mark.parametrize("months", [0, 25])
def test_months_are_bounded(months: int) -> None:
    with pytest.raises(ValidationError):
        relative(Period.LAST_N_MONTHS, n=months)


def test_an_invented_period_is_rejected() -> None:
    with pytest.raises(ValidationError):
        RelativePeriod.model_validate({"period": "SINCE_FOREVER"})


def test_a_resolved_range_is_never_backwards() -> None:
    # DateRange validates it, so any resolution that inverts blows up right here.
    for period in Period:
        extra = {"n": 3} if period in (Period.LAST_N_DAYS, Period.LAST_N_MONTHS) else {}
        for day in (date(2026, 1, 1), date(2026, 2, 28), date(2024, 2, 29), date(2026, 12, 31)):
            date_range = resolve(relative(period, reference=day, **extra), today=day)
            assert date_range.start <= date_range.end
