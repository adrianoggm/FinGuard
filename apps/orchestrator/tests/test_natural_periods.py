"""Quarter, semester, fortnight and weekend windows keep their calendar meaning."""

from datetime import date

import pytest

from finguard_orchestrator.domain.enums import Period
from finguard_orchestrator.domain.services import window_words
from finguard_orchestrator.domain.value_objects.financial_plan import AbsoluteRange, RelativePeriod

TODAY = date(2026, 8, 9)  # Sunday, in Q3, H2 and the first fortnight.


@pytest.mark.parametrize(
    ("sentence", "period", "count"),
    [
        ("este trimestre", Period.THIS_QUARTER, None),
        ("el trimestre anterior", Period.LAST_QUARTER, None),
        ("el último trimestre", Period.LAST_N_MONTHS, 3),
        ("este semestre", Period.THIS_SEMESTER, None),
        ("el semestre pasado", Period.LAST_SEMESTER, None),
        ("el último semestre", Period.LAST_N_MONTHS, 6),
        ("esta quincena", Period.THIS_FORTNIGHT, None),
        ("la quincena pasada", Period.LAST_FORTNIGHT, None),
        ("este fin de semana", Period.THIS_WEEKEND, None),
        ("el fin de semana pasado", Period.LAST_WEEKEND, None),
    ],
)
def test_relative_natural_periods_stay_typed(
    sentence: str, period: Period, count: int | None
) -> None:
    assert window_words.window_in(sentence, today=TODAY) == RelativePeriod(period, n=count)


@pytest.mark.parametrize(
    ("sentence", "expected"),
    [
        (
            "el segundo trimestre de 2025",
            AbsoluteRange(date(2025, 4, 1), date(2025, 6, 30)),
        ),
        (
            "el cuarto trimestre",
            AbsoluteRange(date(2025, 10, 1), date(2025, 12, 31)),
        ),
        (
            "el primer semestre de este año",
            AbsoluteRange(date(2026, 1, 1), date(2026, 6, 30)),
        ),
        (
            "la segunda quincena de julio de 2026",
            AbsoluteRange(date(2026, 7, 16), date(2026, 7, 31)),
        ),
        (
            "la primera quincena de febrero de 2024",
            AbsoluteRange(date(2024, 2, 1), date(2024, 2, 15)),
        ),
    ],
)
def test_numbered_calendar_blocks_become_exact_ranges(
    sentence: str, expected: AbsoluteRange
) -> None:
    assert window_words.window_in(sentence, today=TODAY) == expected


def test_this_weekend_is_not_guessed_before_it_starts() -> None:
    wednesday = date(2026, 8, 5)

    assert window_words.window_in("este fin de semana", today=wednesday) is None
    assert window_words.window_in("el fin de semana pasado", today=wednesday) == RelativePeriod(
        Period.LAST_WEEKEND
    )
