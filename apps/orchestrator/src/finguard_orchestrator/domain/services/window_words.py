"""Reading a window out of a sentence, without a model.

The clarification the planner asks for is almost always the window — "¿de qué periodo?" — and
a window is a closed vocabulary exactly like a category. When somebody answers "este año"
there is nothing to infer, so there is nothing to ask a model about.

This is what makes a clarification finishable. Before it, answering the question restarted
the conversation: the reply arrived as a two-word sentence with no context and a complete
plan had to be derived from it, which is why "este año" came back asking for the period
again.

**Precision over coverage.** "Últimamente", "hace un mes" and "en verano" are windows a
person understands and this does not, and they are left out on purpose: a miss falls through
to the model and costs one turn, while a wrong guess costs a figure about the wrong month
presented as an answer.

Years used to be absent, and that was the reasoning: the banking contract has no LAST_YEAR, so
there was no *period* to translate them into, and answering "el año pasado" as THIS_YEAR would
be a figure about a different year. The conclusion did not follow. A plan carries either a named
period or an explicit range, and a calendar year is an explicit range — "el año pasado" is
2025-01-01..2025-12-31 and nothing about it needs a name in the enum. Leaving it out cost three
turns of a clarification loop in the reported conversation, because the assistant kept asking
"¿de qué periodo?" about a period the user had already written twice.

Longest phrase first, because "el mes pasado" contains "mes" and "este mes" does too.
Whichever phrase is longer is the one the person actually said.
"""

import re
from calendar import monthrange
from collections.abc import Callable
from datetime import date
from typing import Final

from finguard_orchestrator.domain.enums import Period
from finguard_orchestrator.domain.errors import InvalidValueError
from finguard_orchestrator.domain.models.conversation import Slot
from finguard_orchestrator.domain.services import spanish
from finguard_orchestrator.domain.value_objects.financial_plan import AbsoluteRange, RelativePeriod

# A window this vocabulary is sure about, whichever of the two shapes it takes.
type Window = RelativePeriod | AbsoluteRange

# Phrases that name a window on their own, longest first. Folded, so accents and case do not matter.
_NAMED: Final[tuple[tuple[str, Period], ...]] = (
    ("en lo que llevamos de ano", Period.THIS_YEAR),
    ("en lo que va de ano", Period.THIS_YEAR),
    ("en lo que llevamos de mes", Period.THIS_MONTH),
    ("en lo que va de mes", Period.THIS_MONTH),
    ("la semana pasada", Period.LAST_WEEK),
    ("el mes pasado", Period.LAST_MONTH),
    ("esta semana", Period.THIS_WEEK),
    ("este mes", Period.THIS_MONTH),
    ("este ano", Period.THIS_YEAR),
    # How people actually answered "¿de qué periodo?" in the reported conversation: "del año". It
    # was not read, so the turn went to the model, which asked "¿de qué año?", and the next reply
    # was not read either. Two turns of a loop over a window this vocabulary has.
    ("del ano", Period.THIS_YEAR),
    ("el ano", Period.THIS_YEAR),
    ("ayer", Period.YESTERDAY),
    ("hoy", Period.TODAY),
)

# "El año pasado" ends in "el ano", which the table above reads as *this* year. It is matched first
# and answered as a calendar range, so the phrase can never fall through to the wrong one.
_THE_YEAR_BEFORE: Final = re.compile(r"\bano\s+(pasado|anterior)\b")

# Spelled out rather than counted off a list. It was `enumerate(_MONTHS)` with "setiembre" — the
# way half of Spain writes September — sitting in the middle of it and skipped by an `if`, so
# everything after it was one too high: October answered November, and December was month 13 and
# raised `ValueError: month must be in 1..12` on the way to a 500. A position in a list is not a
# month number, and writing the number down is the only version of this that cannot drift again.
_MONTH_NUMBER: Final[dict[str, int]] = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}
_MONTHS: Final[tuple[str, ...]] = tuple(_MONTH_NUMBER)

# "julio de 2026", "en julio del 2026", "julio de este año", "julio del año pasado". A month is the
# window somebody means when they name one, and it has to be read *before* the year — "julio de
# 2026" contains "de 2026", so a year rule that ran first would answer one month with twelve. Same
# for "julio del año pasado", which "el año pasado" would otherwise widen to the whole of it.
_A_MONTH: Final = re.compile(
    rf"\b(?P<month>{'|'.join(_MONTHS)})\s+(?:de|del)\s+(?:el\s+)?"
    rf"(?:(?:ano\s+)?(?P<year>20\d{{2}})|(?P<relative>este\s+ano|ano\s+(?:pasado|anterior)))\b"
)

# A month with no year at all: "en junio", "durante marzo", "¿cuánto gasté en octubre?".
#
# A preposition is required. This reader's answer *overrides* the model's plan, so a false positive
# is a real figure about a month nobody asked for — and a bare "junio" in the middle of a sentence
# is as likely to be part of a name as a window.
_A_BARE_MONTH: Final = re.compile(
    rf"(?<![a-z0-9])(?:en|de|del|durante)\s+(?P<month>{'|'.join(_MONTHS)})(?![a-z0-9])"
)

# A common short answer to "which period?": "el mes 7". Requiring the word `mes` keeps account
# numbers, amounts and days out of this rule. When two alternatives follow ("mes 7 y si no el 8")
# the first one is the requested first attempt, so this deliberately reads that one.
_A_NUMBERED_MONTH: Final = re.compile(
    r"\b(?:el\s+)?mes\s+(?:numero\s+)?(?P<month>0?[1-9]|1[0-2])\b"
)

# "el 15 de junio" is a day, not a month, and answering it with the whole of June is thirty times
# the question. A range — "del 1 de junio al 15 de julio" — trips this too, which is the point.
_A_DAY_OF_A_MONTH: Final = re.compile(rf"\d{{1,2}}\s+de\s+(?:{'|'.join(_MONTHS)})(?![a-z0-9])")

# A bounded explicit range: "del 1 de marzo al 15 de abril de 2026" or "desde el 1 de
# marzo hasta el 15 de abril". Each endpoint has its own named groups because two captures with
# the same name cannot coexist in Python's regular-expression engine.
_DATE_RANGE: Final = re.compile(
    rf"\b(?:del|desde(?:\s+el)?)\s+"
    rf"(?P<start_day>\d{{1,2}})\s+de\s+(?P<start_month>{'|'.join(_MONTHS)})"
    r"(?:\s+(?:de|del)\s+(?:el\s+)?(?:ano\s+)?(?P<start_year>20\d{2}))?"
    r"\s+(?:al|hasta(?:\s+el)?)\s+"
    rf"(?P<end_day>\d{{1,2}})\s+de\s+(?P<end_month>{'|'.join(_MONTHS)})"
    r"(?:\s+(?:de|del)\s+(?:el\s+)?(?:ano\s+)?(?P<end_year>20\d{2}))?\b"
)
_SAME_RANGE_LAST_YEAR: Final = re.compile(
    r"\b(?:con|contra|frente\s+a(?:l)?|versus|vs)\s+"
    r"(?:el\s+)?mismo\s+periodo\s+(?:del|el)\s+ano\s+(?:pasado|anterior)\b"
)

# Two calendar labels joined as alternatives, used only by the direct-comparison path. Unlike the
# ordinary one-window reader, this accepts bare month names because the connector between exactly
# two of them ("julio con junio", "julio que en junio") supplies the missing structure.
_COMPARISON_CONNECTOR: Final = re.compile(r"\b(?:que|con|contra|versus|vs|y|o|frente\s+a(?:l)?)\b")
_MONTH_MENTION: Final = re.compile(
    rf"\b(?:(?P<month>{'|'.join(_MONTHS)})|(?:el\s+)?mes\s+"
    r"(?P<number>0?[1-9]|1[0-2]))\b"
    r"(?:\s+(?:de|del)\s+(?:el\s+)?(?:(?:ano\s+)?(?P<year>20\d{2})"
    r"|(?P<relative>este\s+ano|ano\s+(?:pasado|anterior))))?"
)
_YEAR_MENTION: Final = re.compile(
    r"\b(?:(?:el\s+)?ano\s+(?P<relative>este|pasado|anterior)"
    r"|(?:(?:el\s+)?ano\s+)?(?P<year>20\d{2}))\b"
)

_QUARTER_MENTION: Final = re.compile(
    r"\b(?:el\s+)?(?P<position>primer(?:o)?|segundo|tercer(?:o)?|cuarto|[1-4](?:er|o)?)\s+"
    r"trimestre(?:\s+(?:de|del)\s+(?:el\s+)?(?:(?:ano\s+)?(?P<year>20\d{2})"
    r"|(?P<relative>este\s+ano|ano\s+(?:pasado|anterior))))?\b"
)
_SEMESTER_MENTION: Final = re.compile(
    r"\b(?:el\s+)?(?P<position>primer(?:o)?|segundo|[12](?:er|o)?)\s+"
    r"semestre(?:\s+(?:de|del)\s+(?:el\s+)?(?:(?:ano\s+)?(?P<year>20\d{2})"
    r"|(?P<relative>este\s+ano|ano\s+(?:pasado|anterior))))?\b"
)
_FORTNIGHT_MENTION: Final = re.compile(
    rf"\b(?:la\s+)?(?P<position>primera|segunda|[12]a?)\s+quincena\s+(?:de|del)\s+"
    rf"(?P<month>{'|'.join(_MONTHS)})"
    r"(?:\s+(?:de|del)\s+(?:el\s+)?(?:(?:ano\s+)?(?P<year>20\d{2})"
    r"|(?P<relative>este\s+ano|ano\s+(?:pasado|anterior))))?\b"
)

_THIS_QUARTER: Final = re.compile(
    r"\b(?:este\s+trimestre|trimestre\s+actual|en\s+lo\s+que\s+va\s+de\s+trimestre)\b"
)
_PREVIOUS_QUARTER: Final = re.compile(r"\b(?:el\s+)?trimestre\s+(?:pasado|anterior)\b")
_LAST_QUARTER: Final = re.compile(r"\b(?:el\s+)?ultimo\s+trimestre\b")
_THIS_SEMESTER: Final = re.compile(
    r"\b(?:este\s+semestre|semestre\s+actual|en\s+lo\s+que\s+va\s+de\s+semestre)\b"
)
_PREVIOUS_SEMESTER: Final = re.compile(r"\b(?:el\s+)?semestre\s+(?:pasado|anterior)\b")
_LAST_SEMESTER: Final = re.compile(r"\b(?:el\s+)?ultimo\s+semestre\b")
_THIS_FORTNIGHT: Final = re.compile(r"\b(?:esta\s+quincena|quincena\s+actual)\b")
_PREVIOUS_FORTNIGHT: Final = re.compile(r"\b(?:la\s+)?quincena\s+(?:pasada|anterior)\b")
_THIS_WEEKEND: Final = re.compile(r"\b(?:este\s+fin\s+de\s+semana|fin\s+de\s+semana\s+actual)\b")
_PREVIOUS_WEEKEND: Final = re.compile(
    r"\b(?:(?:el\s+)?fin\s+de\s+semana\s+(?:pasado|anterior)"
    r"|(?:el\s+)?ultimo\s+fin\s+de\s+semana)\b"
)

_BLOCK_POSITION: Final[dict[str, int]] = {
    "primer": 1,
    "primero": 1,
    "primera": 1,
    "1": 1,
    "1a": 1,
    "1er": 1,
    "1o": 1,
    "segundo": 2,
    "segunda": 2,
    "2": 2,
    "2a": 2,
    "2o": 2,
    "tercer": 3,
    "tercero": 3,
    "3": 3,
    "3er": 3,
    "3o": 3,
    "cuarto": 4,
    "4": 4,
    "4o": 4,
}

# A year named outright: "en 2025", "durante 2025", "del 2025", "todo el año 2025". Either a
# preposition or the word "año" has to be there — a bare four-digit number is an amount as often as
# it is a year, and "gasté 2025 euros" must not become a window.
_A_NAMED_YEAR: Final = re.compile(
    r"\b(?:en|durante|del|de|desde)\s+(?:todo\s+)?(?:el\s+)?(?:ano\s+)?(20\d{2})\b"
    r"|\b(?:todo\s+)?(?:el\s+)?ano\s+(20\d{2})\b"
)

# A reply that points at whatever was just asked about and names nothing else. Meaningless in
# isolation — which is why it is only read when a slot says what the question was.
_THIS_ONE: Final = re.compile(r"(de\s+)?(este|esta|el\s+actual|la\s+actual|actual)")

# "los últimos 30 días", "últimos 3 meses", "el último año", and the singular "el último mes"
# which is n=1. Years are counted in months, because that is the unit the contract has: "el último
# año" is twelve months back, and leaving it out is what sent the phrase to the model and started
# the loop.
_COUNTED: Final = re.compile(
    r"ultim[oa]s?\s+(?:(\d{1,3})\s+)?(dias?|meses|mes|anos|ano)\b",
    re.IGNORECASE,
)

_UNIT: Final[dict[str, Period]] = {
    "dia": Period.LAST_N_DAYS,
    "dias": Period.LAST_N_DAYS,
    "mes": Period.LAST_N_MONTHS,
    "meses": Period.LAST_N_MONTHS,
    "ano": Period.LAST_N_MONTHS,
    "anos": Period.LAST_N_MONTHS,
}

# How many months a unit is worth. Only years are not one of themselves.
_MONTHS_PER: Final[dict[str, int]] = {"ano": 12, "anos": 12}


def whole_year(year: int) -> AbsoluteRange:
    """A calendar year as the range it is. Both ends included, like every range here."""
    return AbsoluteRange(start=date(year, 1, 1), end=date(year, 12, 31))


def whole_month(year: int, month: int) -> AbsoluteRange:
    """A calendar month as the range it is. `monthrange` rather than a table of lengths: February
    is the one that moves and a hand-written table is how it stops moving."""
    return AbsoluteRange(
        start=date(year, month, 1), end=date(year, month, monthrange(year, month)[1])
    )


def whole_quarter(year: int, quarter: int) -> AbsoluteRange:
    """One complete calendar quarter."""
    start_month = (quarter - 1) * 3 + 1
    end_month = start_month + 2
    return AbsoluteRange(
        start=date(year, start_month, 1),
        end=date(year, end_month, monthrange(year, end_month)[1]),
    )


def whole_semester(year: int, semester: int) -> AbsoluteRange:
    """January-June or July-December."""
    start_month = 1 if semester == 1 else 7
    end_month = start_month + 5
    return AbsoluteRange(
        start=date(year, start_month, 1),
        end=date(year, end_month, monthrange(year, end_month)[1]),
    )


def whole_fortnight(year: int, month: int, fortnight: int) -> AbsoluteRange:
    """The first (1-15) or second (16-end) half of a calendar month."""
    return AbsoluteRange(
        start=date(year, month, 1 if fortnight == 1 else 16),
        end=date(year, month, 15 if fortnight == 1 else monthrange(year, month)[1]),
    )


def _block_year(
    match: re.Match[str], *, position: int, current_position: int, today: date | None
) -> int | None:
    written = match.group("year")
    relative = match.group("relative")
    if written is not None:
        return int(written)
    if today is None:
        return None
    if relative is not None:
        return today.year if relative.startswith("este") else today.year - 1
    return today.year if position <= current_position else today.year - 1


def _quarter_from(match: re.Match[str], today: date | None) -> AbsoluteRange | None:
    position = _BLOCK_POSITION[match.group("position")]
    year = _block_year(
        match,
        position=position,
        current_position=((today.month - 1) // 3 + 1) if today is not None else position,
        today=today,
    )
    return whole_quarter(year, position) if year is not None else None


def _semester_from(match: re.Match[str], today: date | None) -> AbsoluteRange | None:
    position = _BLOCK_POSITION[match.group("position")]
    year = _block_year(
        match,
        position=position,
        current_position=(1 if today is None or today.month <= 6 else 2),
        today=today,
    )
    return whole_semester(year, position) if year is not None else None


def _fortnight_from(match: re.Match[str], today: date | None) -> AbsoluteRange | None:
    position = _BLOCK_POSITION[match.group("position")]
    month = _MONTH_NUMBER[match.group("month")]
    current_position = 1 if today is None or today.day <= 15 else 2
    written = match.group("year")
    relative = match.group("relative")
    if written is not None:
        year = int(written)
    elif relative is not None and today is not None:
        year = today.year if relative.startswith("este") else today.year - 1
    elif today is not None:
        happened = (month, position) <= (today.month, current_position)
        year = today.year if happened else today.year - 1
    else:
        return None
    return whole_fortnight(year, month, position)


def natural_period_in(sentence: str, *, today: date | None) -> Window | None:
    """Calendar blocks that are richer than the original day/week/month vocabulary."""
    folded = spanish.folded(sentence)
    quarter = _QUARTER_MENTION.search(folded)
    if quarter is not None:
        return _quarter_from(quarter, today)
    semester = _SEMESTER_MENTION.search(folded)
    if semester is not None:
        return _semester_from(semester, today)
    fortnight = _FORTNIGHT_MENTION.search(folded)
    if fortnight is not None:
        return _fortnight_from(fortnight, today)

    if _LAST_QUARTER.search(folded):
        return RelativePeriod(Period.LAST_N_MONTHS, n=3)
    if _PREVIOUS_QUARTER.search(folded):
        return RelativePeriod(Period.LAST_QUARTER)
    if _THIS_QUARTER.search(folded):
        return RelativePeriod(Period.THIS_QUARTER)
    if _LAST_SEMESTER.search(folded):
        return RelativePeriod(Period.LAST_N_MONTHS, n=6)
    if _PREVIOUS_SEMESTER.search(folded):
        return RelativePeriod(Period.LAST_SEMESTER)
    if _THIS_SEMESTER.search(folded):
        return RelativePeriod(Period.THIS_SEMESTER)
    if _PREVIOUS_FORTNIGHT.search(folded):
        return RelativePeriod(Period.LAST_FORTNIGHT)
    if _THIS_FORTNIGHT.search(folded):
        return RelativePeriod(Period.THIS_FORTNIGHT)
    if _PREVIOUS_WEEKEND.search(folded):
        return RelativePeriod(Period.LAST_WEEKEND)
    if _THIS_WEEKEND.search(folded):
        if today is None or today.weekday() < 5:
            return None
        return RelativePeriod(Period.THIS_WEEKEND)
    return None


def _compared_explicit_blocks(
    folded: str,
    pattern: re.Pattern[str],
    reader: Callable[[re.Match[str], date | None], AbsoluteRange | None],
    today: date,
) -> tuple[AbsoluteRange, AbsoluteRange] | None:
    matches = tuple(pattern.finditer(folded))
    if (
        len(matches) != 2
        or _COMPARISON_CONNECTOR.search(folded[matches[0].end() : matches[1].start()]) is None
    ):
        return None
    first = reader(matches[0], today)
    second = reader(matches[1], today)
    if first is None or second is None or first == second:
        return None
    return first, second


def _relative_pair(
    folded: str,
    current_pattern: re.Pattern[str],
    previous_pattern: re.Pattern[str],
    current: Period,
    previous: Period,
) -> tuple[Window, Window] | None:
    current_match = current_pattern.search(folded)
    previous_match = previous_pattern.search(folded)
    if current_match is None or previous_match is None:
        return None
    pair = (RelativePeriod(current), RelativePeriod(previous))
    return pair if current_match.start() < previous_match.start() else (pair[1], pair[0])


def compared_natural_periods(sentence: str, *, today: date) -> tuple[Window, Window] | None:
    """Two quarters, semesters, fortnights or weekends contrasted in mention order."""
    folded = spanish.folded(sentence)
    for pattern, reader in (
        (_QUARTER_MENTION, _quarter_from),
        (_SEMESTER_MENTION, _semester_from),
        (_FORTNIGHT_MENTION, _fortnight_from),
    ):
        explicit = _compared_explicit_blocks(folded, pattern, reader, today)
        if explicit is not None:
            return explicit

    previous_quarter = re.compile(
        r"\b(?:(?:el\s+)?trimestre\s+(?:pasado|anterior)|el\s+anterior)\b"
    )
    previous_semester = re.compile(
        r"\b(?:(?:el\s+)?semestre\s+(?:pasado|anterior)|el\s+anterior)\b"
    )
    previous_fortnight = re.compile(
        r"\b(?:(?:la\s+)?quincena\s+(?:pasada|anterior)|la\s+anterior)\b"
    )
    previous_weekend = re.compile(
        r"\b(?:(?:el\s+)?fin\s+de\s+semana\s+(?:pasado|anterior)|el\s+anterior)\b"
    )
    return (
        _relative_pair(
            folded,
            _THIS_QUARTER,
            previous_quarter,
            Period.THIS_QUARTER,
            Period.LAST_QUARTER,
        )
        or _relative_pair(
            folded,
            _THIS_SEMESTER,
            previous_semester,
            Period.THIS_SEMESTER,
            Period.LAST_SEMESTER,
        )
        or _relative_pair(
            folded,
            _THIS_FORTNIGHT,
            previous_fortnight,
            Period.THIS_FORTNIGHT,
            Period.LAST_FORTNIGHT,
        )
        or _relative_pair(
            folded,
            _THIS_WEEKEND,
            previous_weekend,
            Period.THIS_WEEKEND,
            Period.LAST_WEEKEND,
        )
    )


def _a_month_with_no_year(folded: str) -> int | None:
    """The month a sentence names when it names exactly one and no year, or None.

    Two refusals, and both would otherwise produce a real figure about the wrong days:

        a day    "el 15 de junio" is a date. The whole of June is thirty times the question.
        a range  "de junio a septiembre" names two, and answering with either is a third of it.

    None means "send it to the model", which is where a sentence this is not sure about belongs.
    """
    named = _A_BARE_MONTH.search(folded)
    if named is None or _A_DAY_OF_A_MONTH.search(folded):
        return None
    months = {
        _MONTH_NUMBER[name]
        for name in _MONTHS
        if re.search(rf"(?<![a-z0-9]){name}(?![a-z0-9])", folded)
    }
    return _MONTH_NUMBER[named.group("month")] if len(months) == 1 else None


def _year_of(month: int, today: date) -> int:
    """Which year a bare month means: the last time it happened, counting the one under way.

    "En junio" said in August is this June; said in March it is last June, because June has not
    come round yet. Nobody says "en junio" about a month four months away — that is "el junio que
    viene", and this vocabulary deliberately does not read the future.
    """
    return today.year if month <= today.month else today.year - 1


def compared_calendar_months(
    sentence: str, *, today: date
) -> tuple[AbsoluteRange, AbsoluteRange] | None:
    """Exactly two whole months explicitly contrasted in one sentence, in mention order."""
    folded = spanish.folded(sentence)
    if _A_DAY_OF_A_MONTH.search(folded):
        return None
    mentions = tuple(_MONTH_MENTION.finditer(folded))
    if len(mentions) != 2:
        return None
    between = folded[mentions[0].end() : mentions[1].start()]
    if _COMPARISON_CONNECTOR.search(between) is None:
        return None

    windows: list[AbsoluteRange] = []
    for mention in mentions:
        month = (
            _MONTH_NUMBER[mention.group("month")]
            if mention.group("month") is not None
            else int(mention.group("number"))
        )
        written_year = mention.group("year")
        relative = mention.group("relative")
        if written_year is not None:
            year = int(written_year)
        elif relative is not None:
            year = today.year if relative.startswith("este") else today.year - 1
        else:
            year = _year_of(month, today)
        windows.append(whole_month(year, month))
    if windows[0] == windows[1]:
        return None
    return windows[0], windows[1]


def compared_calendar_years(
    sentence: str, *, today: date
) -> tuple[AbsoluteRange, AbsoluteRange] | None:
    """Exactly two whole years explicitly contrasted in one sentence, in mention order."""
    folded = spanish.folded(sentence)
    mentions = tuple(_YEAR_MENTION.finditer(folded))
    if len(mentions) != 2:
        return None
    between = folded[mentions[0].end() : mentions[1].start()]
    if _COMPARISON_CONNECTOR.search(between) is None:
        return None

    years = tuple(
        int(mention.group("year"))
        if mention.group("year") is not None
        else (today.year if mention.group("relative") == "este" else today.year - 1)
        for mention in mentions
    )
    if years[0] == years[1]:
        return None
    return whole_year(years[0]), whole_year(years[1])


def date_range_in(sentence: str, *, today: date) -> AbsoluteRange | None:
    """One exact, past range, only when the sentence contains exactly one."""
    matches = tuple(_DATE_RANGE.finditer(spanish.folded(sentence)))
    if len(matches) != 1:
        return None
    return _range_from(matches[0], today)


def compared_date_ranges(
    sentence: str, *, today: date
) -> tuple[AbsoluteRange, AbsoluteRange] | None:
    """Two complete explicit ranges, or one followed by the same dates a year earlier."""
    folded = spanish.folded(sentence)
    matches = tuple(_DATE_RANGE.finditer(folded))
    if len(matches) == 1 and _SAME_RANGE_LAST_YEAR.search(folded[matches[0].end() :]):
        current = _range_from(matches[0], today)
        if current is None:
            return None
        return current, AbsoluteRange(
            start=_a_year_before(current.start), end=_a_year_before(current.end)
        )
    if len(matches) != 2:
        return None
    between = folded[matches[0].end() : matches[1].start()]
    if _COMPARISON_CONNECTOR.search(between) is None:
        return None
    first = _range_from(matches[0], today)
    second = _range_from(matches[1], today)
    if first is None or second is None or first == second:
        return None
    return first, second


def _range_from(match: re.Match[str], today: date) -> AbsoluteRange | None:
    start_month = _MONTH_NUMBER[match.group("start_month")]
    end_month = _MONTH_NUMBER[match.group("end_month")]
    written_start_year = match.group("start_year")
    written_end_year = match.group("end_year")

    if written_start_year is not None:
        start_year = int(written_start_year)
    elif written_end_year is not None:
        end_year = int(written_end_year)
        start_year = end_year - int(start_month > end_month)
    else:
        end_this_year = _safe_date(today.year, end_month, int(match.group("end_day")))
        if end_this_year is None:
            return None
        end_year = today.year if end_this_year <= today else today.year - 1
        start_year = end_year - int(start_month > end_month)

    if written_end_year is not None:
        end_year = int(written_end_year)
    elif written_start_year is not None:
        end_year = start_year + int(end_month < start_month)

    start = _safe_date(start_year, start_month, int(match.group("start_day")))
    end = _safe_date(end_year, end_month, int(match.group("end_day")))
    if start is None or end is None or end < start or end > today:
        return None
    try:
        return AbsoluteRange(start=start, end=end)
    except InvalidValueError:
        return None


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def window_in(
    sentence: str, *, asked_for: Slot = Slot.WINDOW, today: date | None = None
) -> Window | None:
    """The window this sentence names, or None when it names none this vocabulary is sure about.

    None is the useful answer as often as a window is: it means "send this to the model", which is
    where a sentence this does not recognise belongs.

    `asked_for` is the slot the previous turn asked about, and it is what makes a two-word reply
    readable. "De este" names nothing on its own — but as the answer to "¿de qué año?" it is this
    year, and that is the difference between a conversation and a list of sentences. Without the
    slot this file could only grow phrases, which is exactly how it acquired "del año" and "el año".

    `today` is what "el año pasado" is relative to. Without it that phrase stays unreadable rather
    than being resolved against a clock this service is not allowed to read — the same rule the
    whole pipeline follows, and the reason the evaluation can replay it.
    """
    folded = spanish.folded(sentence)

    explicit_range = date_range_in(sentence, today=today) if today is not None else None
    if explicit_range is not None:
        return explicit_range

    natural = natural_period_in(sentence, today=today)
    if natural is not None:
        return natural

    if asked_for is Slot.YEAR and _THIS_ONE.fullmatch(folded.strip(" ?¿.!¡,")):
        return RelativePeriod(period=Period.THIS_YEAR)

    month = _A_MONTH.search(folded)
    if month:
        # Before the year, because "julio de 2026" contains "de 2026" and reading that as the year
        # would answer a question about one month with twelve.
        number = _MONTH_NUMBER[month.group("month")]
        if month.group("year"):
            return whole_month(int(month.group("year")), number)
        if today is None:
            # "Este año" and "el año pasado" are relative, and this service does not read a clock.
            return None
        said = month.group("relative")
        return whole_month(today.year if said.startswith("este") else today.year - 1, number)

    numbered_month = _A_NUMBERED_MONTH.search(folded)
    if numbered_month is not None:
        if today is None:
            return None
        number = int(numbered_month.group("month"))
        return whole_month(_year_of(number, today), number)

    named_year = _A_NAMED_YEAR.search(folded)
    if named_year:
        # "En 2025" is the least ambiguous thing anybody can say about a window, so a sentence
        # carrying it does not need the rest of this vocabulary consulted. In particular it must
        # be read before "el año", which the phrase table below answers as *this* year.
        written = next(group for group in named_year.groups() if group)
        return whole_year(int(written))

    if _THE_YEAR_BEFORE.search(folded):
        # Before the phrase table, because "el año pasado" ends in "el ano" and that entry means
        # *this* year. Reading it there was the failure this ordering exists to prevent.
        return whole_year(today.year - 1) if today is not None else None

    if today is not None:
        bare = _a_month_with_no_year(folded)
        if bare is not None:
            # After the explicit forms and before the phrase table, which is the whole fix: "hoy
            # quiero saber cuánto gasté en junio" fell through to "hoy", because a month with no
            # year was not a window this file knew — a real figure about today, answering about
            # June, with nothing anywhere to contradict it.
            return whole_month(_year_of(bare, today), bare)

    counted = _COUNTED.search(folded)
    if counted:
        # No number means one: "el último mes" is a month back, not an unbounded period.
        count = int(counted.group(1)) if counted.group(1) else 1
        unit = counted.group(2)
        try:
            return RelativePeriod(period=_UNIT[unit], n=count * _MONTHS_PER.get(unit, 1))
        except InvalidValueError:
            # `n` is bounded by the domain, so "los últimos 5000 días" is not a window this can
            # build. None sends it to the model, which is better than a reader that raises.
            return None

    for phrase, period in _NAMED:
        if re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", folded):
            return RelativePeriod(period=period)
    return None


# "¿Y el año anterior?" after a question about July. It shifts whatever window was just answered
# rather than naming one, which is why it cannot live in the table above: "el año anterior" means
# July 2025 after July 2026 and the whole of 2025 after nothing at all.
_A_YEAR_BACK: Final = re.compile(
    r"(y\s+)?(el\s+)?(mismo\s+periodo\s+(del|el)\s+)?ano\s+(anterior|pasado)"
)


def a_year_earlier(sentence: str, previous: AbsoluteRange | None) -> AbsoluteRange | None:
    """The same dates one year back, when that is all the sentence asks for.

    "¿Cuánto gasté en restaurantes en julio de este año?" then "¿y el año anterior?" means July of
    last year. It used to mean the last twelve months, or nothing at all — the phrase was refused
    outright, so the turn went to the model and came back asking for a period again.

    Only against an explicit range. A relative period would have to be resolved to be shifted, and
    resolving windows is banking's job — two implementations of what LAST_MONTH covers is one too
    many, which is the rule this file has followed since it was written.
    """
    if previous is None:
        return None
    if not _A_YEAR_BACK.fullmatch(spanish.folded(sentence).strip(" ?¿.!¡,")):
        return None
    return AbsoluteRange(start=_a_year_before(previous.start), end=_a_year_before(previous.end))


def _a_year_before(day: date) -> date:
    """The same day one year back. 29 February becomes the 28th, which is the only day that has to
    move and the convention every calendar uses for it."""
    try:
        return day.replace(year=day.year - 1)
    except ValueError:
        return day.replace(year=day.year - 1, day=28)
