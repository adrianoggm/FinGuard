"""Reading a window out of a sentence, without a model.

The clarification the planner asks for is almost always the window — "¿de qué periodo?" — and
a window is a closed vocabulary exactly like a category. So when somebody answers "este año",
there is nothing to infer and nothing to ask a model about.

This is what makes a clarification finishable. Before it, answering the question restarted the
whole conversation: the reply "este año" arrived as a two-word sentence with no context, and
the planner had to derive a complete plan from it.

Precision over coverage, and the exclusions are deliberate. "Últimamente" and "hace un mes"
are windows a person understands and this does not, so they fall through to the model rather
than being guessed at — the cost of a miss is one more turn, and the cost of a wrong guess is
a figure about the wrong month.
"""

from datetime import date

import pytest

from finguard_orchestrator.domain.enums import Period
from finguard_orchestrator.domain.services import window_words
from finguard_orchestrator.domain.value_objects.financial_plan import AbsoluteRange, RelativePeriod


@pytest.mark.parametrize(
    ("sentence", "period", "count"),
    [
        ("hoy", Period.TODAY, None),
        ("ayer", Period.YESTERDAY, None),
        ("esta semana", Period.THIS_WEEK, None),
        ("la semana pasada", Period.LAST_WEEK, None),
        ("este mes", Period.THIS_MONTH, None),
        ("el mes pasado", Period.LAST_MONTH, None),
        ("este año", Period.THIS_YEAR, None),
        ("en lo que va de año", Period.THIS_YEAR, None),
        ("los últimos 30 días", Period.LAST_N_DAYS, 30),
        ("últimos 7 dias", Period.LAST_N_DAYS, 7),
        ("los últimos 3 meses", Period.LAST_N_MONTHS, 3),
        ("en los últimos 6 meses", Period.LAST_N_MONTHS, 6),
        # Inside a longer sentence, because a reply to "¿de qué periodo?" is often a small sentence
        # rather than a bare phrase.
        ("pues el mes pasado", Period.LAST_MONTH, None),
        ("me refería a este año", Period.THIS_YEAR, None),
    ],
)
def test_a_window_this_vocabulary_knows_is_read(
    sentence: str, period: Period, count: int | None
) -> None:
    window = window_words.window_in(sentence)

    assert window is not None
    assert window.period is period
    assert window.n == count


@pytest.mark.parametrize(
    "sentence",
    [
        "",
        "no sé",
        "de gasolina",
        # Windows a person understands and this does not. They fall through to the model rather than
        # being guessed: a miss costs one turn, a wrong guess costs a figure about the wrong month.
        "últimamente",
        "hace un mes",
        "desde que empecé a trabajar",
        "en verano",
    ],
)
def test_anything_else_is_not_a_window(sentence: str) -> None:
    assert window_words.window_in(sentence) is None


def test_last_year_needs_a_reference_day_and_not_a_period_name() -> None:
    """It was excluded because banking's period vocabulary has no LAST_YEAR. That was the wrong
    conclusion from a right premise: a plan carries either a period or an explicit range, and a
    calendar year is a range. Without a reference day there is still nothing to resolve it against,
    and this service does not read a clock."""
    assert window_words.window_in("el año pasado") is None


def test_the_longest_reading_wins() -> None:
    """Both "el mes pasado" and "este mes" contain the word "mes", so whichever of the two
    phrases is longer is the one the person actually said."""
    assert window_words.window_in("el mes pasado").period is Period.LAST_MONTH
    assert window_words.window_in("este mes").period is Period.THIS_MONTH


def test_a_count_out_of_range_is_not_a_window() -> None:
    """The domain bounds `n`, so a sentence asking for 5000 days is not a window this can build.
    Returning None sends it to the model, which is better than raising from a reader."""
    assert window_words.window_in("los últimos 5000 días") is None


def test_the_singular_forms_are_read_too() -> None:
    """Somebody answering "el último mes" means one month, and "en el último día" means one day.
    Both are counted periods with n=1 rather than a separate name."""
    assert window_words.window_in("el último mes") == window_words.window_in("los últimos 1 meses")


# --- the phrasings the loop was made of ----------------------------------------------
#
# From the report, answering "¿de qué periodo?":
#
#     "del año"   → "¿De qué año? Por ejemplo 2025 o el año actual."
#     "de este"   → "¿De qué periodo? Por ejemplo este mes…"
#
# Neither was read, so both went to the model and the conversation went round. "Del año" is this
# year in any reading of it, and so is "en el año".


@pytest.mark.parametrize(
    "sentence",
    ["del año", "de este año", "en el año", "el año", "durante el año", "de todo el año"],
)
def test_the_year_is_read_however_it_is_phrased(sentence: str) -> None:
    window = window_words.window_in(sentence)

    assert window is not None
    assert window.period is Period.THIS_YEAR


def test_last_year_is_still_not_this_year() -> None:
    """The one that must not be swept up. "Del año pasado" ends in a phrase the table reads as this
    year, and answering it as this year would be a figure about a different one."""
    assert window_words.window_in("del año pasado") != RelativePeriod(period=Period.THIS_YEAR)
    assert window_words.window_in("el año pasado") != RelativePeriod(period=Period.THIS_YEAR)


# --- years, months and the loop they caused -------------------------------------------
#
# From the report:
#
#     "el último año"   → not read, so the model asked for the period again, twice
#     "el año anterior" → answered as the last twelve months, after a question about July
#     "durante todo el año 2025" → the window did not change at all
#
# The first is a counted period in the unit the contract has. The other two are ranges.

TODAY = date(2026, 8, 4)


@pytest.mark.parametrize(
    ("sentence", "months"),
    [("el último año", 12), ("los últimos 2 años", 24), ("últimos 2 años", 24)],
)
def test_a_counted_year_is_counted_in_months(sentence: str, months: int) -> None:
    """LAST_N_MONTHS is the only counted window longer than a day the contract has, so a year is
    twelve of them. Leaving the unit out is what sent "el último año" to the model."""
    assert window_words.window_in(sentence, today=TODAY) == RelativePeriod(
        period=Period.LAST_N_MONTHS, n=months
    )


@pytest.mark.parametrize(
    "sentence",
    ["en 2025", "durante 2025", "del 2025", "todo el año 2025", "el año 2025", "en el año 2025"],
)
def test_a_named_year_is_the_range_it_is(sentence: str) -> None:
    assert window_words.window_in(sentence, today=TODAY) == AbsoluteRange(
        start=date(2025, 1, 1), end=date(2025, 12, 31)
    )


def test_last_year_is_the_previous_calendar_year() -> None:
    assert window_words.window_in("el año pasado", today=TODAY) == AbsoluteRange(
        start=date(2025, 1, 1), end=date(2025, 12, 31)
    )


def test_a_four_digit_number_that_is_not_a_year_is_not_a_window() -> None:
    """A marker is required. "Gasté 2025 euros" is an amount, and reading it as a year would
    answer a question about one purchase with a whole calendar.

    The window here is August, which the sentence does name. It used to be None, because a month
    with no year was not a window this file knew — the assertion was about the number all along and
    now says so without depending on the month being unreadable.
    """
    assert window_words.window_in(
        "gasté 2025 euros en agosto", today=TODAY
    ) == window_words.whole_month(TODAY.year, 8)
    assert window_words.window_in("gasté 2025 euros", today=TODAY) is None


@pytest.mark.parametrize(
    ("sentence", "start", "end"),
    [
        ("en julio de 2026", date(2026, 7, 1), date(2026, 7, 31)),
        ("julio de este año", date(2026, 7, 1), date(2026, 7, 31)),
        ("en febrero de 2024", date(2024, 2, 1), date(2024, 2, 29)),
        ("en febrero de 2025", date(2025, 2, 1), date(2025, 2, 28)),
    ],
)
def test_a_month_of_a_year_is_that_month(sentence: str, start: date, end: date) -> None:
    """Read before the year, because "julio de 2026" contains "de 2026" — a year rule that ran
    first would answer a question about one month with twelve. February is in here because it is
    the month whose last day moves."""
    assert window_words.window_in(sentence, today=TODAY) == AbsoluteRange(start=start, end=end)


def test_the_year_before_shifts_the_window_it_follows() -> None:
    """ "¿Y el año anterior?" after a question about July is July of last year — not the last twelve
    months, and not the whole of last year."""
    july = AbsoluteRange(start=date(2026, 7, 1), end=date(2026, 7, 31))

    assert window_words.a_year_earlier("¿y el año anterior?", july) == AbsoluteRange(
        start=date(2025, 7, 1), end=date(2025, 7, 31)
    )


def test_only_a_bare_continuation_shifts_a_window() -> None:
    """A sentence carrying its own question is a new question. Shifting the previous window for it
    would answer something nobody asked, which is the rule every continuation reader follows."""
    july = AbsoluteRange(start=date(2026, 7, 1), end=date(2026, 7, 31))

    assert window_words.a_year_earlier("¿cuánto gasté el año anterior en ocio?", july) is None
    assert window_words.a_year_earlier("¿y el año anterior?", None) is None


# --- a month with no year, which used to be no window at all ------------------------------


def test_a_bare_hoy_no_longer_outranks_the_month_beside_it() -> None:
    """The reported sentence. "En junio" was not a window this file knew, so the phrase table found
    "hoy" and answered about today — a real figure, about the wrong thirty days, with nothing
    anywhere to contradict it."""
    assert window_words.window_in(
        "hoy quiero saber cuánto gasté en junio", today=TODAY
    ) == AbsoluteRange(start=date(2026, 6, 1), end=date(2026, 6, 30))


@pytest.mark.parametrize(
    ("sentence", "start", "end"),
    [
        # Already past this year, so it is this year's.
        ("¿cuánto gasté en junio?", date(2026, 6, 1), date(2026, 6, 30)),
        # The month under way counts as having happened: it is the one somebody means.
        ("¿cuánto gasté en agosto?", date(2026, 8, 1), date(2026, 8, 31)),
        # Not yet come round, so it is last year's. Nobody says "en octubre" about a month four
        # months away — that is "el octubre que viene", and this reads no futures.
        ("¿cuánto gasté en octubre?", date(2025, 10, 1), date(2025, 10, 31)),
        ("durante diciembre", date(2025, 12, 1), date(2025, 12, 31)),
    ],
)
def test_a_month_with_no_year_is_the_last_time_it_happened(
    sentence: str, start: date, end: date
) -> None:
    assert window_words.window_in(sentence, today=TODAY) == AbsoluteRange(start=start, end=end)


@pytest.mark.parametrize(
    "sentence",
    ["el mes 7", "prueba el mes 7", "el mes 7 y si no el 8"],
)
def test_a_numbered_month_answer_is_a_calendar_month(sentence: str) -> None:
    assert window_words.window_in(sentence, today=TODAY) == AbsoluteRange(
        start=date(2026, 7, 1), end=date(2026, 7, 31)
    )


def test_a_month_of_the_year_before_is_that_month_and_not_the_whole_year() -> None:
    """ "El año pasado" on its own is a calendar year and is read as one. Beside a month it is the
    year that month belongs to, and answering with twelve months instead of one is the same class
    of mistake in the other direction."""
    assert window_words.window_in(
        "¿cuánto gasté en julio del año pasado?", today=TODAY
    ) == AbsoluteRange(start=date(2025, 7, 1), end=date(2025, 7, 31))


@pytest.mark.parametrize(
    "sentence",
    [
        # A date, not a month. The whole of June is thirty times the question.
        "¿cuánto gasté el 15 de junio?",
        # Two months is a range, and answering with either is a fraction of what was asked.
        "¿cuánto gasté de junio a septiembre?",
        "¿cuánto gasté del 1 de junio al 15 de julio?",
    ],
)
def test_a_sentence_this_is_not_sure_about_goes_to_the_model(sentence: str) -> None:
    """Precision over coverage, which is this file's whole rule: a miss costs one turn and a wrong
    guess costs a figure about the wrong days, presented as the answer."""
    assert window_words.window_in(sentence, today=TODAY) is None


def test_a_month_with_no_year_needs_to_know_what_year_it_is() -> None:
    """No clock is read here. Without `today` the phrase is unresolvable rather than resolved
    against a date this service invented — the rule that lets the evaluation replay a turn."""
    assert window_words.window_in("¿cuánto gasté en junio?") is None


@pytest.mark.parametrize("name", list(range(1, 13)))
def test_every_month_maps_to_its_own_number(name: int) -> None:
    """It was `enumerate` over a list with "setiembre" in the middle of it, skipped by an `if`, so
    every month after September was one too high: October answered November, and December was
    month 13 — a `ValueError` on the way to a 500."""
    spelled = [
        "enero",
        "febrero",
        "marzo",
        "abril",
        "mayo",
        "junio",
        "julio",
        "agosto",
        "septiembre",
        "octubre",
        "noviembre",
        "diciembre",
    ][name - 1]

    window = window_words.window_in(f"en {spelled} de 2025", today=TODAY)

    assert window == window_words.whole_month(2025, name)


def test_both_spellings_of_september_are_september() -> None:
    assert window_words.window_in("en setiembre de 2025", today=TODAY) == window_words.window_in(
        "en septiembre de 2025", today=TODAY
    )
