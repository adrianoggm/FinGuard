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

from finguard_orchestrator.domain.models.chart import VisualContext
from finguard_orchestrator.domain.models.visual_reference import (
    Ambiguous,
    NoMatch,
    Reference,
    ReferenceKind,
    Resolved,
)
from finguard_orchestrator.domain.services import answer_writer, category_words, spanish

# The input is `fold()`ed before this searches it, so an accented alternative (`qué`, an accented
# `ensename`) never matches and none is listed here. `por que` is qualified to the shapes §3.3
# actually names — unqualified, it read «¿Por qué no me funciona la app?» as a drill-down just as
# readily as «¿Por qué agosto es tan alto?» — and neither `explica` nor a bare `responsable`
# survived for the same reason: each claimed an ordinary question that named none of this turn's
# chart.
DRILL_DOWN: Final = re.compile(
    r"\bpor que\b.*\b(?:tan|tanto|alto|alta|bajo|baja|sube|subio|subido|pico)\b|"
    r"\bque movimientos\b|\bque comercio\b|"
    r"\b(?:ensename|muestrame) (?:los|las) (?:movimientos|compras|operaciones)\b"
)

# YYYY-MM, the same shape `answer_writer.month_group_name` parses -- a full match, not a search,
# so a category label that merely contains four digits and a dash is never mistaken for one.
_MONTH_LABEL = re.compile(r"(\d{4})-(\d{2})")

# The word-boundary rule `category_words._matches` draws, over already-folded text: a bare
# alphanumeric run cannot straddle into the next word, but the pattern itself carries no accents
# and no case to fold, because the haystack it searches is folded once by the caller.
_WORD = "(?<![a-z0-9]){}(?![a-z0-9])"

# B-6 (Wave 3 hardening): design §3.3's own drill words -- "¿por qué tanto?", "¿qué comercio es
# responsable?", "¿de dónde sale?" -- name no month, no category and none of the "más alto/grande/
# caro" vocabulary above; each is an implicit "the standout bar" exactly the way "el pico" already
# is. "Tanto" is the bare word, not "tan" (kept out of `DRILL_DOWN`'s own "tan alto" reading, which
# `resolve_reference`'s existing "¿Por qué es tan alto?" -> `NoMatch()` still needs: naming no
# specific bar and no `_TOP` word at all stays unresolved).
_TOP = re.compile(
    r"\bmas\s+(?:alto|alta|grande|caro|cara|elevado|elevada)\b|\b(?:el|ese|este)\s+pico\b"
    r"|\b(?:el|la)\s+mayor\b|\btanto\b|\bresponsable\b|\bde\s+donde\s+sale\b"
)
_BOTTOM = re.compile(r"\bmas\s+(?:bajo|baja|pequeno|pequena|barato|barata)\b|\b(?:el|la)\s+menor\b")


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


def _word_present(word: str, folded_sentence: str) -> bool:
    return re.search(_WORD.format(re.escape(word)), folded_sentence) is not None


def _month_matches(context_labels: tuple[str, ...], folded: str) -> list[str]:
    """The month labels a sentence names (R7): the exact (full-name) matches if any, else the
    loose (bare-month) ones -- so «agosto de 2025» picks one of two Augusts and a bare «agosto»
    over two Augusts is ambiguous."""
    exact: list[str] = []
    loose: list[str] = []
    for label in context_labels:
        parsed = parsed_month(label)
        if parsed is None:
            continue
        year, month = parsed
        full = spanish.folded(answer_writer.month_name(year, month))
        bare = full.split(" ", 1)[0]
        if _word_present(full, folded):
            exact.append(label)
        elif _word_present(bare, folded):
            loose.append(label)
    return exact if exact else loose


def resolve_reference(sentence: str, context: VisualContext) -> Reference:
    """Which bar of `context`'s chart `sentence` means: one label, an ambiguity, or nothing."""
    if not context.labels:
        return NoMatch()

    folded = spanish.folded(sentence)

    month_matches = _month_matches(context.labels, folded)
    named = category_words.precisely_named_in(sentence)
    category_matches = [label for label in context.labels if label in named]

    combined = [
        label for label in context.labels if label in month_matches or label in category_matches
    ]

    if len(combined) == 1:
        label = combined[0]
        kind: ReferenceKind = "month" if parsed_month(label) is not None else "category"
        return Resolved(label, kind)
    if len(combined) >= 2:
        return Ambiguous(tuple(combined))

    top = _TOP.search(folded) is not None
    bottom = _BOTTOM.search(folded) is not None
    if top and bottom:
        return NoMatch()
    if top or bottom:
        if context.ranked:
            return (
                Resolved(context.labels[0], "rank") if top else Resolved(context.labels[-1], "rank")
            )
        if len(context.labels) >= 2:
            return Ambiguous(context.labels)
        # A single unranked label falls through to `NoMatch`; `drawn_labels` never produces one.

    return NoMatch()
