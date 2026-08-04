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

"El año pasado" is absent for a harder reason: the banking contract has no LAST_YEAR, so
there is no window to translate it into. Answering it as THIS_YEAR would be a figure about a
different year, which is the exact failure this file exists to avoid.

Longest phrase first, because "el mes pasado" contains "mes" and "este mes" does too.
Whichever phrase is longer is the one the person actually said.
"""

import re
from typing import Final

from finguard_orchestrator.domain.enums import Period
from finguard_orchestrator.domain.errors import InvalidValueError
from finguard_orchestrator.domain.services import spanish
from finguard_orchestrator.domain.value_objects.financial_plan import RelativePeriod

# Phrases that name a window on their own, longest first. Folded, so accents and case do not matter.
_NAMED: Final[tuple[tuple[str, Period], ...]] = (
    ("en lo que va de ano", Period.THIS_YEAR),
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

# What must never reach the phrases above. "El año pasado" ends in "el ano" and is a different year
# — one the banking contract cannot express at all — so the whole sentence is refused before any
# phrase is tried. Without this, widening "el ano" to THIS_YEAR would have quietly started answering
# about the wrong year.
_A_YEAR_WE_CANNOT_NAME: Final = re.compile(r"\bano\s+pasado\b|\bano\s+anterior\b")

# "los últimos 30 días", "últimos 3 meses", and the singular "el último mes" which is n=1.
_COUNTED: Final = re.compile(
    r"ultim[oa]s?\s+(?:(\d{1,3})\s+)?(dias?|meses|mes)\b",
    re.IGNORECASE,
)

_UNIT: Final[dict[str, Period]] = {
    "dia": Period.LAST_N_DAYS,
    "dias": Period.LAST_N_DAYS,
    "mes": Period.LAST_N_MONTHS,
    "meses": Period.LAST_N_MONTHS,
}


def window_in(sentence: str) -> RelativePeriod | None:
    """The window this sentence names, or None when it names none this vocabulary is sure about.

    None is the useful answer as often as a window is: it means "send this to the model", which is
    where a sentence this does not recognise belongs.
    """
    folded = spanish.folded(sentence)

    if _A_YEAR_WE_CANNOT_NAME.search(folded):
        # Before anything else, because "el año pasado" ends in a phrase this now recognises. The
        # contract has no LAST_YEAR, so the honest answer is that this vocabulary does not know the
        # window — not that it is the current year.
        return None

    counted = _COUNTED.search(folded)
    if counted:
        # No number means one: "el último mes" is a month back, not an unbounded period.
        count = int(counted.group(1)) if counted.group(1) else 1
        period = _UNIT[counted.group(2)]
        try:
            return RelativePeriod(period=period, n=count)
        except InvalidValueError:
            # `n` is bounded by the domain, so "los últimos 5000 días" is not a window this can
            # build. None sends it to the model, which is better than a reader that raises.
            return None

    for phrase, period in _NAMED:
        if re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", folded):
            return RelativePeriod(period=period)
    return None
