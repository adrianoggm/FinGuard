"""Calendar arithmetic: where off-by-one-month errors slip in unnoticed."""

from datetime import date

import pytest

from finguard_banking.domain.enums import Period
from finguard_banking.domain.errors import InvalidValueError
from finguard_banking.domain.value_objects.relative_period import RelativePeriod, resolve
from finguard_banking.infrastructure.dto.banking_dto import RelativePeriodRequest

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
        (Period.THIS_QUARTER, {}, WEDNESDAY, ("2026-07-01", "2026-07-15")),
        (Period.LAST_QUARTER, {}, WEDNESDAY, ("2026-04-01", "2026-06-30")),
        (Period.THIS_SEMESTER, {}, WEDNESDAY, ("2026-07-01", "2026-07-15")),
        (Period.LAST_SEMESTER, {}, WEDNESDAY, ("2026-01-01", "2026-06-30")),
        (Period.THIS_FORTNIGHT, {}, WEDNESDAY, ("2026-07-01", "2026-07-15")),
        (Period.LAST_FORTNIGHT, {}, WEDNESDAY, ("2026-06-16", "2026-06-30")),
        (Period.LAST_WEEKEND, {}, WEDNESDAY, ("2026-07-11", "2026-07-12")),
        (Period.THIS_WEEKEND, {}, date(2026, 8, 9), ("2026-08-08", "2026-08-09")),
        (Period.LAST_WEEKEND, {}, date(2026, 8, 9), ("2026-08-01", "2026-08-02")),
        (Period.LAST_WEEKEND, {}, date(2026, 8, 10), ("2026-08-08", "2026-08-09")),
        (Period.THIS_YEAR, {}, WEDNESDAY, ("2026-01-01", "2026-07-15")),
        # Today counts: it is today and the six days before it.
        (Period.LAST_N_DAYS, {"n": 7}, WEDNESDAY, ("2026-07-09", "2026-07-15")),
        (Period.LAST_N_DAYS, {"n": 1}, WEDNESDAY, ("2026-07-15", "2026-07-15")),
        # Rolling back from today, like LAST_N_DAYS beside it: three months ago to today.
        (Period.LAST_N_MONTHS, {"n": 3}, WEDNESDAY, ("2026-04-15", "2026-07-15")),
        # `n=1` is the case that was wrong and untested. It counted the current month from its
        # 1st, so on the 15th it was a fortnight — and exactly equal to THIS_MONTH.
        (Period.LAST_N_MONTHS, {"n": 1}, WEDNESDAY, ("2026-06-15", "2026-07-15")),
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
        (Period.LAST_N_MONTHS, {"n": 3}, date(2026, 1, 10), ("2025-10-10", "2026-01-10")),
        # 31 March minus a month is 28 February. Every calendar makes this choice.
        (Period.LAST_N_MONTHS, {"n": 1}, date(2026, 3, 31), ("2026-02-28", "2026-03-31")),
        (Period.LAST_N_MONTHS, {"n": 1}, date(2024, 3, 31), ("2024-02-29", "2024-03-31")),
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
    with pytest.raises(InvalidValueError, match="requires n"):
        relative(Period.LAST_N_DAYS)


def test_a_period_without_a_count_does_not_accept_one() -> None:
    # LAST_MONTH with n=3 would be ambiguous: last month, or the last three?
    with pytest.raises(InvalidValueError, match="does not accept n"):
        relative(Period.LAST_MONTH, n=3)


def test_this_weekend_is_not_silently_moved_into_the_future() -> None:
    with pytest.raises(InvalidValueError, match="has not started"):
        resolve(relative(Period.THIS_WEEKEND), today=WEDNESDAY)


@pytest.mark.parametrize("months", [0, 25])
def test_months_are_bounded(months: int) -> None:
    with pytest.raises(InvalidValueError):
        relative(Period.LAST_N_MONTHS, n=months)


def test_an_invented_period_is_rejected() -> None:
    # A period outside the enum never reaches the domain: the DTO stops it.
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        RelativePeriodRequest.model_validate({"period": "SINCE_FOREVER"})


def test_a_resolved_range_is_never_backwards() -> None:
    # DateRange validates it, so any resolution that inverts blows up right here.
    for period in Period:
        extra = {"n": 3} if period in (Period.LAST_N_DAYS, Period.LAST_N_MONTHS) else {}
        for day in (date(2026, 1, 1), date(2026, 2, 28), date(2024, 2, 29), date(2026, 12, 31)):
            if period is Period.THIS_WEEKEND and day.weekday() < 5:
                continue
            date_range = resolve(relative(period, reference=day, **extra), today=day)
            assert date_range.start <= date_range.end


def test_the_last_month_is_not_this_month() -> None:
    """The defect, as the property that would have caught it.

    `LAST_N_MONTHS n=1` counted the current month from its 1st, which on the 4th of August is four
    days — and character for character the same range as THIS_MONTH. Two different questions
    resolved to one window, so a holder shown 53,71 € of health spending "el mes pasado" was told
    there were none "en el último mes". Both answers were true of their own range.
    """
    august = date(2026, 8, 4)

    last = resolve(relative(Period.LAST_N_MONTHS, n=1, reference=august), today=august)
    this = resolve(relative(Period.THIS_MONTH, reference=august), today=august)

    assert last != this
    assert last.start == date(2026, 7, 4), "a month back, not the 1st of this one"


def test_a_counted_month_window_is_never_a_calendar_one() -> None:
    """The generalisation, narrowed to the invariant that is actually true.

    A blanket "no two periods collide" is too strong and says so on the first run: `LAST_N_DAYS`
    with n=1 *is* today, by design, because "el último día" means today. What must never happen is
    a counted month window landing exactly on a calendar one — those are different questions, and
    the collapse of n=1 onto THIS_MONTH is what made one conversation contradict itself.

    Swept over the whole month, because the old arithmetic was correct on the 31st and absurd on
    the 1st, and a single reference date would have picked the wrong day.
    """
    for day in range(1, 29):
        reference = date(2026, 8, day)
        calendar = {
            resolve(relative(period, reference=reference), today=reference)
            for period in (Period.THIS_MONTH, Period.LAST_MONTH)
        }
        for n in (1, 2, 3):
            counted = resolve(
                relative(Period.LAST_N_MONTHS, n=n, reference=reference), today=reference
            )
            assert counted not in calendar, f"{reference}: LAST_N_MONTHS:{n} is a calendar month"


def test_the_last_n_months_covers_about_n_months() -> None:
    """A window called "three months" that spans sixty-five days is a wrong answer with a right
    label. Bounded on both sides, because the old arithmetic failed the lower one."""
    reference = date(2026, 8, 4)

    for n in (1, 2, 3, 6, 12):
        found = resolve(relative(Period.LAST_N_MONTHS, n=n, reference=reference), today=reference)
        days = (found.end - found.start).days

        assert 28 * n <= days <= 31 * n, f"n={n} spans {days} days"
