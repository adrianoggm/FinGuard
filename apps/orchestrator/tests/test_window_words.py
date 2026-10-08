"""Reading a window out of a sentence, without a model.

The clarification the planner asks for is almost always the window — "¿de qué periodo?" — and
a window is a closed vocabulary exactly like a category. So when somebody answers "este año",
there is nothing to infer and nothing to ask a model about.

This is what makes a clarification finishable. Before it, answering the question restarted the
whole conversation: the reply "este año" arrived as a two-word sentence with no context, and
the planner had to derive a complete plan from it.

Precision over coverage, and the exclusions are deliberate. "Hace un mes" and "en verano"
are windows a person understands and this does not, so they fall through to the model rather
than being guessed at — the cost of a miss is one more turn, and the cost of a wrong guess is
a figure about the wrong month. "Llevo gastado" and "últimamente" are read since 2026-10-07 as
product decisions (spec §3.5), each pinned at the end of this file against the windows and the
names they must not take the place of.
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
        # The count on the other side of the word, which Spanish puts there just as often. These
        # did not fail before — they fell through to the default and became *one*, so "los 10
        # últimos días" answered about yesterday with a real figure on it. Found by asserting the
        # query a corpus turn ran rather than only which tool answered it.
        ("los 2 últimos meses", Period.LAST_N_MONTHS, 2),
        ("en los 3 últimos meses", Period.LAST_N_MONTHS, 3),
        ("los 10 últimos días", Period.LAST_N_DAYS, 10),
        # Written out, either side, because that was the same silent one.
        ("los dos últimos meses", Period.LAST_N_MONTHS, 2),
        ("los últimos dos meses", Period.LAST_N_MONTHS, 2),
        ("los cinco últimos meses", Period.LAST_N_MONTHS, 5),
        # A plural with no number is a few, not one. «Los últimos meses» read as one month, and in
        # the live log that rule's LAST_N_MONTHS:1 overrode the model's LAST_N_MONTHS:3.
        ("los últimos meses", Period.LAST_N_MONTHS, 3),
        ("¿cuánto gasté en los últimos meses?", Period.LAST_N_MONTHS, 3),
        ("los últimos días", Period.LAST_N_DAYS, 7),
        # The singular with no number is still exactly one.
        ("el último mes", Period.LAST_N_MONTHS, 1),
        ("el último día", Period.LAST_N_DAYS, 1),
        ("el último año", Period.LAST_N_MONTHS, 12),
        # And a stated count wins over either default, on both sides of the word.
        ("los últimos 2 años", Period.LAST_N_MONTHS, 24),
        ("los 2 últimos días", Period.LAST_N_DAYS, 2),
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
        # «Últimamente» alone is about nothing this can date; about money it is the last 30 days
        # (see `test_lately_is_the_last_thirty_days_of_money_spent_or_received`).
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


@pytest.mark.parametrize(
    "sentence",
    [
        "¿cuánto gasté del mes pasado?",
        "los gastos del mes pasado",
        "de lo del mes pasado",
        # And the spelling that always worked, which is the point: the article is optional, not
        # forbidden.
        "el mes pasado",
        "pues del mes pasado",
    ],
)
def test_the_article_of_a_month_may_be_glued_to_its_preposition(sentence: str) -> None:
    """ "Del" is "de" and "el", and the phrase table only knew the second one spelled out.

    The entry was "el mes pasado" and it is matched between word boundaries, so "del mes pasado"
    matched nothing and a sentence that names its period outright was sent to a model to be asked
    which period it meant. The year never had the defect, because it is read as noun plus adjective
    by a rule that never learned the article.
    """
    assert window_words.window_in(sentence).period is Period.LAST_MONTH


def test_a_count_out_of_range_is_not_a_window() -> None:
    """The domain bounds `n`, so a sentence asking for 5000 days is not a window this can build.
    Returning None sends it to the model, which is better than raising from a reader."""
    assert window_words.window_in("los últimos 5000 días") is None


def test_the_singular_forms_are_read_too() -> None:
    """Somebody answering "el último mes" means one month, and "en el último día" means one day.
    Both are counted periods with n=1 rather than a separate name."""
    assert window_words.window_in("el último mes") == window_words.window_in("los últimos 1 meses")


def test_a_word_beside_the_last_that_is_not_a_number_is_not_a_count() -> None:
    """The article and the preposition sit exactly where a count would, and neither is one.

    This is what makes reading the count on both sides safe: "el último mes" and "de los últimos
    días" put a word there and state no count, so each means what it means without one — one month,
    and a few days — and an amount that happens to end up adjacent — "gasté 500 euros el último
    mes" — is separated from the word by the article it needs.

    "De los últimos días" was one day until the plural learnt to mean a few; that is the only
    expectation here the change moved, and it moved because one day was the wrong reading."""
    assert window_words.window_in("el último mes") == RelativePeriod(Period.LAST_N_MONTHS, n=1)
    assert window_words.window_in("de los últimos días") == RelativePeriod(Period.LAST_N_DAYS, n=7)
    assert window_words.window_in("gasté 500 euros el último mes") == RelativePeriod(
        Period.LAST_N_MONTHS, n=1
    )


def test_a_few_years_is_more_than_a_window_may_span_so_the_model_reads_it() -> None:
    """«Los últimos años» is a few years like the other plurals, and three years is past
    `MAX_PERIOD_MONTHS`. So this vocabulary declines it, exactly as it declines any count it cannot
    build — and it is *not* an impossible count the holder stated, which would have the planner
    refuse a sentence that named no number at all instead of letting the model read it."""
    assert window_words.window_in("los últimos años") is None
    assert not window_words.has_invalid_counted_window("¿cuánto gasté en los últimos años?")
    # A count the holder did state is still checked against the bound.
    assert window_words.has_invalid_counted_window("los últimos tres años")


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


@pytest.mark.parametrize(
    ("sentence", "expected"),
    [
        (
            "¿Cuánto gasté del 1 de junio al 15 de julio?",
            AbsoluteRange(date(2026, 6, 1), date(2026, 7, 15)),
        ),
        (
            "desde el 15 de diciembre hasta el 15 de enero",
            AbsoluteRange(date(2025, 12, 15), date(2026, 1, 15)),
        ),
        (
            "del 1 de marzo al 15 de abril de 2025",
            AbsoluteRange(date(2025, 3, 1), date(2025, 4, 15)),
        ),
    ],
)
def test_an_explicit_range_is_resolved_without_a_model(
    sentence: str, expected: AbsoluteRange
) -> None:
    assert window_words.date_range_in(sentence, today=TODAY) == expected
    assert window_words.window_in(sentence, today=TODAY) == expected


def test_a_future_or_impossible_explicit_range_is_not_executed() -> None:
    assert (
        window_words.date_range_in("del 1 de septiembre al 15 de octubre de 2026", today=TODAY)
        is None
    )
    assert (
        window_words.date_range_in("del 31 de febrero al 15 de marzo de 2026", today=TODAY) is None
    )


def test_two_arbitrary_ranges_are_read_in_mention_order() -> None:
    sentence = (
        "compara del 1 de marzo al 15 de abril de 2026 "
        "con el periodo del 1 de mayo al 15 de junio de 2026"
    )

    assert window_words.compared_date_ranges(sentence, today=TODAY) == (
        AbsoluteRange(date(2026, 3, 1), date(2026, 4, 15)),
        AbsoluteRange(date(2026, 5, 1), date(2026, 6, 15)),
    )


def test_the_same_explicit_period_last_year_is_derived_exactly() -> None:
    sentence = "compara del 1 de marzo al 15 de abril de 2026 con el mismo periodo del año pasado"

    assert window_words.compared_date_ranges(sentence, today=TODAY) == (
        AbsoluteRange(date(2026, 3, 1), date(2026, 4, 15)),
        AbsoluteRange(date(2025, 3, 1), date(2025, 4, 15)),
    )


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


@pytest.mark.parametrize(
    ("sentence", "first", "second"),
    [
        (
            "\u00bfGast\u00e9 m\u00e1s en julio que en junio?",
            window_words.whole_month(2026, 7),
            window_words.whole_month(2026, 6),
        ),
        (
            "\u00bfEn qu\u00e9 mes gast\u00e9 m\u00e1s, julio o junio?",
            window_words.whole_month(2026, 7),
            window_words.whole_month(2026, 6),
        ),
        (
            "Compara julio de 2025 con junio de 2026",
            window_words.whole_month(2025, 7),
            window_words.whole_month(2026, 6),
        ),
        (
            "Compara enero y diciembre",
            window_words.whole_month(2026, 1),
            window_words.whole_month(2025, 12),
        ),
        (
            "Compara el mes 7 frente al mes 6",
            window_words.whole_month(2026, 7),
            window_words.whole_month(2026, 6),
        ),
    ],
)
def test_two_compared_months_are_read_in_mention_order(
    sentence: str, first: AbsoluteRange, second: AbsoluteRange
) -> None:
    assert window_words.compared_calendar_months(sentence, today=TODAY) == (first, second)


@pytest.mark.parametrize(
    "sentence",
    [
        "compara los movimientos del 1 de junio con el 15 de julio",
        "compara mis gastos de junio a septiembre",
        "compara julio, junio y mayo",
    ],
)
def test_ambiguous_month_ranges_are_not_rewritten_as_two_whole_months(sentence: str) -> None:
    assert window_words.compared_calendar_months(sentence, today=TODAY) is None


@pytest.mark.parametrize(
    "sentence",
    ["Compara mis ingresos de 2025 con 2026", "Ingresos de 2025 vs 2026"],
)
def test_two_calendar_years_can_be_read_from_one_comparison(sentence: str) -> None:
    assert window_words.compared_calendar_years(sentence, today=TODAY) == (
        window_words.whole_year(2025),
        window_words.whole_year(2026),
    )


def test_a_single_window_is_complete_but_not_a_comparison_pair() -> None:
    assert window_words.has_complete_window("este mes", today=TODAY)
    assert not window_words.has_compared_windows("este mes", today=TODAY)


def test_two_compared_windows_are_complete_without_becoming_one_shareable_window() -> None:
    sentence = "compara junio y julio"

    assert window_words.has_complete_window(sentence, today=TODAY)
    assert window_words.has_compared_windows(sentence, today=TODAY)


# --- the two shapes the corpus stated plainly and this could not see -------------------
#
# Both are held-out cases the model resolved wrongly *while the sentence said the window
# outright*, and both survived because `window_in` answered None: `explicit_facts.enforce`
# overrides the model with what the sentence stated, but only for a window this file reads.
#
#     "¿Cuánto llevo desde el lunes?"            resolved as a rolling seven days
#     "¿Qué gasté entre el 10 y el 20 de mayo?"  resolved as the same days last year
#
# The corpus reference day. It is a Wednesday, so "desde el lunes" covers three days and a
# rolling week covers seven — the two readings cannot be confused for one another here.
REFERENCE = date(2026, 7, 15)


def test_since_monday_is_the_week_so_far() -> None:
    """A week starts on Monday here, so "desde el lunes" *is* THIS_WEEK — the same days, by
    definition, on whichever day of the week it is said.

    Only Monday. "Desde el martes" names a window the contract has no name for and whose start is
    ambiguous — this week's Tuesday if it has come round, last week's if it has not — and this
    file's answer to an ambiguity is None.
    """
    assert window_words.window_in("¿Cuánto llevo desde el lunes?", today=REFERENCE) == (
        RelativePeriod(period=Period.THIS_WEEK)
    )


def test_two_days_of_one_named_month_are_a_range() -> None:
    """ "Entre el 10 y el 20 de mayo" states both ends, and only the second carries the month —
    which is why the range reader, which wants a month on each end, never saw it.

    The year is the one every bare month in this file gets: the last time it happened, counting
    the one under way. Said in July 2026, "de mayo" is May 2026 and not May 2025.
    """
    assert window_words.window_in("¿Qué gasté entre el 10 y el 20 de mayo?", today=REFERENCE) == (
        AbsoluteRange(start=date(2026, 5, 10), end=date(2026, 5, 20))
    )


def test_an_explicit_year_on_a_shared_month_beats_the_one_this_would_have_guessed() -> None:
    """The reason the trailing year is read rather than ignored. Matching only the prefix of "entre
    el 10 y el 20 de mayo de 2025" would answer 2026 — a year the sentence had already denied, made
    authoritative by the very override this reader exists to feed."""
    assert window_words.window_in(
        "¿Qué gasté entre el 10 y el 20 de mayo de 2025?", today=REFERENCE
    ) == AbsoluteRange(start=date(2025, 5, 10), end=date(2025, 5, 20))


@pytest.mark.parametrize(
    "sentence",
    [
        # A weekday that is not the one the week starts on. "Desde el martes" has no name in the
        # contract and no unambiguous start, and this file's answer to an ambiguity is None.
        "¿Cuánto llevo desde el martes?",
        "¿Cuánto llevo desde el viernes?",
        # Still a Monday, but not this one, or not a window that closes today.
        "¿Cuánto gasté desde el lunes pasado?",
        "¿Cuánto gasté desde el lunes anterior?",
        "¿Cuánto gasté desde el lunes 13?",
        "¿Cuánto gasté desde el lunes 13 de julio?",
        "¿Cuánto gasté desde el lunes hasta el viernes?",
        "¿Cuánto gasté desde el lunes al viernes?",
        # A single day rather than everything since it.
        "¿Cuánto gasté el lunes?",
        # Two numbers and no calendar.
        "¿Cuánto gasté entre el 10 y el 20?",
        # Two months and no days: a third of the question either way round.
        "¿Cuánto gasté entre febrero y marzo?",
        # A month on each end, which is the other reader's shape and needs its opener.
        "¿Cuánto gasté entre el 30 de junio y el 1 de junio?",
        # The same shape said another way. Unmeasured, so still nobody's business here.
        "¿Cuánto gasté del 10 al 20 de mayo?",
        # Backwards, and swapping it silently would answer eleven days nobody asked about.
        "¿Cuánto gasté entre el 20 y el 10 de mayo?",
        # A day that does not exist.
        "¿Cuánto gasté entre el 10 y el 32 de mayo?",
        # One of each shape is two windows, and answering with either is answering neither.
        "¿Cuánto gasté entre el 1 y el 15 de junio y del 1 de marzo al 15 de abril?",
    ],
)
def test_neither_new_shape_generalises_past_what_was_measured(sentence: str) -> None:
    """The risk this change carries, written down as the thing that must keep failing.

    `window_in` is consulted on every path and `explicit_facts.enforce` makes whatever it returns
    beat the model, so a reader that sees a window the sentence did not state is worse than the
    blind one it replaced. Each of these is refused for a stated reason, not by accident.
    """
    assert window_words.window_in(sentence, today=REFERENCE) is None


@pytest.mark.parametrize(
    "sentence",
    ("los últimos 0 días", "los últimos 5000 días", "durante los últimos 100 meses"),
)
def test_an_explicit_count_outside_the_domain_is_distinguished_from_no_window(
    sentence: str,
) -> None:
    assert window_words.has_invalid_counted_window(sentence)


def test_the_valid_edge_is_not_marked_invalid() -> None:
    assert not window_words.has_invalid_counted_window("durante los últimos 366 días")


# --- B-4 (Wave 3 hardening): a season is a question with its months in it -----------------------
#
# `window_in` keeps refusing to guess a season -- see `test_anything_else_is_not_a_window` above,
# unchanged. What changes is what the refusal becomes: an honest ask naming the months a reading
# would take, with a suggestion chip per reading, rather than a silent fall-through that let the
# inherited window answer a different question (§11's own opening sentence).


def test_this_summer_asks_for_the_current_year_by_name() -> None:
    question, chips = window_words.season_ask(
        "¿En qué he gastado más este verano?", today=REFERENCE
    )

    assert question == "¿Te refieres a junio, julio y agosto de 2026?"
    # F-H2 (Wave 3 hardening, fix round): the chip is a range phrase the month reader can parse
    # back ("de junio a agosto de 2026"), not the comma list the question reads aloud -- `_A_MONTH`
    # only ever reads the *last* named month of a comma list, so a holder answering with the chip
    # exactly as offered used to get back a single, silently wrong month.
    assert chips == ("De junio a agosto de 2026",)


def test_last_summer_asks_for_the_previous_year_by_name() -> None:
    question, chips = window_words.season_ask("¿Y en el verano pasado?", today=REFERENCE)

    assert question == "¿Te refieres a junio, julio y agosto de 2025?"
    assert chips == ("De junio a agosto de 2025",)


def test_a_bare_season_with_no_qualifier_offers_both_years() -> None:
    question, chips = window_words.season_ask("¿Cuánto gasté en primavera?", today=REFERENCE)

    assert question == (
        "¿Te refieres a marzo, abril y mayo de 2026 o a marzo, abril y mayo de 2025?"
    )
    assert chips == ("De marzo a mayo de 2026", "De marzo a mayo de 2025")


@pytest.mark.parametrize("sentence", ["en verano", "otoño"])
def test_a_bare_season_word_alone_is_still_a_season_ask(sentence: str) -> None:
    assert window_words.season_ask(sentence, today=REFERENCE) is not None


@pytest.mark.parametrize(
    "sentence",
    ["¿cuánto gasté el mes pasado?", "el mes de agosto", "los últimos 3 meses"],
)
def test_a_sentence_with_no_season_is_not_a_season_ask(sentence: str) -> None:
    assert window_words.season_ask(sentence, today=REFERENCE) is None


# --- F-H2 (Wave 3 hardening, fix round): the season's own chip is a window `window_in` reads -----
#
# `season_ask` offers a chip a holder can answer with verbatim -- and until now nothing in this
# file could read it back: `_A_MONTH` matches only the last named month of a comma list, so
# "junio, julio y agosto de 2026" resolved to a single, silently wrong August. The chip is a range
# phrase instead ("de junio a agosto de 2026"), and this is the reader for exactly that shape.


def test_the_seasons_own_chip_is_a_window_it_can_read_back() -> None:
    window = window_words.window_in("De junio a agosto de 2026", today=REFERENCE)
    assert window == AbsoluteRange(start=date(2026, 6, 1), end=date(2026, 8, 31))


def test_the_range_reader_keeps_every_named_month_not_just_the_last() -> None:
    """The exact failure B-4 left open: a comma list drops every month but the last one, and a
    range phrase must not repeat that."""
    window = window_words.window_in("de marzo a mayo de 2025", today=REFERENCE)
    assert window == AbsoluteRange(start=date(2025, 3, 1), end=date(2025, 5, 31))


def test_a_winter_range_crosses_the_year_boundary() -> None:
    """ "de diciembre a febrero de 2026" names December 2026 through February *2027* -- the same
    anchor `_season_months` already uses for winter (the printed year is the first month's)."""
    window = window_words.window_in("de diciembre a febrero de 2026", today=date(2027, 3, 1))
    assert window == AbsoluteRange(start=date(2026, 12, 1), end=date(2027, 2, 28))


# --- two product defaults (spec §3.5, 2026-10-07) ----------------------------------------------
#
# Not vocabulary: decisions about what the product answers instead of asking, each stated in the
# answer («en lo que va de año», «en los últimos 30 días»). What they must never do is take the
# place of a window the sentence names, read or not, or read one out of a name.


@pytest.mark.parametrize(
    "sentence",
    [
        "¿Cuánto llevo gastado?",
        "¿Cuánto llevo gastado en Spotify?",
        "¿cuánta pasta llevo gastada?",
        "llevo gastados un montón en restaurantes, ¿cuánto?",
    ],
)
def test_llevo_gastado_with_no_window_is_the_year_so_far(sentence: str) -> None:
    assert window_words.window_in(sentence, today=REFERENCE) == RelativePeriod(Period.THIS_YEAR)


@pytest.mark.parametrize(
    ("sentence", "window"),
    [
        ("¿cuánto llevo gastado este mes?", RelativePeriod(Period.THIS_MONTH)),
        ("¿cuánto llevo gastado hoy?", RelativePeriod(Period.TODAY)),
        ("¿Cuánto llevo gastado desde el lunes?", RelativePeriod(Period.THIS_WEEK)),
        (
            "¿Cuánto llevo gastado en los últimos seis meses?",
            RelativePeriod(Period.LAST_N_MONTHS, 6),
        ),
        ("¿cuánto llevo gastado en julio?", AbsoluteRange(date(2026, 7, 1), date(2026, 7, 31))),
    ],
)
def test_llevo_gastado_with_a_window_is_that_window(sentence: str, window: object) -> None:
    assert window_words.window_in(sentence, today=REFERENCE) == window


@pytest.mark.parametrize(
    "sentence",
    [
        "¿Cuánto llevo gastado desde Navidad?",
        "¿Qué llevo gastado en la semana en curso?",
        "¿Cuánto llevo gastado desde enero?",
        "¿cuánto llevo gastado este verano?",
        "¿cuánto llevo gastado esta mañana?",
        "¿cuánto llevo gastado desde el martes?",
    ],
)
def test_llevo_gastado_beside_a_window_nothing_here_reads_is_not_the_year(sentence: str) -> None:
    """A window was named and this file cannot read it. The year so far in its place would be a
    different window presented as the one asked about, so the sentence goes to the model."""
    assert window_words.window_in(sentence, today=REFERENCE) is None


@pytest.mark.parametrize(
    "sentence",
    [
        "¿Cuánto he gastado últimamente en restaurantes?",
        "cuánto he gastado en el gimnasio últimamente",
        "¿qué he ingresado últimamente?",
        "¿cuánto me ha entrado últimamente?",
        "Últimamente he gastado mucho, ¿cuánto?",
    ],
)
def test_lately_is_the_last_thirty_days_of_money_spent_or_received(sentence: str) -> None:
    assert window_words.window_in(sentence, today=REFERENCE) == RelativePeriod(
        Period.LAST_N_DAYS, n=30
    )


@pytest.mark.parametrize(
    "sentence",
    [
        # Nothing about money: no question this could be the window of.
        "¿qué tal últimamente?",
        # A name, after a preposition, a quote or «concepto», or capitalised after another word.
        "¿cuánto gasté en Últimamente?",
        "¿cuánto gasté en «Últimamente»?",
        "¿cuánto gasté en el bar Últimamente?",
        "hazle un bizum a Marta con concepto últimamente",
    ],
)
def test_lately_inside_a_name_or_about_nothing_is_not_a_window(sentence: str) -> None:
    assert window_words.window_in(sentence, today=REFERENCE) is None


def test_a_window_the_sentence_states_wins_over_lately() -> None:
    assert window_words.window_in(
        "¿cuánto he gastado últimamente este mes?", today=REFERENCE
    ) == RelativePeriod(Period.THIS_MONTH)


@pytest.mark.parametrize(
    ("sentence", "window"),
    [
        ("¿Cuánto llevo gastado?", RelativePeriod(Period.THIS_YEAR)),
        ("¿Cuánto he gastado últimamente?", RelativePeriod(Period.LAST_N_DAYS, n=30)),
        # Stated, not a default: a window the sentence names is never the product's decision.
        ("¿cuánto llevo gastado este mes?", None),
        ("¿cuánto he gastado últimamente este mes?", None),
        ("¿cuánto gasté el mes pasado?", None),
        ("¿qué tal últimamente?", None),
    ],
)
def test_a_default_is_told_apart_from_a_window_the_sentence_states(
    sentence: str, window: RelativePeriod | None
) -> None:
    assert window_words.default_window_in(sentence, today=REFERENCE) == window


@pytest.mark.parametrize(
    "sentence",
    [
        "¿cuánto he gastado últimamente desde Navidad?",
        "¿cuánto he gastado últimamente hace un mes?",
        "¿cuánto he gastado últimamente en la semana en curso?",
        "¿cuánto he gastado últimamente, desde que empecé a trabajar?",
        "¿cuánto he gastado últimamente los fines de semana?",
        "¿cuánto he gastado últimamente en verano?",
        "¿cuánto he gastado últimamente por las mañanas?",
    ],
)
def test_lately_stands_aside_for_a_window_it_cannot_read(sentence: str) -> None:
    """Ruling R46 (I-2): beside a window the sentence names and this file does not read, the last
    30 days would be a different window presented as the one asked about — the rule «llevo
    gastado» already keeps."""
    assert window_words.window_in(sentence, today=REFERENCE) != RelativePeriod(
        Period.LAST_N_DAYS, n=30
    )


@pytest.mark.parametrize(
    "sentence",
    [
        "¿cuánto llevo gastado hasta ahora?",
        "¿cuánto llevo gastado hasta la fecha?",
        "¿cuánto llevo gastado hasta hoy?",
    ],
)
def test_llevo_gastado_up_to_now_is_still_the_year_so_far(sentence: str) -> None:
    """Ruling R46: «hasta ahora», «hasta la fecha», «hasta hoy» say what «llevo» already says — up
    to now — and name no window of their own. «Hasta hoy» was read as today."""
    assert window_words.window_in(sentence, today=REFERENCE) == RelativePeriod(Period.THIS_YEAR)
    assert window_words.default_window_in(sentence, today=REFERENCE) == RelativePeriod(
        Period.THIS_YEAR
    )


def test_today_on_its_own_is_still_today() -> None:
    assert window_words.window_in("¿cuánto llevo gastado hoy?", today=REFERENCE) == (
        RelativePeriod(Period.TODAY)
    )


@pytest.mark.parametrize(
    ("reply", "window"),
    [
        ("últimamente", RelativePeriod(Period.LAST_N_DAYS, n=30)),
        ("pues últimamente", RelativePeriod(Period.LAST_N_DAYS, n=30)),
        ("lo que llevo gastado", RelativePeriod(Period.THIS_YEAR)),
        # A reply that says anything more is not only a period.
        ("últimamente en gasolina", None),
        ("lo que llevo gastado en la hucha", None),
        ("lo que llevo gastado este mes", None),
        ("hoy", None),
        ("pues no sé", None),
    ],
)
def test_a_default_is_a_whole_reply_or_nothing(reply: str, window: RelativePeriod | None) -> None:
    assert window_words.default_reply_window(reply) == window
