"""Frequency is a typed question over complete ledger dates, not a prose heuristic."""

from datetime import UTC, datetime

from finguard_orchestrator.domain.models.query_answer import Movement, QueryAnswer
from finguard_orchestrator.domain.services import frequency_analyzer


def movements_on(days: list[datetime], *, direction: str = "EXPENSE") -> QueryAnswer:
    return QueryAnswer(
        currency="EUR",
        total_minor=5_000 * len(days),
        movement_count=len(days),
        movements=tuple(
            Movement(day, "Movimiento", 5_000, direction, merchant_name="Comercio") for day in days
        ),
    )


def test_frequency_bins_dates_in_the_reporting_timezone() -> None:
    answer = movements_on(
        [
            datetime(2026, 1, 31, 23, 30, tzinfo=UTC),
            datetime(2026, 2, 28, 23, 30, tzinfo=UTC),
        ]
    )

    pattern = frequency_analyzer.analyse(answer, reporting_timezone="Europe/Madrid")

    assert pattern.monthly
    assert pattern.day_of_month_min == pattern.day_of_month_max == 1


def test_skipped_months_are_not_called_a_monthly_recurrence() -> None:
    answer = movements_on([datetime(2026, 1, 10), datetime(2026, 3, 10)])

    pattern = frequency_analyzer.analyse(answer, reporting_timezone="Europe/Madrid")

    assert not pattern.monthly
    assert pattern.median_interval_days == 59
