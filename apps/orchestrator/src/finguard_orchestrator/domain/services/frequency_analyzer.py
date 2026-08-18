"""Deterministic cadence derived from complete, bounded ledger evidence."""

from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from finguard_orchestrator.domain.models.query_answer import QueryAnswer


@dataclass(frozen=True)
class FrequencyAnalysis:
    count: int
    complete: bool
    median_interval_days: int | None = None
    monthly: bool = False
    day_of_month_min: int | None = None
    day_of_month_max: int | None = None
    usual_weekday: int | None = None


def analyse(answer: QueryAnswer, *, reporting_timezone: str) -> FrequencyAnalysis:
    """Summarise intervals only when every matching movement is present."""
    complete = len(answer.movements) == answer.movement_count
    if not complete or answer.movement_count < 2:
        return FrequencyAnalysis(count=answer.movement_count, complete=complete)

    days = sorted(
        _local_date(movement.occurred_at, reporting_timezone) for movement in answer.movements
    )
    intervals = tuple(
        (latest - earlier).days for earlier, latest in zip(days, days[1:], strict=False)
    )
    months = sorted({(day.year, day.month) for day in days})
    monthly = len(months) == len(days) and _are_consecutive_months(months)
    weekday_counts = Counter(day.weekday() for day in days)
    weekday, weekday_count = weekday_counts.most_common(1)[0]
    usual_weekday = weekday if weekday_count >= 2 and weekday_count * 2 >= len(days) else None
    return FrequencyAnalysis(
        count=answer.movement_count,
        complete=True,
        median_interval_days=_integer_median(intervals),
        monthly=monthly,
        day_of_month_min=min(day.day for day in days) if monthly else None,
        day_of_month_max=max(day.day for day in days) if monthly else None,
        usual_weekday=usual_weekday,
    )


def _local_date(value: datetime, reporting_timezone: str) -> date:
    # Old test fixtures are deliberately naive and already represent reporting-local time.
    if value.tzinfo is None:
        return value.date()
    return value.astimezone(ZoneInfo(reporting_timezone)).date()


def _integer_median(values: tuple[int, ...]) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle] + 1) // 2


def _are_consecutive_months(months: list[tuple[int, int]]) -> bool:
    ordinals = tuple(year * 12 + month for year, month in months)
    return all(
        latest - earlier == 1 for earlier, latest in zip(ordinals, ordinals[1:], strict=False)
    )
