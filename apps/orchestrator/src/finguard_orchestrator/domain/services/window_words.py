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

# Two calendar labels joined as alternatives, used only by the direct-comparison path. Unlike the
# ordinary one-window reader, this accepts bare month names because the connector between exactly
# two of them ("julio con junio", "julio que en junio") supplies the missing structure.
_COMPARISON_CONNECTOR: Final = re.compile(r"\b(?:que|con|contra|versus|vs|y|o|frente\s+a)\b")
_MONTH_MENTION: Final = re.compile(
    rf"\b(?P<month>{'|'.join(_MONTHS)})\b"
    r"(?:\s+(?:de|del)\s+(?:el\s+)?(?:(?:ano\s+)?(?P<year>20\d{2})"
    r"|(?P<relative>este\s+ano|ano\s+(?:pasado|anterior))))?"
)
_YEAR_MENTION: Final = re.compile(
    r"\b(?:(?:el\s+)?ano\s+(?P<relative>este|pasado|anterior)"
    r"|(?:(?:el\s+)?ano\s+)?(?P<year>20\d{2}))\b"
)

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
        month = _MONTH_NUMBER[mention.group("month")]
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
