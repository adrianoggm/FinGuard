"""What shape a window is, and what precedes it.

Twenty-nine capture groups of date grammar live in `window_words` and stay there; this is the small
piece above it that reads a window that has *already* been resolved: is this range a whole month, a
whole quarter, a weekend — and what is the range one calendar unit before it.

It is its own module because two reader groups need it and would otherwise each grow a copy. The
comparison readers ask "what is the period before the one in view" to build their second operand;
`follow_up`'s reader asks the same question for «¿y el mes anterior?». That is one definition of
what precedes a window, and the comment on `_A_CALENDAR_UNIT_BACK` says what the second one cost:
June in one sentence and July in the other.

The handler reads `_is_whole_month` and `_is_whole_year` too, for the label it puts on a
comparison. That is the same question again and not a third answer to it.
"""

from __future__ import annotations

from datetime import date, timedelta

from finguard_orchestrator.domain.enums import (
    Period,
)
from finguard_orchestrator.domain.services import (
    window_words,
)
from finguard_orchestrator.domain.value_objects.financial_plan import (
    AbsoluteRange,
    FinancialPlan,
    RelativePeriod,
)


def _previous_calendar_window(
    plan: FinancialPlan, unit: str, today: date
) -> window_words.Window | None:
    """The calendar window directly before a compatible plan window."""
    if unit == "periodo":
        unit = _calendar_unit_of(plan) or ""
    if unit in {"mes", "semana", "ano"}:
        # The week, the month and the year are `window_words.window_before`'s, which the follow-up
        # patch reads too when a turn says «¿y la anterior?» and names no unit (validation fix 1,
        # F6): one definition for both, and last week steps back to the week before it there.
        window = plan.period or plan.date_range
        return window_words.window_before(window, unit, today) if window is not None else None
    if unit == "trimestre":
        if plan.period and plan.period.period is Period.THIS_QUARTER:
            return RelativePeriod(Period.LAST_QUARTER)
        if plan.period and plan.period.period is Period.LAST_QUARTER:
            current = window_words.whole_quarter(today.year, (today.month - 1) // 3 + 1)
            last_end = current.start - timedelta(days=1)
            last = window_words.whole_quarter(last_end.year, (last_end.month - 1) // 3 + 1)
            before = last.start - timedelta(days=1)
            return window_words.whole_quarter(before.year, (before.month - 1) // 3 + 1)
        if plan.date_range and _is_whole_quarter(plan.date_range):
            before = plan.date_range.start - timedelta(days=1)
            return window_words.whole_quarter(before.year, (before.month - 1) // 3 + 1)
    if unit == "semestre":
        if plan.period and plan.period.period is Period.THIS_SEMESTER:
            return RelativePeriod(Period.LAST_SEMESTER)
        if plan.period and plan.period.period is Period.LAST_SEMESTER:
            current = window_words.whole_semester(today.year, 1 if today.month <= 6 else 2)
            last_end = current.start - timedelta(days=1)
            last = window_words.whole_semester(last_end.year, 1 if last_end.month <= 6 else 2)
            before = last.start - timedelta(days=1)
            return window_words.whole_semester(before.year, 1 if before.month <= 6 else 2)
        if plan.date_range and _is_whole_semester(plan.date_range):
            before = plan.date_range.start - timedelta(days=1)
            return window_words.whole_semester(before.year, 1 if before.month <= 6 else 2)
    if unit == "quincena":
        if plan.period and plan.period.period is Period.THIS_FORTNIGHT:
            return RelativePeriod(Period.LAST_FORTNIGHT)
        if plan.period and plan.period.period is Period.LAST_FORTNIGHT:
            current = window_words.whole_fortnight(
                today.year, today.month, 1 if today.day <= 15 else 2
            )
            last_end = current.start - timedelta(days=1)
            last = window_words.whole_fortnight(
                last_end.year, last_end.month, 1 if last_end.day <= 15 else 2
            )
            before = last.start - timedelta(days=1)
            return window_words.whole_fortnight(
                before.year, before.month, 1 if before.day <= 15 else 2
            )
        if plan.date_range and _is_whole_fortnight(plan.date_range):
            before = plan.date_range.start - timedelta(days=1)
            return window_words.whole_fortnight(
                before.year, before.month, 1 if before.day <= 15 else 2
            )
    if unit == "fin de semana":
        if plan.period and plan.period.period is Period.THIS_WEEKEND:
            return RelativePeriod(Period.LAST_WEEKEND)
        if plan.period and plan.period.period is Period.LAST_WEEKEND:
            last = _last_completed_weekend(today)
            return AbsoluteRange(
                start=last.start - timedelta(days=7),
                end=last.end - timedelta(days=7),
            )
        if plan.date_range and _is_weekend(plan.date_range):
            return AbsoluteRange(
                start=plan.date_range.start - timedelta(days=7),
                end=plan.date_range.end - timedelta(days=7),
            )
    return None


def _calendar_month_window(plan: FinancialPlan, today: date) -> AbsoluteRange | None:
    """Resolve only month-sized windows, for comparisons whose unit is one payroll."""
    if plan.date_range is not None and _is_whole_month(plan.date_range):
        return plan.date_range
    if plan.period is None:
        return None
    if plan.period.period is Period.THIS_MONTH:
        return window_words.whole_month(today.year, today.month)
    if plan.period.period is Period.LAST_MONTH:
        year, month = _month_before(today.year, today.month)
        return window_words.whole_month(year, month)
    return None


def _calendar_unit_of(plan: FinancialPlan) -> str | None:
    if plan.period is not None:
        return {
            Period.THIS_MONTH: "mes",
            Period.LAST_MONTH: "mes",
            Period.THIS_WEEK: "semana",
            Period.THIS_YEAR: "ano",
            Period.THIS_QUARTER: "trimestre",
            Period.LAST_QUARTER: "trimestre",
            Period.THIS_SEMESTER: "semestre",
            Period.LAST_SEMESTER: "semestre",
            Period.THIS_FORTNIGHT: "quincena",
            Period.LAST_FORTNIGHT: "quincena",
            Period.THIS_WEEKEND: "fin de semana",
            Period.LAST_WEEKEND: "fin de semana",
        }.get(plan.period.period)
    if plan.date_range is not None:
        if _is_whole_month(plan.date_range):
            return "mes"
        if _is_whole_year(plan.date_range):
            return "ano"
        if _is_whole_quarter(plan.date_range):
            return "trimestre"
        if _is_whole_semester(plan.date_range):
            return "semestre"
        if _is_whole_fortnight(plan.date_range):
            return "quincena"
        if _is_weekend(plan.date_range):
            return "fin de semana"
        if (plan.date_range.end - plan.date_range.start).days == 6:
            return "semana"
    return None


def _month_before(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def _is_whole_month(window: AbsoluteRange) -> bool:
    return window == window_words.whole_month(window.start.year, window.start.month)


def _is_whole_year(window: AbsoluteRange) -> bool:
    return window == window_words.whole_year(window.start.year)


def _is_whole_quarter(window: AbsoluteRange) -> bool:
    quarter = (window.start.month - 1) // 3 + 1
    return window == window_words.whole_quarter(window.start.year, quarter)


def _is_whole_semester(window: AbsoluteRange) -> bool:
    semester = 1 if window.start.month <= 6 else 2
    return window == window_words.whole_semester(window.start.year, semester)


def _is_whole_fortnight(window: AbsoluteRange) -> bool:
    fortnight = 1 if window.start.day <= 15 else 2
    return window == window_words.whole_fortnight(window.start.year, window.start.month, fortnight)


def _is_weekend(window: AbsoluteRange) -> bool:
    return (
        window.start.weekday() == 5
        and window.end.weekday() == 6
        and (window.end - window.start).days == 1
    )


def _last_completed_weekend(today: date) -> AbsoluteRange:
    if today.weekday() <= 4:
        sunday = today - timedelta(days=today.weekday() + 1)
    else:
        current_saturday = today - timedelta(days=today.weekday() - 5)
        sunday = current_saturday - timedelta(days=6)
    return AbsoluteRange(start=sunday - timedelta(days=1), end=sunday)
