"""Conservative recurring-expense discovery from complete ledger evidence."""

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo

from finguard_orchestrator.domain.enums import (
    CounterpartyType,
    ReceiptCategory,
    TransactionType,
)
from finguard_orchestrator.domain.models.query_answer import Movement, QueryAnswer
from finguard_orchestrator.domain.services import spanish

Cadence = Literal["WEEKLY", "FORTNIGHTLY", "MONTHLY", "QUARTERLY"]
GroupKey = tuple[CounterpartyType, TransactionType | None, str]


@dataclass(frozen=True)
class RecurringExpense:
    counterparty: str
    category: str | None
    counterparty_type: CounterpartyType
    transaction_type: TransactionType | None
    receipt_category: ReceiptCategory | None
    cadence: Cadence
    occurrences: int
    total_minor: int
    average_minor: int
    stable_amount: bool
    day_of_month_min: int | None = None
    day_of_month_max: int | None = None


@dataclass(frozen=True)
class RecurrenceAnalysis:
    movement_count: int
    complete: bool
    candidates: tuple[RecurringExpense, ...] = ()


def analyse(answer: QueryAnswer, *, reporting_timezone: str) -> RecurrenceAnalysis:
    """Find repeated counterparties, but only when the complete result page is present.

    A merchant is recurring when its calendar dates form a recognisable schedule. Amount stability
    is reported separately: rent and utility-like bills may recur while their amount changes. This
    deliberately does not use merchant popularity, so ten irregular supermarket visits are not
    promoted to a subscription merely because there are many of them.
    """
    complete = len(answer.movements) == answer.movement_count
    if not complete:
        return RecurrenceAnalysis(movement_count=answer.movement_count, complete=False)

    grouped: dict[GroupKey, list[Movement]] = defaultdict(list)
    labels: dict[GroupKey, str] = {}
    for movement in answer.movements:
        label = (
            movement.counterparty_name or movement.merchant_name or movement.description
        ).strip()
        if not label:
            continue
        key = (
            movement.counterparty_type,
            movement.transaction_type,
            spanish.folded(label),
        )
        labels.setdefault(key, label)
        grouped[key].append(movement)

    candidates: list[RecurringExpense] = []
    for key, movements in grouped.items():
        if len(movements) < 3:
            continue
        days = sorted(_local_date(item.occurred_at, reporting_timezone) for item in movements)
        cadence = _cadence(days)
        if cadence is None:
            continue
        amounts = tuple(abs(item.amount_minor) for item in movements)
        average = _rounded_mean(sum(amounts), len(amounts))
        categories = Counter(item.category for item in movements if item.category)
        category = categories.most_common(1)[0][0] if categories else None
        receipt_categories = Counter(
            item.receipt_category for item in movements if item.receipt_category is not None
        )
        receipt_category = receipt_categories.most_common(1)[0][0] if receipt_categories else None
        monthly = cadence == "MONTHLY"
        candidates.append(
            RecurringExpense(
                counterparty=labels[key],
                category=category,
                counterparty_type=key[0],
                transaction_type=key[1],
                receipt_category=receipt_category,
                cadence=cadence,
                occurrences=len(movements),
                total_minor=sum(amounts),
                average_minor=average,
                stable_amount=_stable_amount(amounts, average),
                day_of_month_min=min(day.day for day in days) if monthly else None,
                day_of_month_max=max(day.day for day in days) if monthly else None,
            )
        )

    return RecurrenceAnalysis(
        movement_count=answer.movement_count,
        complete=True,
        candidates=tuple(
            sorted(candidates, key=lambda item: (-item.total_minor, item.counterparty.casefold()))
        ),
    )


def _cadence(days: list[date]) -> Cadence | None:
    months = [(day.year, day.month) for day in days]
    if len(set(months)) == len(days) and _consecutive_months(months):
        return "MONTHLY"

    intervals = tuple(
        (latest - earlier).days for earlier, latest in zip(days, days[1:], strict=False)
    )
    if len(days) >= 4 and _mostly_between(intervals, 5, 9):
        return "WEEKLY"
    if len(days) >= 4 and _mostly_between(intervals, 12, 17):
        return "FORTNIGHTLY"
    if len(days) >= 3 and _mostly_between(intervals, 75, 100):
        return "QUARTERLY"
    return None


def _consecutive_months(months: list[tuple[int, int]]) -> bool:
    ordered = sorted(year * 12 + month for year, month in months)
    return all(latest - earlier == 1 for earlier, latest in zip(ordered, ordered[1:], strict=False))


def _mostly_between(intervals: tuple[int, ...], minimum: int, maximum: int) -> bool:
    matching = sum(minimum <= interval <= maximum for interval in intervals)
    return matching * 4 >= len(intervals) * 3


def _stable_amount(amounts: tuple[int, ...], average: int) -> bool:
    if not amounts:
        return False
    tolerance = max(100, average // 10)
    return max(amounts) - min(amounts) <= tolerance


def _rounded_mean(total: int, count: int) -> int:
    units, remainder = divmod(total, count)
    return units + int(remainder * 2 >= count)


def _local_date(value: datetime, reporting_timezone: str) -> date:
    if value.tzinfo is None:
        return value.date()
    return value.astimezone(ZoneInfo(reporting_timezone)).date()
