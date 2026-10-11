"""Reading a window out of a sentence, without a model.

The clarification the planner asks for is almost always the window — "¿de qué periodo?" — and
a window is a closed vocabulary exactly like a category. When somebody answers "este año"
there is nothing to infer, so there is nothing to ask a model about.

This is what makes a clarification finishable. Before it, answering the question restarted
the conversation: the reply arrived as a two-word sentence with no context and a complete
plan had to be derived from it, which is why "este año" came back asking for the period
again.

**Precision over coverage.** "Hace un mes" and "en verano" are windows a person understands
and this does not, and they are left out on purpose: a miss falls through to the model and
costs one turn, while a wrong guess costs a figure about the wrong month presented as an
answer.

Two readings are product decisions rather than vocabulary (spec §3.5, 2026-10-07): "llevo
gastado" with no window is the year so far, and "últimamente" about money spent or received is
the last 30 days. Neither is a fact about Spanish. Each is what the product answers rather than
asking, and every answer states the window it used — «en lo que va de año», «en los últimos 30
días» — so the default is said, never guessed silently. Both read last, under every window a
sentence states more precisely, and both stand aside where a window is named that this cannot
read.

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
from datetime import date, timedelta
from typing import Final

from finguard_orchestrator.domain.enums import Period
from finguard_orchestrator.domain.errors import InvalidValueError
from finguard_orchestrator.domain.models.conversation import Slot
from finguard_orchestrator.domain.services import explicit_lexicon, spanish, spanish_numbers
from finguard_orchestrator.domain.value_objects.financial_plan import AbsoluteRange, RelativePeriod

# A window this vocabulary is sure about, whichever of the two shapes it takes.
type Window = RelativePeriod | AbsoluteRange

# Phrases that name a window on their own, longest first. Folded, so accents and case do not matter.
_NAMED: Final[tuple[tuple[str, Period], ...]] = (
    ("en lo que llevamos de ano", Period.THIS_YEAR),
    ("en lo que va de ano", Period.THIS_YEAR),
    ("en lo que llevamos de mes", Period.THIS_MONTH),
    ("en lo que va de mes", Period.THIS_MONTH),
    # The month and the week, in all four spellings their five sister units get from one regex each.
    #
    # **The parity is enforced by a test rather than by the shape of this table.** Quarter,
    # semester, fortnight, weekend and year are read further up as a noun plus an adjective, so all
    # five have known "anterior" since they were written; the month and the week were literals here
    # and knew only "pasado". Moving them up there was tried and is wrong for a reason worth
    # keeping: `natural_period_in` is consulted by a *different* recogniser, so a month that
    # answered from it stopped being handed over at all — fifty-five turns changing hands to fix
    # two spellings, and two turns of the corpus left with their reader decided by the order of a
    # cascade. The table is data; `tests/test_a_class_knows_what_its_sisters_know.py` is the rule.
    #
    # Without the determiner, because "del" hides one: the entry read "el mes pasado" and is matched
    # between word boundaries, so "¿cuánto gasté del mes pasado?" carried no window at all.
    ("semana pasada", Period.LAST_WEEK),
    ("semana anterior", Period.LAST_WEEK),
    ("mes pasado", Period.LAST_MONTH),
    ("mes anterior", Period.LAST_MONTH),
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

# "¿Cuánto llevo desde el lunes?" — a window that opens on a weekday and closes today.
#
# **Monday and no other weekday, because the week starts there.** "Desde el lunes" covers exactly
# the days THIS_WEEK covers, on whichever day of the week it is said, so this reads a window the
# contract already names rather than deriving one from a clock. "Desde el martes" has no name in
# the contract *and* no unambiguous start — this week's Tuesday when it has come round, last week's
# when it has not — and an ambiguity is what None is for. It went unread, so a model answered it
# with a rolling seven days and there was nothing to say otherwise.
#
# What follows the word is what decides it is still *this* Monday. "El lunes pasado" is the one
# before, "el lunes 13" is a date, "el lunes de la semana pasada" is qualified, and "desde el lunes
# hasta el viernes" closes somewhere that is not today. All four are somebody else's window.
_SINCE_MONDAY: Final = re.compile(
    r"\bdesde\s+(?:el\s+)?lunes\b(?!\s+(?:pasado|anterior|de\b|del\b|hasta\b|al\b|a\b|\d))"
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

# "¿Qué gasté entre el 10 y el 20 de mayo?" — both ends of a range inside one named month, with the
# month written once at the end and understood on both.
#
# The reader above wants a month on *each* endpoint, so it never saw this: "entre el 10" is a bare
# day. The sentence states its window as plainly as any it does read, and answering None left a
# model free to place the same two days in the wrong year.
#
# **Narrow on purpose, in three directions.** Only "entre … y …", so "del 10 al 20 de mayo" — the
# same shape said another way — is still nobody's business here until it is measured. Only with the
# month present, because "entre el 10 y el 20" alone names two numbers and no calendar. And the
# article is required on the first day, which is what keeps amounts out: dates in Spanish carry it
# ("entre el 10 y el 20 de mayo") and quantities do not ("entre 10 y 20 euros").
#
# The trailing year is optional and read when written, for the reason it is optional above: without
# it the phrase would match the prefix of "entre el 10 y el 20 de mayo de 2025" and answer with a
# year the sentence had already denied — the precise failure a widened reader is dangerous for.
_SHARED_MONTH_RANGE: Final = re.compile(
    rf"\bentre\s+el\s+(?P<start_day>\d{{1,2}})\s+y\s+(?:el\s+)?(?P<end_day>\d{{1,2}})"
    rf"\s+de\s+(?P<month>{'|'.join(_MONTHS)})"
    r"(?:\s+(?:de|del)\s+(?:el\s+)?(?:ano\s+)?(?P<year>20\d{2}))?\b"
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

# A year and nothing else — «2026», «el 2026», «del año 2025». Only as the reply to «¿De qué año?»,
# for the reason `window_in` gives where it reads it.
_A_YEAR_ALONE: Final = re.compile(r"(?:(?:el|del|de|en)\s+)?(?:ano\s+)?(?P<year>20\d{2})")

# "los últimos 30 días", "últimos 3 meses", "el último año", and the singular "el último mes"
# which is n=1 — while the plural with no number, "los últimos meses", is a few (`_UNSTATED`
# below). Years are counted in months, because that is the unit the contract has: "el último
# año" is twelve months back, and leaving it out is what sent the phrase to the model and started
# the loop.
#
# **The count may sit on either side of the word.** Spanish puts it in both places — "los últimos 2
# meses" and "los 2 últimos meses" — and only the first was read. The second did not fail: it fell
# through to the default and became *one* month, so "los 10 últimos días" answered about yesterday
# with a real figure. Found by asserting the query a corpus turn ran rather than only which tool
# answered it.
#
# Either position accepts digits or a written cardinal, because "los dos últimos meses" was the same
# silent one, and half of a fix to a wrong number is not one.
_COUNTED: Final = re.compile(
    r"(?:(?P<before>\d+|[a-z]+)\s+)?ultim[oa]s?\s+"
    r"(?:(?P<after>\d+|[a-z]+)\s+)?(?P<unit>dias?|meses|mes|anos|ano)\b",
    re.IGNORECASE,
)


# What a counted phrase means when nobody says the number, by its unit. The singular is one — "el
# último mes" is a month back — and the plural is a few: "los últimos meses" is three months, "los
# últimos días" a week, "los últimos años" three years. The plural read as one for as long as the
# singular did, and that was wrong in the way that costs most: the live log showed this rule's
# LAST_N_MONTHS:1 overriding the model's LAST_N_MONTHS:3, a real figure for one month answering a
# question about several. "A few" is a convention rather than a fact: three months is what the
# model read in that log and the period chip this service already offers («Los últimos 3 meses»),
# and a week and three years are the same reading in the other two units. Three years is past
# MAX_PERIOD_MONTHS, so that one is declined below like any count the domain refuses, and the
# model reads it.
_UNSTATED: Final[dict[str, int]] = {"dias": 7, "meses": 3, "anos": 3}


def _stated(counted: re.Match[str]) -> int | None:
    """The count either side of "último", or None when neither side names a number.

    A word that is not a number — the article in "el último mes", the preposition in "de los últimos
    días" — is no count at all.
    """
    for group in ("before", "after"):
        written = counted.group(group)
        if written is None:
            continue
        if written.isdigit():
            return int(written)
        value = spanish_numbers.integer(written)
        if value is not None and value > 0:
            return value
    return None


def _how_many(counted: re.Match[str]) -> int:
    """The count stated, or what the phrase means without one: one, or a few for a plural."""
    stated = _stated(counted)
    if stated is not None:
        return stated
    return _UNSTATED.get(counted.group("unit"), 1)


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

# «¿Cuánto llevo gastado?» — what has gone so far, and so far is the year (spec §3.5, a product
# decision on 2026-10-07). It asked «¿De qué periodo?» before, about a question whose own verb says
# "up to now"; the answer says «en lo que va de año», so the year is stated rather than assumed.
_SO_FAR: Final = re.compile(r"\bllevo\s+gastad[oa]s?\b")
# What «llevo» already says, said again: up to now. Not a window of its own, so it does not stand in
# the way of the year so far — «hasta hoy» used to be read as today (ruling R46).
_UP_TO_NOW: Final = re.compile(r"\bhasta\s+(?:ahora|la\s+fecha|hoy|el\s+dia\s+de\s+hoy)\b")

# Words that name a window, whether or not this file can read the one they name. «Llevo gastado
# desde Navidad» and «… en la semana en curso» each name a window nothing here reads; a default in
# its place — the year so far, the last 30 days — would be a different window presented as the one
# asked about, so a sentence carrying any of these goes to the model as it always did. A digit is a
# date or a year. Generous on purpose — a word here that was not a window costs one question back,
# as before.
_A_WINDOW_IS_NAMED: Final = re.compile(
    r"\d|\b(?:desde|hasta|hace|durante|hoy|ayer|dias?|semanas?|finde|mes|meses|anos?|trimestres?"
    r"|semestres?|quincenas?|temporada|curso|ejercicio|periodo|epoca|verano|invierno|primavera"
    r"|otono|navidad(?:es)?|puente|vacaciones|mananas?|tardes?|noches?|ultim[oa]s?|ultimamente"
    r"|lunes|martes|miercoles|jueves|viernes|sabados?|domingos?|" + "|".join(_MONTHS) + r")\b"
)

# «Últimamente» — the last 30 days (spec §3.5, a product decision on 2026-10-07), which is also the
# window the period question offers as its example. As an adverb only: after a preposition, a quote
# or «concepto» it is part of a name — a shop, a payment's concept — and nothing here may read a
# window out of a name.
_LATELY: Final = re.compile(
    r"(?<![«\"'“])(?<!\ben\s)(?<!\bde\s)(?<!\bdel\s)(?<!\ba\s)(?<!concepto\s)\bultimamente\b"
)
# The adverb is capitalised only where a sentence starts; «el bar Últimamente» is a name. Read on
# the sentence as written, because folding is what loses the capital.
_A_NAME_CALLED_LATELY: Final = re.compile(r"\w[\s«\"'“]+[ÚU]ltimamente\b")
# And only as the window of money spent or received, in `explicit_facts`' own words for the two
# directions, so «¿qué tal últimamente?» stays a sentence about nothing this file can date.
_SPENT: Final = explicit_lexicon.compiled("expense")
_RECEIVED: Final = explicit_lexicon.compiled("income")
_THE_WORD_LATELY: Final = re.compile(r"\bultimamente\b")


def _lately(sentence: str, folded: str) -> bool:
    """Whether «últimamente» is, in this sentence, the window of money spent or received.

    Not beside another window the sentence names (ruling R46): «últimamente desde Navidad», «…
    los fines de semana», «… hace un mes» each say a window this file does not read, and the last
    30 days in its place would be a different one presented as theirs — the rule «llevo gastado»
    keeps, with the word itself left out of the count.
    """
    return (
        _LATELY.search(folded) is not None
        and _A_NAME_CALLED_LATELY.search(sentence) is None
        and (_SPENT.search(folded) is not None or _RECEIVED.search(folded) is not None)
        and _A_WINDOW_IS_NAMED.search(_THE_WORD_LATELY.sub(" ", folded)) is None
    )


def whole_year(year: int) -> AbsoluteRange:
    """A calendar year as the range it is. Both ends included, like every range here."""
    return AbsoluteRange(start=date(year, 1, 1), end=date(year, 12, 31))


def whole_month(year: int, month: int) -> AbsoluteRange:
    """A calendar month as the range it is. `monthrange` rather than a table of lengths: February
    is the one that moves and a hand-written table is how it stops moving.

    C-4 (Wave 3 hardening): never raises for a `(year, month)` pair `visual_reference.parsed_month`
    accepted -- that reader now bounds the year to `2000-2099` (the same range every other named
    year in this file already enforces) and the month to `01-12`, so `date`/`monthrange` here
    never see a value outside what they can build. A caller with an unvalidated year is still its
    own responsibility; this function itself does not re-check one."""
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


def date_range_in(sentence: str, *, today: date) -> AbsoluteRange | None:
    """One exact, past range, only when the sentence contains exactly one.

    Two spellings of the same thing: a month on each endpoint, or one month at the end doing for
    both. They are counted together rather than one after the other, because "exactly one" is this
    reader's whole guarantee — a sentence carrying one of each states two windows, and answering
    with either of them is answering a question nobody asked.
    """
    folded = spanish.folded(sentence)
    matches = tuple(_DATE_RANGE.finditer(folded))
    shared = tuple(_SHARED_MONTH_RANGE.finditer(folded))
    if len(matches) + len(shared) != 1:
        return None
    if matches:
        return _range_from(matches[0], today)
    return _shared_month_range_from(shared[0], today)


def _range_from(match: re.Match[str], today: date) -> AbsoluteRange | None:
    written_start_year = match.group("start_year")
    written_end_year = match.group("end_year")
    return _range_between(
        start_day=int(match.group("start_day")),
        start_month=_MONTH_NUMBER[match.group("start_month")],
        end_day=int(match.group("end_day")),
        end_month=_MONTH_NUMBER[match.group("end_month")],
        written_start_year=int(written_start_year) if written_start_year is not None else None,
        written_end_year=int(written_end_year) if written_end_year is not None else None,
        today=today,
    )


def _shared_month_range_from(match: re.Match[str], today: date) -> AbsoluteRange | None:
    """ "Entre el 10 y el 20 de mayo": one month, written once, standing for both endpoints.

    Through the same arithmetic as the two-month form rather than beside it. The year a range with
    none written gets — the last time its end happened, counting the one under way — is a rule this
    file already holds in one place, and the whole reason this shape is worth teaching is that a
    model put these two days in the wrong year. A second copy of that rule is how the two answers
    start to disagree.
    """
    month = _MONTH_NUMBER[match.group("month")]
    written = match.group("year")
    year = int(written) if written is not None else None
    return _range_between(
        start_day=int(match.group("start_day")),
        start_month=month,
        end_day=int(match.group("end_day")),
        end_month=month,
        written_start_year=year,
        written_end_year=year,
        today=today,
    )


def _range_between(
    *,
    start_day: int,
    start_month: int,
    end_day: int,
    end_month: int,
    written_start_year: int | None,
    written_end_year: int | None,
    today: date,
) -> AbsoluteRange | None:
    """Two endpoints and whichever years the sentence wrote down, as the range they bound.

    A year nobody wrote is the last time that end came round, counting the one under way — the
    convention `_year_of` states for a bare month, and the reason "el 10 al 20 de mayo" said in
    July is this May. The other end follows it across January when the months run backwards.
    """
    if written_start_year is not None:
        start_year = written_start_year
    elif written_end_year is not None:
        start_year = written_end_year - int(start_month > end_month)
    else:
        end_this_year = _safe_date(today.year, end_month, end_day)
        if end_this_year is None:
            return None
        last_time = today.year if end_this_year <= today else today.year - 1
        start_year = last_time - int(start_month > end_month)

    if written_end_year is not None:
        end_year = written_end_year
    else:
        end_year = start_year + int(end_month < start_month)

    start = _safe_date(start_year, start_month, start_day)
    end = _safe_date(end_year, end_month, end_day)
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

    # F-H2 (Wave 3 hardening, fix round): before `_A_MONTH`, which would otherwise match this
    # phrase's own trailing "agosto de 2026" as a single bare month and answer with a third of the
    # range asked for.
    month_range = _a_month_range(folded)
    if month_range is not None:
        return month_range

    if asked_for is Slot.YEAR and _THIS_ONE.fullmatch(folded.strip(" ?¿.!¡,")):
        return RelativePeriod(period=Period.THIS_YEAR)

    a_year = _A_YEAR_ALONE.fullmatch(folded.strip(" ?¿.!¡,"))
    if asked_for is Slot.YEAR and a_year is not None:
        # «2026» as the whole answer to «¿De qué año?» (validation fix 1, F2). `_A_NAMED_YEAR`
        # wants a preposition or «año» in front, because four digits anywhere else may be an
        # amount; asked for a year, they can only be one. Read as nothing, the reply was put to
        # the model, which asked the year again: «Sigo sin tener claro el periodo».
        return whole_year(int(a_year.group("year")))

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
        # No number means what the phrase means without one, never an unbounded period: the
        # singular is one ("el último mes" is a month back) and the plural is a few ("los últimos
        # meses" is three). Reading the plural as one is what let this rule's one month override
        # the model's three in the live log; `_UNSTATED` holds the few and says why.
        count = _how_many(counted)
        unit = counted.group("unit")
        try:
            return RelativePeriod(period=_UNIT[unit], n=count * _MONTHS_PER.get(unit, 1))
        except InvalidValueError:
            # `n` is bounded by the domain, so "los últimos 5000 días" is not a window this can
            # build. None sends it to the model, which is better than a reader that raises.
            return None

    if _SINCE_MONDAY.search(folded):
        # Last of the readers and only just before the table, because everything above states a
        # window more precisely than a weekday does: a sentence that also names a month, a year or
        # an explicit range said the more particular thing and keeps it.
        return RelativePeriod(period=Period.THIS_WEEK)

    # «Llevo gastado» before the table only so that «hasta hoy» is not read as today: it stands
    # aside for every other window word, the table's own included, so «… este mes» is still this
    # month and «… hoy» still today.
    so_far = _so_far(folded)
    if so_far is not None:
        return so_far

    for phrase, period in _NAMED:
        if re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", folded):
            return RelativePeriod(period=period)

    # The two product defaults, last: every window above is something the sentence states more
    # precisely, so «¿cuánto llevo gastado este mes?» is this month and «… desde el lunes» this
    # week.
    return _a_default(sentence, folded)


def _a_default(sentence: str, folded: str) -> Window | None:
    """«Últimamente» about money, or «llevo gastado» beside no other window: the product's answer
    to a sentence that states no window (spec §3.5). Not rows of the table, because neither is a
    phrase that names a window wherever it stands — the first needs a sentence about money, the
    second a sentence that names no other window at all, read or not."""
    if _lately(sentence, folded):
        return RelativePeriod(period=Period.LAST_N_DAYS, n=30)
    return _so_far(folded)


def _so_far(folded: str) -> Window | None:
    """«Llevo gastado» naming no other window, read or not — «hasta ahora» is no other window."""
    if _SO_FAR.search(folded) and not _A_WINDOW_IS_NAMED.search(_UP_TO_NOW.sub(" ", folded)):
        return RelativePeriod(period=Period.THIS_YEAR)
    return None


_A_WORD: Final = re.compile(r"[a-z]+")


# --- validation fix 1, F2: a month said after an answer is a month of the year in view ---------
#
# «Ahora solo julio» after «junio vs julio» of 2026, «pero de julio y agosto» after a comparison of
# both: on 2026-10-08 the model asked «¿De qué año?» about each, a year the answer on screen had
# already said. The vocabulary below is what the handler needs to take the year from the answer
# instead — which months a turn names when months are all it names, and which year a window is in —
# and what it needs to complete «¿De qué año?» with the months that question was asked about.


# Validation fix 3, G5: a year said over the months in view, and months said with their year.
#
# «2026» with nothing open after a comparison of August and September was asked «¿De qué año?»,
# and «agosto y septiembre de 2026» answered September alone: `window_in` reads «septiembre de
# 2026» as the window and drops the month said before it. The handler moves the months in view to a
# year said alone, and takes months said with their year whole, from these.


# --- validation fix 1, F6: the period before, in the unit of the one in view ------------------
#
# «¿Y la anterior?» after this week's spending is last week. The step back was the follow-up
# reader's alone (`readers/windows._previous_calendar_window`), where the turn has to name its unit;
# with none named the model got it and asked which period. The week, month and year steps live here
# now, below both readers that need them — the follow-up patch, which reads the unit off the answer,
# and the follow-up reader, which reads it off the sentence — so the two cannot answer June in one
# turn and July in the other. The other units stay where they were.

# The calendar unit a window is one of, in the words a sentence names it with.
_UNIT_OF_A_PERIOD: Final[dict[Period, str]] = {
    Period.THIS_WEEK: "semana",
    Period.LAST_WEEK: "semana",
    Period.THIS_MONTH: "mes",
    Period.LAST_MONTH: "mes",
    Period.THIS_YEAR: "ano",
}


def calendar_unit_of(window: Window) -> str | None:
    """«semana», «mes» or «ano» when `window` is one whole week, month or year — this one, the
    last one, or one named by its dates — and None otherwise: thirty days back is no unit."""
    if isinstance(window, RelativePeriod):
        return _UNIT_OF_A_PERIOD.get(window.period)
    if window == whole_month(window.start.year, window.start.month):
        return "mes"
    if window == whole_year(window.start.year):
        return "ano"
    if (window.end - window.start).days == 6:
        return "semana"
    return None


def window_before(window: Window, unit: str, today: date) -> Window | None:
    """The `unit` («semana», «mes», «ano») directly before `window`, or None when `window` is not
    one of that unit.

    Named periods step back as named periods while the banking contract has one — this month to
    LAST_MONTH, this week to LAST_WEEK — and to dates when it has none: the month before last, the
    week before last, last year. A range steps back by its own dates.
    """
    if unit == "mes":
        if isinstance(window, RelativePeriod):
            if window.period is Period.THIS_MONTH:
                return RelativePeriod(Period.LAST_MONTH)
            if window.period is Period.LAST_MONTH:
                before_last = date(today.year, today.month, 1) - timedelta(days=32)
                return whole_month(before_last.year, before_last.month)
            return None
        if window == whole_month(window.start.year, window.start.month):
            before = window.start - timedelta(days=1)
            return whole_month(before.year, before.month)
        return None
    if unit == "semana":
        if isinstance(window, RelativePeriod):
            if window.period is Period.THIS_WEEK:
                return RelativePeriod(Period.LAST_WEEK)
            if window.period is Period.LAST_WEEK:
                monday = today - timedelta(days=today.weekday())
                return AbsoluteRange(
                    start=monday - timedelta(days=14), end=monday - timedelta(days=8)
                )
            return None
        if (window.end - window.start).days == 6:
            return AbsoluteRange(
                start=window.start - timedelta(days=7), end=window.end - timedelta(days=7)
            )
        return None
    if unit == "ano":
        if isinstance(window, RelativePeriod):
            return whole_year(today.year - 1) if window.period is Period.THIS_YEAR else None
        if window == whole_year(window.start.year):
            return whole_year(window.start.year - 1)
    return None


# "de junio a agosto de 2026" -- the season chip's own shape, and read narrowly on purpose: a
# trailing year is required, which is exactly what keeps this apart from "de junio a septiembre"
# (no year), the ambiguous range `_a_month_with_no_year` already refuses because it could be either
# a span or two months contrasted (`test_ambiguous_month_ranges_are_not_rewritten_as_two_whole_
# months`). Requiring the year is not a narrower version of that same guess -- it is a different,
# unambiguous sentence, the one the chip actually offers.
_A_MONTH_RANGE: Final = re.compile(
    rf"\bde\s+(?P<start_month>{'|'.join(_MONTHS)})\s+a\s+(?P<end_month>{'|'.join(_MONTHS)})\s+"
    rf"de\s+(?:el\s+)?(?P<year>20\d{{2}})\b"
)


def _a_month_range(folded: str) -> AbsoluteRange | None:
    match = _A_MONTH_RANGE.search(folded)
    if match is None:
        return None
    start_month = _MONTH_NUMBER[match.group("start_month")]
    end_month = _MONTH_NUMBER[match.group("end_month")]
    # The printed year anchors the *first* named month, the same convention `_season_months`
    # already uses for winter -- "de diciembre a febrero de 2026" is December 2026 through
    # February 2027, not February of the year December names.
    start_year = int(match.group("year"))
    end_year = start_year + 1 if end_month < start_month else start_year
    start = whole_month(start_year, start_month).start
    end = whole_month(end_year, end_month).end
    try:
        return AbsoluteRange(start=start, end=end)
    except InvalidValueError:
        return None
