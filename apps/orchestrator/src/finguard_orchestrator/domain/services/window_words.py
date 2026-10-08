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
from collections.abc import Callable
from datetime import date
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


def has_invalid_counted_window(sentence: str) -> bool:
    """Whether a stated relative count is outside the domain instead of merely absent.

    `window_in` must keep returning ``None`` for this case, but that value alone cannot tell a
    caller whether there was no recognised window or a recognised, impossible one. Keeping this
    distinction prevents the model from accepting "5000 days" under one paraphrase after asking
    for a correction under another.

    Only a *stated* count can be impossible. «Los últimos años» names none, and the three years
    `_UNSTATED` reads into it are past the bound — which is this vocabulary declining to guess, not
    the holder asking for something impossible, so the model still gets the sentence.
    """
    counted = _COUNTED.search(spanish.folded(sentence))
    if counted is None:
        return False
    count = _stated(counted)
    if count is None:
        return False
    unit = counted.group("unit")
    try:
        RelativePeriod(period=_UNIT[unit], n=count * _MONTHS_PER.get(unit, 1))
    except InvalidValueError:
        return True
    return False


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

# Words that name a window, whether or not this file can read the one they name. «Llevo gastado
# desde Navidad» and «… en la semana en curso» each name a window nothing here reads; a default in
# its place — the year so far, the last 30 days — would be a different window presented as the one
# asked about, so a sentence carrying any of these goes to the model as it always did. A digit is a
# date or a year. Generous on purpose — a word here that was not a window costs one question back,
# as before.
_A_WINDOW_IS_NAMED: Final = re.compile(
    r"\d|\b(?:desde|hasta|hace|durante|dias?|semanas?|finde|mes|meses|anos?|trimestres?"
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


def has_compared_windows(sentence: str, *, today: date) -> bool:
    """Whether a clause states a complete supported pair of comparison windows."""
    return any(
        pair is not None
        for pair in (
            compared_date_ranges(sentence, today=today),
            compared_calendar_months(sentence, today=today),
            compared_calendar_years(sentence, today=today),
            compared_natural_periods(sentence, today=today),
        )
    )


def has_complete_window(sentence: str, *, today: date) -> bool:
    """Whether a clause states its one window or a complete pair of comparison windows."""
    return window_in(sentence, today=today) is not None or has_compared_windows(
        sentence, today=today
    )


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
    if _SO_FAR.search(folded) and not _A_WINDOW_IS_NAMED.search(folded):
        return RelativePeriod(period=Period.THIS_YEAR)
    return None


def default_window_in(sentence: str, *, today: date | None = None) -> Window | None:
    """The window `window_in` answers with, when it is one of the two product defaults rather than a
    window the sentence states; None otherwise.

    For the caller that must tell them apart: a planner asking for the window of «¿cuánto he
    gastado últimamente?» asks what the product has already decided, and that question is answered
    from here instead of being put to the person. A window the sentence states is another matter —
    a model that asked about one saw something this vocabulary did not, and its question stands.
    """
    window = window_in(sentence, today=today)
    if window is None or window != _a_default(sentence, spanish.folded(sentence)):
        return None
    return window


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


# --- B-4 (Wave 3 hardening): a season is a question with its months in it ----------------------
#
# `window_in` above keeps refusing to guess "este verano" -- that refusal is right, and this file's
# own docstring already says why: a miss falls through and costs one turn, a wrong guess costs a
# figure about the wrong months presented as an answer. What was missing is what the refusal
# *becomes*. §11's own opening sentence, «¿En qué he gastado más este verano?», reached the planner
# with no signal that "este verano" was never read, and the window it ran with was whatever was
# already on screen -- a silent substitution, not a refusal. `season_ask` turns the same refusal
# into an honest question naming the months a reading would take, with one suggestion chip per
# reading a holder might have meant.
_SEASON_MONTHS: Final[dict[str, tuple[int, int, int]]] = {
    "primavera": (3, 4, 5),
    "verano": (6, 7, 8),
    "otono": (9, 10, 11),
    "invierno": (12, 1, 2),
}

_SEASON: Final = re.compile(r"\b(primavera|verano|otono|invierno)\b")

# "Este verano"/"este invierno"/"este otoño" (masculine) and "esta primavera" (feminine) both name
# the reading anchored on the current year; the pattern reads either article so the season word
# alone decides which season it is.
_THIS_SEASON: Final = re.compile(r"\best[ae]\s+(primavera|verano|otono|invierno)\b")
_LAST_SEASON: Final = re.compile(r"\b(primavera|verano|otono|invierno)\s+pasad[ao]\b")


def _season_months(season: str, year: int) -> tuple[str, str, tuple[int, int, int]]:
    months = _SEASON_MONTHS[season]
    named = ", ".join(_MONTHS[month - 1] for month in months[:-1])
    reading = f"{named} y {_MONTHS[months[-1] - 1]} de {year}"
    # F-H2 (Wave 3 hardening, fix round): the reading above is what the *question* reads aloud,
    # and it is not what the chip offers back -- a comma list is exactly the shape `_A_MONTH`
    # cannot read (it matches only the last named month), so a holder answering with the chip
    # verbatim used to get a single, silently wrong month back. The chip is a range phrase instead,
    # read by `_a_month_range` below.
    chip = f"de {_MONTHS[months[0] - 1]} a {_MONTHS[months[-1] - 1]} de {year}"
    return reading, chip, months


def season_ask(sentence: str, *, today: date) -> tuple[str, tuple[str, ...]] | None:
    """The honest question a season deserves, or `None` when the sentence names no season.

    One reading when the sentence itself picks a year ("este verano" is `today.year`, "el verano
    pasado" is `today.year - 1`); two when it does not ("en primavera" alone), offered current year
    first because that is what every other bare period in this file resolves to when a holder does
    not say otherwise (`window_in`'s own "the last time it happened, counting the one under way").

    Never a guess: nothing here reads `window_in`'s vocabulary or touches a plan. The caller answers
    with this question and these chips and asks the ledger nothing -- but the chips themselves are a
    window `window_in` can read back on the next turn (`_a_month_range` below), which is what closes
    the round trip: the caller opens a `FinancialClarification` awaiting `Slot.WINDOW`, and the
    ordinary reply-to-a-clarification path resolves it exactly like any other window answer would.
    """
    folded = spanish.folded(sentence)
    match = _SEASON.search(folded)
    if match is None:
        return None
    season = match.group(1)
    years: tuple[int, ...]
    if _THIS_SEASON.search(folded):
        years = (today.year,)
    elif _LAST_SEASON.search(folded):
        years = (today.year - 1,)
    else:
        years = (today.year, today.year - 1)
    pairs = [_season_months(season, year)[:2] for year in years]
    readings = [reading for reading, _chip in pairs]
    chips = tuple(chip[0].upper() + chip[1:] for _reading, chip in pairs)
    question = "¿Te refieres a " + " o a ".join(readings) + "?"
    return question, chips


# F-4 (fix wave, D-H-7/Important 4): §11's own opening sentence, «¿En qué he gastado más este
# verano?», is a breakdown by category ("en qué" + a superlative), not a total -- the round trip
# B-4/F-H2 closed only settles the *window*; the draft `season_ask`'s caller opens still defaulted
# to `GroupBy.NONE`, so the resolved plan drew a single number for a question that asked which
# category. Scoped to sentences `season_ask` has already matched (a season word is present), so
# this needs only the one distinction its own docstring already draws: "en qué ... más" is a
# ranking, "cuánto" is a total.
_BREAKDOWN_SHAPE: Final = re.compile(r"\ben\s+qu[ée]\b.{0,60}\bm[áa]s\b")


def season_asks_a_breakdown(sentence: str) -> bool:
    """Whether this season question asks "en qué ... más" -- a ranking by category -- rather than
    a total. Called only on a sentence `season_ask` has already claimed."""
    return bool(_BREAKDOWN_SHAPE.search(spanish.folded(sentence)))


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
