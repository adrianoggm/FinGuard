"""Which bar of the chart in context a sentence means -- one, several (ask), or none.

Deterministic, over `VisualContext.labels` only (R15): `series_labels` -- the legend, a comparison's
window or a recurrence's cadence -- are not resolved in 4.3. A sentence about one of those still
goes to the planner as it does today; this module only ever answers about the *dimension* a chart
already drew.

Trust is not this module's business: the caller (Tasks 3 and 4) decides whether `context.trusted`
gates a resolved reference at all. This reader only reads `context.labels` and `context.ranked`.

Order of reading, each more specific than the last: a named month (R7) or category beats a
superlative, because a name is more specific than a shape; a superlative is only read once nothing
was named. Weekdays are deliberately NOT resolved (R14, YAGNI) -- a frequency chart's «¿por qué los
martes...?» is `NoMatch` and goes to the planner exactly as it does today.
"""

import re
from typing import Final

# The input is `fold()`ed before this searches it, so an accented alternative (`qué`, an accented
# `ensename`) never matches and none is listed here. `por que` is qualified to the shapes §3.3
# actually names — unqualified, it read «¿Por qué no me funciona la app?» as a drill-down just as
# readily as «¿Por qué agosto es tan alto?» — and neither `explica` nor a bare `responsable`
# survived for the same reason: each claimed an ordinary question that named none of this turn's
# chart.
DRILL_DOWN: Final = re.compile(
    r"\bpor que\b.*\b(?:tan|tanto|alto|alta|bajo|baja|sube|subio|subido|pico)\b|"
    r"\bque movimientos\b|\bque comercio\b|"
    r"\b(?:ensename|muestrame) (?:los|las) (?:movimientos|compras|operaciones)\b|"
    # Item 5 (Wave 3 hardening, T8b): "¿cuál es el mayor?" over the chart in view -- the bare
    # superlative `_TOP` already reads for a *resolved* reference, asked here as its own question
    # rather than named at the tail of one. Qualified to "cual es" so it never fires on an
    # unrelated "la mayor parte de mis gastos" -- only the shape of the actual question.
    r"\bcual es (?:el|la) mayor\b"
)

# YYYY-MM, the same shape `answer_writer.month_group_name` parses -- a full match, not a search,
# so a category label that merely contains four digits and a dash is never mistaken for one.
_MONTH_LABEL = re.compile(r"(\d{4})-(\d{2})")

# The word-boundary rule `category_words._matches` draws, over already-folded text: a bare
# alphanumeric run cannot straddle into the next word, but the pattern itself carries no accents
# and no case to fold, because the haystack it searches is folded once by the caller.
_WORD = "(?<![a-z0-9]){}(?![a-z0-9])"


def parsed_month(label: str) -> tuple[int, int] | None:
    """`label` as a calendar month, or `None` when it is not `YYYY-MM` with `MM` in 01-12.

    Public: `read_drill_down` (`application/queries/readers/drill_down.py`) needs this exact
    predicate to tell a month-shaped label from a category one, and a second copy of it there used
    to be the one place that reader re-derived an answer this module already computed.
    `_MONTH_LABEL` itself stays private -- nothing outside this module needs the regex, only what
    it decides.
    """
    match = _MONTH_LABEL.fullmatch(label)
    if match is None:
        return None
    month = int(match.group(2))
    if not 1 <= month <= 12:
        return None
    year = int(match.group(1))
    # C-4 (Wave 3 hardening): the month was already bounded to 01-12; the year never was. A
    # boundary or unbucketed ledger row can carry a group key like "0000-01", which parsed to
    # `(0, 1)` and made `window_words.whole_month(0, 1)` build `date(0, 1, 1)` -- an unhandled
    # `ValueError`, neither a `DomainError` nor a pydantic `ValidationError`, on the way to an
    # HTTP 500. `2000-2099` is not a new range: it is the bound every other year this service
    # reads already enforces (`window_words.py`'s own named-year, quarter and semester patterns
    # all match `20\d{2}`), applied here so a label the resolver accepts is always a month the
    # calendar -- and `whole_month` -- can build without raising.
    if not 2000 <= year <= 2099:
        return None
    return year, month
