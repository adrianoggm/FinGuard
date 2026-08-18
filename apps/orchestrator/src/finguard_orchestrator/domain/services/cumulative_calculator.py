"""Running totals over exact calendar buckets returned by banking."""

from datetime import date, timedelta

from finguard_orchestrator.domain.enums import GroupBy
from finguard_orchestrator.domain.models.query_answer import Group, QueryAnswer


def accumulate(answer: QueryAnswer, group_by: GroupBy) -> QueryAnswer:
    """Return the same evidence with cumulative, gap-complete temporal groups.

    Banking remains the authority for every bucket amount. Missing calendar buckets contribute
    zero and therefore repeat the previous running total; no money is estimated or interpolated.
    """
    by_key = {group.key: group for group in answer.groups}
    keys = _bucket_keys(answer, group_by) or sorted(by_key)
    total = 0
    count = 0
    groups: list[Group] = []
    for key in keys:
        bucket = by_key.get(key)
        if bucket is not None:
            total += bucket.total_minor
            count += bucket.movement_count
        groups.append(Group(key=key, total_minor=total, movement_count=count))
    return QueryAnswer(
        currency=answer.currency,
        total_minor=answer.total_minor,
        movement_count=answer.movement_count,
        period_start=answer.period_start,
        period_end=answer.period_end,
        groups=tuple(groups),
        movements=answer.movements,
    )


def _bucket_keys(answer: QueryAnswer, group_by: GroupBy) -> list[str]:
    start = answer.period_start
    end = answer.period_end
    if start is None or end is None or start > end:
        return []
    if group_by is GroupBy.DAY:
        return [day.isoformat() for day in _days(start, end)]
    if group_by is GroupBy.WEEK:
        first = start - timedelta(days=start.weekday())
        return [day.isoformat() for day in _days(first, end, step=7)]
    if group_by is GroupBy.MONTH:
        keys: list[str] = []
        year, month = start.year, start.month
        while (year, month) <= (end.year, end.month):
            keys.append(f"{year:04d}-{month:02d}")
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)
        return keys
    return []


def _days(start: date, end: date, *, step: int = 1) -> list[date]:
    count = (end - start).days // step + 1
    return [start + timedelta(days=index * step) for index in range(count)]
