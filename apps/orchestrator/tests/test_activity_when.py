"""Future days, time-of-day bands and clock times, read for an activity (spec §14 A2).

`window_words` reads the past and the current period; «el jueves», «mañana por la tarde» and «a
las 19:00» exist nowhere in it. This reader is its own so the financial readers do not move.
"""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from finguard_orchestrator.domain.services.activity_when import (
    StatedTime,
    activity_window,
    clock_times_in,
    stated_times,
)

# A Wednesday, mid-morning, in the holder's zone.
NOW = datetime(2026, 9, 30, 10, 0, tzinfo=ZoneInfo("Europe/Madrid"))


@pytest.mark.parametrize(
    ("sentence", "day", "start", "end"),
    [
        ("¿qué clases hay hoy?", date(2026, 9, 30), time(10, 0), time(23, 0)),  # today starts now
        ("clases de yoga mañana", date(2026, 10, 1), time(6, 0), time(23, 0)),
        ("pasado mañana por la tarde", date(2026, 10, 2), time(15, 0), time(21, 0)),
        ("el jueves por la tarde", date(2026, 10, 1), time(15, 0), time(21, 0)),
        ("el miércoles", date(2026, 10, 7), time(6, 0), time(23, 0)),  # today is Wednesday
        ("el viernes por la mañana", date(2026, 10, 2), time(7, 0), time(14, 0)),
        ("el sábado por la noche", date(2026, 10, 3), time(20, 0), time(23, 0)),
        ("mañana a partir de las 18", date(2026, 10, 1), time(18, 0), time(23, 0)),
        ("mañana a las 19:00", date(2026, 10, 1), time(19, 0), time(20, 0)),
    ],
)
def test_a_named_day_and_band(sentence: str, day: date, start: time, end: time) -> None:
    window = activity_window(sentence, now=NOW)
    assert window is not None and window.single_day
    assert (window.start, window.end) == (
        datetime.combine(day, start, NOW.tzinfo),
        datetime.combine(day, end, NOW.tzinfo),
    )


def test_no_day_means_the_next_seven_days_from_now() -> None:
    w = activity_window("¿hay clases de spinning?", now=NOW)
    assert w is not None and not w.single_day
    assert (w.start, w.end) == (NOW, datetime(2026, 10, 7, 23, 0, tzinfo=NOW.tzinfo))


def test_esta_semana_and_el_fin_de_semana() -> None:
    weekend = activity_window("este fin de semana", now=NOW)
    week = activity_window("esta semana", now=NOW)
    assert weekend is not None and weekend.start.date() == date(2026, 10, 3)
    assert week is not None and week.end.date() == date(2026, 10, 4)


def test_a_past_or_unreadable_period_is_none() -> None:
    for s in ("ayer", "el mes pasado", "el 45 de octubre"):
        assert activity_window(s, now=NOW) is None


@pytest.mark.parametrize(
    ("text", "times"),
    [
        ("a las 19:00", (time(19, 0),)),
        ("19h", (time(19, 0),)),
        ("las 19.30", (time(19, 30),)),
        ("a las 7 de la tarde", (time(19, 0),)),
        ("a las 9 de la mañana", (time(9, 0),)),
        ("18:30 y 20:00", (time(18, 30), time(20, 0))),
        ("sin hora", ()),
        ("25:00", ()),
    ],
)
def test_clock_times(text: str, times: tuple[time, ...]) -> None:
    assert clock_times_in(text) == times


# --- beyond the brief's table: the same rules, said the other ways people say them -------------


def test_a_weekday_reads_without_its_accent_and_the_band_without_its_article() -> None:
    """Nobody types accents into a chat box (`spanish.py`): «el miercoles» is «el miércoles»."""
    typed = activity_window("el miercoles", now=NOW)
    said = activity_window("el miércoles", now=NOW)
    assert typed is not None and typed == said
    saturday = activity_window("el sabado por la manana", now=NOW)
    assert saturday is not None
    assert saturday.start == datetime(2026, 10, 3, 7, 0, tzinfo=NOW.tzinfo)


def test_this_afternoon_is_today_and_a_past_band_today_is_none() -> None:
    afternoon = activity_window("¿hay spinning esta tarde?", now=NOW)
    assert afternoon is not None
    assert afternoon.start == datetime(2026, 9, 30, 15, 0, tzinfo=NOW.tzinfo)
    late = NOW.replace(hour=16)
    assert activity_window("hoy por la mañana", now=late) is None


def test_a_range_of_hours_and_an_hour_read_inside_the_band() -> None:
    between = activity_window("el jueves entre las 18:00 y las 20:30", now=NOW)
    assert between is not None
    assert (between.start.time(), between.end.time()) == (time(18, 0), time(20, 30))
    evening = activity_window("el jueves por la tarde a las 7", now=NOW)
    assert evening is not None
    assert (evening.start.time(), evening.end.time()) == (time(19, 0), time(20, 0))


def test_next_week_and_a_named_date() -> None:
    week = activity_window("la semana que viene", now=NOW)
    assert week is not None and not week.single_day
    assert (week.start, week.end) == (
        datetime(2026, 10, 5, 6, 0, tzinfo=NOW.tzinfo),
        datetime(2026, 10, 11, 23, 0, tzinfo=NOW.tzinfo),
    )
    named = activity_window("el 2 de octubre por la tarde", now=NOW)
    assert named is not None and named.start == datetime(2026, 10, 2, 15, 0, tzinfo=NOW.tzinfo)


def test_a_date_already_gone_and_a_period_this_reader_does_not_serve_are_none() -> None:
    """A month is not a window this reader serves: answering it with the next seven days would be
    a real list for a question nobody asked."""
    for s in ("el 3 de septiembre", "el jueves pasado", "este mes", "hace dos días"):
        assert activity_window(s, now=NOW) is None


def test_clock_times_read_quarters_and_skip_offsets_and_prices() -> None:
    assert clock_times_in("a las 7 y media de la tarde") == (time(19, 30),)
    assert clock_times_in("2026-10-01T19:00:00+02:00") == (time(19, 0),)
    assert clock_times_in("cuesta 12.50 €") == ()


@pytest.mark.parametrize(
    "text",
    [
        "20.10.2026",
        "precio: 19.00",
        "€19.00",
        "19.00 euros",
        "EUR 19.00",
        "600.19.00.00",
        "01/10/2026",
    ],
)
def test_a_date_a_price_or_a_phone_number_states_no_clock_time(text: str) -> None:
    """Fragments of other numbers are not times (review finding 3): «20.10.2026» is not 20:10."""
    assert clock_times_in(text) == ()


def test_seconds_belong_to_the_time_they_follow() -> None:
    assert clock_times_in("inicio 19:00:00, fin 19:45:30.000") == (time(19, 0), time(19, 45))


def test_an_iso_offset_is_read_beside_its_time_and_a_range_is_not_an_offset() -> None:
    """Review finding 4: «T17:00:00Z» is 17:00 *at UTC*, and dropping the Z is reading 19:00 in
    Madrid as 17:00. An offset is read only where ISO 8601 writes one, on a date-time: «10:00-11:00»
    is a range, not 10:00 at UTC-11."""
    assert stated_times("2026-10-01T17:00:00Z") == (StatedTime(time(17, 0), timedelta(0)),)
    assert stated_times("2026-10-01T19:00:00+02:00") == (
        StatedTime(time(19, 0), timedelta(hours=2)),
    )
    assert stated_times("2026-10-01T19:00-0530") == (
        StatedTime(time(19, 0), -timedelta(hours=5, minutes=30)),
    )
    assert stated_times("de 10:00-11:00") == (
        StatedTime(time(10, 0), None),
        StatedTime(time(11, 0), None),
    )
    assert clock_times_in("2026-10-01T17:00:00Z") == (time(17, 0),)


def test_the_window_carries_the_daily_hours_it_was_asked_for() -> None:
    """Review finding 9 (ruling R9): over several days a band is not the hull. «El fin de semana
    por la tarde» is 15:00-21:00 on Saturday *and* on Sunday, not Saturday 15:00 to Sunday 21:00."""
    weekend = activity_window("el fin de semana por la tarde", now=NOW)
    assert weekend is not None and weekend.band == (time(15, 0), time(21, 0))
    evenings = activity_window("esta semana a partir de las 18", now=NOW)
    assert evenings is not None and evenings.band == (time(18, 0), time(23, 0))
    assert activity_window("¿hay clases de spinning?", now=NOW).band is None  # type: ignore[union-attr]
    # One hour after 23:30 is tomorrow: no daily band can say that, and the window itself does.
    assert activity_window("hoy a las 23:30", now=NOW).band is None  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("text", "offset"),
    [
        ("2026-10-01 17:00:00+00:00", timedelta(0)),
        ("a las 17:00Z", timedelta(0)),
        ("17:00+00:00", timedelta(0)),
        ("2026-10-01 17:00Z", timedelta(0)),
        ("2026-10-01T17:00:00Z", timedelta(0)),
        ("2026-10-01 19:00:00+02:00", timedelta(hours=2)),
        ("19:00+02:00", timedelta(hours=2)),
        ("19:00:00+0200", timedelta(hours=2)),
        ("2026-10-01 19:00-05:00", -timedelta(hours=5)),
    ],
)
def test_an_offset_is_read_after_any_time_and_a_minus_only_after_a_date_time(
    text: str, offset: timedelta
) -> None:
    """Round-2 finding A: `Z` or `+hh(:mm)` right after a clock time is always its offset; `-hh:mm`
    is one only after a date-time (`YYYY-MM-DD`, then `T` or a space, then the time), because on
    its own «10:00-11:00» is a range."""
    [stated] = stated_times(text)
    assert stated.offset == offset


def test_a_range_is_two_local_times_not_a_time_at_utc_minus_eleven() -> None:
    assert stated_times("Spinning de 10:00-11:00") == (
        StatedTime(time(10, 0), None),
        StatedTime(time(11, 0), None),
    )
