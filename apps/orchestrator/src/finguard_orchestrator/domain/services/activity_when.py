"""When an activity is wanted: future days, time-of-day bands and clock times (spec §14 A2).

`window_words` reads the past and the current period, which is what a bank question asks about.
A class is booked for a day that has not happened yet, and «el jueves», «mañana por la tarde» and
«a las 19:00» exist nowhere in that reader. This one is its own, so the financial readers do not
move, and it reuses nothing from them.

The rules, all read on the folded sentence (`spanish.folded`, so accents are optional):

    day         «hoy» (starting now, never in the past), «mañana», «pasado mañana», «el <weekday>»
                -- the next date with that weekday **strictly after** today, because «hoy» is the
                word for today -- «D de <mes>[ de AAAA]», «esta semana», «(este) fin de semana»,
                «la semana que viene». No day at all is the next seven days from now.
    band        mañana 07-14, mediodía 12-16, tarde 15-21, noche 20-23; no band 06-23.
    hours       «a partir de / desde / después de las H» -> H to the band's end; «antes de / hasta
                las H» -> the band's start to H; «entre las H y las H2»; «a las H[:MM]» -> one hour.
                A bare hour under an afternoon or night band is read in it: «por la tarde a las 7»
                is 19:00.

A past period («ayer», «el mes pasado», «el jueves pasado», «hace dos días»), a date that does not
exist or has gone («el 45 de octubre»), and a period this reader does not serve («este mes») are
`None`: answering any of them with the next seven days would be a real list for a question nobody
asked. A window is single-day when its start and end share a date. Over several days it is one
interval, from the first day's opening to the last day's closing, and the daily hours travel beside
it as `band`: «el fin de semana por la tarde» is Saturday 15:00 to Sunday 21:00 with the band
15:00-21:00, so Saturday night is inside the interval and outside the request.
"""

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo
from typing import Final

from finguard_orchestrator.domain.services.spanish import folded


@dataclass(frozen=True)
class ActivityWindow:
    start: datetime
    end: datetime
    single_day: bool
    band: tuple[time, time] | None  # the daily hours asked for; None when none were stated


@dataclass(frozen=True)
class StatedTime:
    """A clock time as a text states it, with the UTC offset written right after it, if any: `Z` or
    `+hh(:mm)` after any time («17:00Z», «19:00:00+02:00»), and `-hh:mm` only after a date-time
    (`YYYY-MM-DD`, then `T` or a space, then the time) -- on its own «10:00-11:00» is a range, not
    10:00 at UTC-11."""

    at: time
    offset: timedelta | None


@dataclass(frozen=True)
class _Clock:
    at: time
    begins: int
    ends: int
    explicit: bool  # «de la tarde» or a 24-hour figure said which half of the day it is
    offset: timedelta | None = None


_DAY_START: Final = time(6, 0)
_DAY_END: Final = time(23, 0)
_HORIZON_DAYS: Final = 7

_BANDS: Final = {
    "manana": (time(7, 0), time(14, 0)),
    "mediodia": (time(12, 0), time(16, 0)),
    "tarde": (time(15, 0), time(21, 0)),
    "noche": (time(20, 0), time(23, 0)),
}

_WEEKDAYS: Final = ("lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo")
_MONTHS: Final = (
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
)

# One pattern for every way a clock time is written, so the verifier and the window read the same
# times. Four shapes, each guarded so it cannot fire inside another number:
#   …T19:00:00+02   the time of a date-time (`YYYY-MM-DD`, then `T` or a space), with its offset:
#                   `Z`, `+hh`, `+hh:mm`, and here also `-hh:mm` (`StatedTime`)
#   19:00 / 19.30   not after a digit, a colon, a sign or `\d.` / `\d/` (the «10.20» of a date
#                   «20.10.2026», the «19.00» of a phone «600.19.00.00», the «02:00» of an offset
#                   `+02:00`), not before `.d`, `:d` or `/d`, not before a currency (a price
#                   «12.50 €» is not 12:50); seconds («19:00:00», «19:45:30.000») belong to it, and
#                   so does a `Z` or `+hh(:mm)` right after it -- but never a `-hh:mm`, because
#                   «10:00-11:00» on its own is a range, not 10:00 at UTC-11
#   19h / 19h30     not inside a number, a decimal or a date
#   las 7 / la 1    a bare hour needs its article, and yields to the two shapes above
# then an optional «y media / y cuarto / menos cuarto», and «de la mañana/tarde/noche» or
# «del mediodía». A time right after a price word («precio: 19.00», «€19.00») is skipped too.
_CLOCK: Final = re.compile(
    r"""
    (?:
        (?<=\d{4}-\d{2}-\d{2}[t\ ])
        (?P<h0>\d{2}):(?P<m0>\d{2})(?::\d{2}(?:\.\d+)?)?
        (?P<offset>z(?![a-z])|[+-]\d{2}(?::?\d{2})?)?(?![\d:])
      |
        (?<![\d:+])(?<!\d[./])(?<!\d\d:\d\d:\d\d-)
        (?P<h1>\d{1,2})[:.](?P<m1>\d{2})(?::\d{2}(?:\.\d+)?)?(?!\d|[.:/]\d)(?!\s*(?:€|eur))
        (?P<offset1>(?:z(?![a-z])|\+\d{2}(?::?\d{2})?)(?![\d:]))?
      |
        (?<![\d:.,/])(?P<h2>\d{1,2})\s?h(?P<m2>\d{2})?\b
      |
        \blas?\s+(?P<h3>\d{1,2})(?!\d|[:./]\d|\s?h\b)
    )
    (?:\s+y\s+(?P<past>media|cuarto)|\s+menos\s+(?P<less>cuarto))?
    (?:\s+de\s+la\s+(?P<part>manana|tarde|noche)|\s+del\s+(?P<noon>mediodia))?
    """,
    re.VERBOSE,
)
_PRICE_BEFORE: Final = re.compile(r"(?:€|\beur(?:os)?|\bprecio)\s*:?\s*$")

_PAST: Final = re.compile(
    r"\b(?:ayer|anteayer|anoche)\b"
    r"|\bpasad[oa]s?\b(?!\s+manana)"
    r"|\bhace\s+(?:\d+|un[oa]?s?|dos|tres|cuatro|cinco|seis|siete|ocho|nueve|diez)\s+"
    r"(?:dias?|semanas?|mes(?:es)?|anos?|horas?)\b"
)
_UNSERVED: Final = re.compile(r"\b(?:mes|meses|ano|anos|trimestre|semestre|quincena)\b")

_BAND: Final = re.compile(
    r"\b(?:(?:por|en|a|de)\s+la|esta)\s+(?P<band>manana|tarde|noche)\b"
    r"|\b(?:(?:al|a)\s+)?(?P<noon>mediodia)\b"
)
# Not «esta mañana», which is this morning: `_TODAY` reads it, and its band is already known.
_TOMORROW: Final = re.compile(r"(?<!esta )\b(?P<after>pasado\s+)?manana\b")
_TODAY: Final = re.compile(r"\bhoy\b|\besta\s+(?:manana|tarde|noche)\b")
_WEEKDAY: Final = re.compile(r"\b(?P<day>" + "|".join(_WEEKDAYS) + r")s?\b")
_DATE: Final = re.compile(
    r"\b(?P<day>\d{1,2})\s+de\s+(?P<month>" + "|".join(_MONTHS) + r"|setiembre)"
    r"(?:\s+(?:de\s+|del\s+)?(?P<year>\d{4}))?\b"
)
_NEXT_WEEK: Final = re.compile(
    r"\bsemana\s+(?:que\s+viene|proxima|siguiente)\b|\bproxima\s+semana\b"
)
_WEEKEND: Final = re.compile(r"\bfin(?:es)?\s+de\s+semana\b|\bfinde\b")
_THIS_WEEK: Final = re.compile(r"\besta\s+semana\b")

_FROM: Final = re.compile(r"(?:a\s+partir\s+de|desde|despues\s+de)\s+(?:las?\s+)?$")
_UNTIL: Final = re.compile(r"(?:antes\s+de|hasta)\s+(?:las?\s+)?$")
_BETWEEN: Final = re.compile(r"entre\s+(?:las?\s+)?$")
_AND: Final = re.compile(r"\by\s+(?:las?\s+)?$")


def clock_times_in(text: str) -> tuple[time, ...]:
    """Every clock time `text` states, in order, each once. An hour past 23 or a minute past 59 is
    not a time and is skipped."""
    return tuple(dict.fromkeys(stated.at for stated in stated_times(text)))


def stated_times(text: str) -> tuple[StatedTime, ...]:
    """`clock_times_in` with the offset each time was written with, each once, in order."""
    found = (StatedTime(clock.at, clock.offset) for clock in _clocks(folded(text)))
    return tuple(dict.fromkeys(found))


def activity_window(sentence: str, *, now: datetime) -> ActivityWindow | None:
    """The window a sentence asks an activity for, in `now`'s zone, or `None` (module docstring)."""
    text = " ".join(folded(sentence).split())
    if _PAST.search(text) or _UNSERVED.search(text):
        return None
    clocks = _clocks(text)
    # Clock phrases first: «a las 7 de la tarde» names an hour, not the afternoon band.
    unclocked = _blank(text, [(c.begins, c.ends) for c in clocks])
    band, unbanded = _band_in(unclocked)
    days = _days(unbanded, now.date())
    if days is None:
        return None
    first, last = days
    opens, closes = _hours(text, clocks, band)
    if closes <= opens:
        return None  # «entre las 20 y las 18»
    start = max(_at(first, opens, now.tzinfo), now)
    end = _at(last, closes, now.tzinfo)
    if start >= end:
        return None  # the whole window has already gone
    # The daily hours, when the sentence stated any: a band or a clock phrase. Not when they run
    # past midnight («hoy a las 23:30»), which no pair of times within a day can say.
    stated = band is not None or bool(clocks)
    daily = (_clock(opens), _clock(closes)) if stated and closes < timedelta(days=1) else None
    return ActivityWindow(start, end, start.date() == end.date(), daily)


def _clock(since_midnight: timedelta) -> time:
    minutes = int(since_midnight.total_seconds()) // 60
    return time(minutes // 60, minutes % 60)


def _at(day: date, since_midnight: timedelta, zone: tzinfo | None) -> datetime:
    """Wall-clock time on `day`; one hour after 23:30 is 00:30 of the next day."""
    return datetime.combine(day, time(0, 0), zone) + since_midnight


def _since_midnight(at: time) -> timedelta:
    return timedelta(hours=at.hour, minutes=at.minute)


def _clocks(text: str) -> list[_Clock]:
    found: list[_Clock] = []
    for match in _CLOCK.finditer(text):
        if _PRICE_BEFORE.search(text[max(0, match.start() - 16) : match.start()]):
            continue
        hour = int(match["h0"] or match["h1"] or match["h2"] or match["h3"])
        minute = int(match["m0"] or match["m1"] or match["m2"] or 0)
        written = match["offset"] or match["offset1"]
        offset = _offset(written) if written else None
        if written and offset is None:
            continue  # an offset no zone has: the time it qualifies is unreadable, not local
        if match["past"]:
            minute += 30 if match["past"] == "media" else 15
        if match["less"]:
            hour, minute = hour - 1, minute + 45
        part = match["part"] or ("mediodia" if match["noon"] else None)
        if part in ("tarde", "noche") and 1 <= hour < 12:
            hour += 12
        elif part == "noche" and hour == 12:
            hour = 0
        elif part == "mediodia" and 1 <= hour < 4:
            hour += 12
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            continue
        explicit = part is not None or hour >= 13 or hour == 0
        found.append(_Clock(time(hour, minute), match.start(), match.end(), explicit, offset))
    return found


def _offset(written: str) -> timedelta | None:
    """`z`, `+02`, `+0200`, `-05:30` as a UTC offset; `None` past the ±14:00 any zone uses."""
    if written == "z":
        return timedelta(0)
    digits = written[1:].replace(":", "")
    hours, minutes = int(digits[:2]), int(digits[2:] or 0)
    if hours > 14 or minutes > 59:
        return None
    sign = -1 if written[0] == "-" else 1
    return sign * timedelta(hours=hours, minutes=minutes)


def _blank(text: str, spans: list[tuple[int, int]]) -> str:
    for begins, ends in spans:
        text = text[:begins] + " " * (ends - begins) + text[ends:]
    return text


def _band_in(text: str) -> tuple[str | None, str]:
    """The band named, and the text with every band phrase blanked -- so the «mañana» of «por la
    mañana» is not also read as tomorrow. «esta mañana» keeps its «esta» for `_TODAY`."""
    band: str | None = None
    spans: list[tuple[int, int]] = []
    for match in _BAND.finditer(text):
        band = band or match["band"] or match["noon"]
        if match["band"] and match.group(0).startswith("esta"):
            continue  # «esta tarde» is also a day, read below
        spans.append((match.start(), match.end()))
    return band, _blank(text, spans)


def _days(text: str, today: date) -> tuple[date, date] | None:
    """The first and last day asked, or `None` for a date that does not exist or has gone."""
    named: list[date] = []
    if _TODAY.search(text):
        named.append(today)
    for match in _TOMORROW.finditer(text):
        named.append(today + timedelta(days=2 if match["after"] else 1))
    for match in _WEEKDAY.finditer(text):
        wanted = _WEEKDAYS.index(match["day"])
        named.append(today + timedelta(days=(wanted - today.weekday() - 1) % 7 + 1))
    for match in _DATE.finditer(text):
        month = _MONTHS.index(match["month"]) + 1 if match["month"] in _MONTHS else 9
        try:
            day = date(int(match["year"] or today.year), month, int(match["day"]))
        except ValueError:
            return None
        if day < today:
            return None
        named.append(day)
    if named:
        return min(named), max(named)
    # The weekend before next week: «el fin de semana que viene» is the coming Saturday.
    if _WEEKEND.search(text):
        saturday = today + timedelta(days=max(5 - today.weekday(), 0))
        return saturday, today + timedelta(days=6 - today.weekday())
    if _NEXT_WEEK.search(text):
        monday = today + timedelta(days=7 - today.weekday())
        return monday, monday + timedelta(days=6)
    if _THIS_WEEK.search(text):
        return today, today + timedelta(days=6 - today.weekday())
    return today, today + timedelta(days=_HORIZON_DAYS)


def _hours(text: str, clocks: list[_Clock], band: str | None) -> tuple[timedelta, timedelta]:
    """The day's opening and closing hour, from the band and the clock phrases."""
    opens, closes = _BANDS[band] if band is not None else (_DAY_START, _DAY_END)

    def read(clock: _Clock) -> timedelta:
        # «por la tarde a las 7» is 19:00: a bare hour is read in the band's half of the day.
        shift = not clock.explicit and band in ("tarde", "noche") and clock.at.hour < 12
        return _since_midnight(clock.at) + timedelta(hours=12 if shift else 0)

    lead = [(text[: c.begins], read(c)) for c in clocks]
    for index, (before, at) in enumerate(lead):
        if _BETWEEN.search(before) and index + 1 < len(lead) and _AND.search(lead[index + 1][0]):
            return at, lead[index + 1][1]
    starts = [at for before, at in lead if _FROM.search(before)]
    ends = [at for before, at in lead if _UNTIL.search(before)]
    if starts or ends:
        return (
            starts[0] if starts else _since_midnight(opens),
            ends[0] if ends else _since_midnight(closes),
        )
    if lead:
        return lead[0][1], lead[0][1] + timedelta(hours=1)
    return _since_midnight(opens), _since_midnight(closes)
