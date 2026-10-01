"""Why a chart looks the way it does, said in Spanish, from the sealed context alone.

Design §7 (as rewritten by Task 1, Wave 4.5) rules out deriving this from `decision` or
`source_plan`: both are turn-local (§4), and the orchestrator keeps no conversation store to
resurrect either after a round trip (§13). An explanation asked on a later turn -- «¿Por qué este
gráfico?» typed after the chart has already scrolled by -- has to come from what actually crossed
the wire and came back: `spec`, `profile`, `labels`, `ranked`, `window`, `source`,
`fallback_reason` and `group_by`. `explain_chart` reads exactly those and nothing else.

**What this never says**: an amount, a total, a percentage, a rule name, a provider error, or the
proposal the verifier discarded. §9's invariant -- "zero explanations of a discarded spec" --
holds here too: this describes the chart finally accepted, never the one a model wanted. The
evidence (movement) count does not survive the wire either, so it is never claimed.

**Why the noun comes from `group_by` first, the drawn labels second, never the dimension field's
name (R11).** Every `FAMILY_DATASETS` producer names its dimension field literally `"dimension"`
(Task 2's report), so a name-keyed noun was always the generic «grupo/grupos» -- it explained
nothing. The producers name a field by its *role* (`"dimension"`, `"measure"`, `"series"`), never
by what it holds. Task 2 (Wave 3 hardening, U-5) closed the gap this paragraph used to name here:
`group_by` now travels sealed, so a WEEK-grouped chart says «semanas» rather than misreading its
bucket-Monday labels as DAY, and a merchant/counterparty/account chart gets its own noun instead of
the generic one. `group_by` is `None` for the four plan-less producers (a comparison, a ratio,
...); those fall back to the values the chart drew, i.e. `context.labels` -- free strings such as
"Netflix" that do not say what kind of thing they are, so the fallback still reads «grupos» there,
same as before this task.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Final, get_args

from finguard_orchestrator.domain.enums import GroupBy, Metric
from finguard_orchestrator.domain.models.chart import (
    ChartSpec,
    Mark,
    VisualContext,
    VisualField,
    VisualProfile,
    VisualSource,
)
from finguard_orchestrator.domain.services import category_words, visual_reference
from finguard_orchestrator.domain.services.answer_writer import _MONTHS

# Wave 4.5, Task 4: `asks_why`'s grammar reuses `interpret_visual`'s own vocabulary rather than a
# second copy of it -- `_FINANCIAL` (a sentence naming a period, a month or a category is a drill
# or a financial question, never this) and `fold`/`TARGET` (the same casefold-and-strip-diacritics
# normalisation, and the same ordinal-chart vocabulary `interpret_visual` resolves).
from finguard_orchestrator.domain.services.visual_interpreter import _FINANCIAL, TARGET, fold
from finguard_orchestrator.domain.services.visual_profile import MAX_DRAWN_LABELS

# The generic fallback -- an empty label tuple, a mix of shapes, or labels that match none of the
# closed shapes below (merchant names, counterparty names, account names, weekday abbreviations,
# "Parte"/"Resto", ...).
_GENERIC_NOUN: Final[tuple[str, str, str]] = ("grupo", "grupos", "un grupo")

# `category_words.names()` is the Spanish word a *person* reads ("supermercado", "restaurantes",
# ...) -- what `answer_writer._group_name` substitutes into spoken text. A chart's `labels` are
# never that: `visual_profile.drawn_labels` carries `row.dimension` straight through with no
# translation, so a CATEGORY-grouped chart's labels are the ledger's own keys ("GROCERIES",
# "RESTAURANTS", ...), exactly the set `category_words.keys()` closes over (D-4.5-4, fix wave:
# was `category_words._NAMES`, read across the module boundary). The public `names()` would never
# match a real label -- stated here rather than silently swapped, and repeated in the Task 2b
# report as a brief/code mismatch worth a second look.
_CATEGORY_KEYS: Final[frozenset[str]] = frozenset(category_words.keys())


def _is_iso_date(label: str) -> bool:
    try:
        date.fromisoformat(label)
    except ValueError:
        return False
    return True


# Task 2 (Wave 3 hardening, U-5): the plan's own `group_by`, when the chart has one, decides the
# noun outright -- no label-shape guessing needed, and the one place that guessing could not
# distinguish (WEEK's bucket-Monday key has the identical `YYYY-MM-DD` shape a DAY key already
# has, the gap `_nouns_of`'s own docstring used to name) is closed for good, not narrowed. `NONE`
# is deliberately absent: an ungrouped chart falls through to the label-shape guess below, same as
# a chart with no `group_by` at all (a comparison, a ratio, a plan-less producer).
_NOUNS_BY_GROUP: Final[dict[GroupBy, tuple[str, str, str]]] = {
    GroupBy.DAY: ("día", "días", "un día"),
    GroupBy.WEEK: ("semana", "semanas", "una semana"),
    GroupBy.MONTH: ("mes", "meses", "un mes"),
    GroupBy.CATEGORY: ("categoría", "categorías", "una categoría"),
    GroupBy.MERCHANT: ("comercio", "comercios", "un comercio"),
    GroupBy.COUNTERPARTY: ("contraparte", "contrapartes", "una contraparte"),
    GroupBy.ACCOUNT: ("cuenta", "cuentas", "una cuenta"),
}


def _nouns_of(group_by: GroupBy | None, labels: tuple[str, ...]) -> tuple[str, str, str]:
    """The noun triple the chart earns: singular, plural, indefinite.

    `group_by` decides first, when the chart has one -- the plan's own grouping is stronger
    evidence than a guess from the labels' shape, and it is the only way to tell WEEK from DAY at
    all (see `_NOUNS_BY_GROUP`'s own comment). Labels are the fallback: `group_by` is `None` for
    the four plan-less producers (a comparison, a ratio, ...) and for `GroupBy.NONE` alike, and the
    guess below is all either has.

    All-or-nothing on the label guess: every label in the tuple must agree on the same shape, or
    the tuple reads as a mix and gets the generic noun -- one merchant name next to two month
    labels is not "two months and a merchant", it is a chart this module cannot name precisely.
    Checked in this order -- month, then ISO date, then category key -- because the three never
    collide: `visual_reference.parsed_month` demands a `YYYY-MM` shape with `01`-`12`,
    `date.fromisoformat` rejects that same string only when the day component is missing (it is),
    and a category key such as `RESTAURANTS` fails both.
    """
    if group_by is not None and group_by in _NOUNS_BY_GROUP:
        return _NOUNS_BY_GROUP[group_by]
    if not labels:
        return _GENERIC_NOUN
    if all(visual_reference.parsed_month(label) is not None for label in labels):
        return ("mes", "meses", "un mes")
    if all(_is_iso_date(label) for label in labels):
        return ("día", "días", "un día")
    if all(label in _CATEGORY_KEYS for label in labels):
        return ("categoría", "categorías", "una categoría")
    return _GENERIC_NOUN


# A-7/C-2/U-3 (Wave 3 hardening): `profile.measure_metric()` is `plan_metric or metric` -- the
# plan's own `Metric` when one survived the wire (Task 2 made it), the dataset's shape marker
# otherwise. This module used to close over six *shape* markers only, on the premise that no
# producer ever set `plan_metric` -- false on the live path: `_decided_visual` passes
# `plan_metric=context.metric` on every turn with a plan in view, so a COUNT/AVERAGE/
# PERIOD_AVERAGE plan reaches here as itself, not as the dataset's shape. Closed over the real
# `Metric` enum (never `EXPENSE`/`INCOME` -- those live only on a `FinancialPlan.direction`, which
# this module never sees, R1's whole point) plus the two shape markers no `Metric` member names:
# `RATIO_SHARE` and `COMPARISON` (a comparison whose `plan_metric` did not survive -- an ungrouped
# two-total comparison with no plan in view -- assumed a comparison of amounts, same as before).
# U-2: `RECURRENCE`'s points plot `value_minor`, an amount, not a count -- "número de movimientos"
# was always the wrong word for it, unrelated to the `plan_metric` gap C-2 fixes.
_MEASURE_WORDS: Final[dict[str, str]] = {
    "SUM": "importe",
    "CUMULATIVE": "importe",
    "COMPARISON": "importe",
    "COUNT": "número de movimientos",
    "FREQUENCY": "número de movimientos",
    "AVERAGE": "importe medio",
    "PERIOD_AVERAGE": "importe medio",
    "RECURRENCE": "importe",
    "RATIO_SHARE": "peso",
}
assert set(_MEASURE_WORDS) == {m.value for m in Metric} | {"RATIO_SHARE", "COMPARISON"}


def _measure_word(profile: VisualProfile) -> str:
    # A metric this module has never seen falls back to "importe" at runtime rather than raising --
    # the import-time assertion above is where a new metric marker gets caught.
    return _MEASURE_WORDS.get(profile.measure_metric(), "importe")


# F-1 (fix wave): the noun triple's own `indef` ("una categoría" vs "un mes") is what already says
# which gender it is -- `_scope_summary` (:315) reads it this way already. These two helpers are
# the same agreement, reused rather than a second hardcoded table: `_los_las` for the "los/las
# {plural}" article, `_cada` for "cada uno/cada una".
def _los_las(indef: str) -> str:
    return "las" if indef.startswith("una") else "los"


def _cada(indef: str) -> str:
    return "cada una" if indef.startswith("una") else "cada uno"


def _bar_reason(temporal: bool, medida: str, singular: str, plural: str, indef: str) -> str:
    if temporal:
        return f"Barras por {plural}: ves cuánto suma {_cada(indef)}."
    return f"Barras: comparas {plural} entre sí."


def _line_reason(temporal: bool, medida: str, singular: str, plural: str, indef: str) -> str:
    return f"Líneas: ves cómo cambia el {medida} a lo largo de {_los_las(indef)} {plural}."


def _area_reason(temporal: bool, medida: str, singular: str, plural: str, indef: str) -> str:
    return f"Área: ves cómo se acumula el {medida} a lo largo de {_los_las(indef)} {plural}."


def _point_reason(temporal: bool, medida: str, singular: str, plural: str, indef: str) -> str:
    return f"Puntos: {_cada(indef)} es {indef} y su {medida}."


def _donut_reason(temporal: bool, medida: str, singular: str, plural: str, indef: str) -> str:
    return f"Reparto: cada porción es el peso de {indef} en el total."


# Closed over every `Mark` -- asserted below so a mark added later without a reasoner here fails
# loudly at import instead of a `KeyError` deep inside `explain_chart`.
_MARK_REASONS: Final[dict[Mark, Callable[[bool, str, str, str, str], str]]] = {
    "bar": _bar_reason,
    "line": _line_reason,
    "area": _area_reason,
    "point": _point_reason,
    "donut": _donut_reason,
}
assert set(_MARK_REASONS) == set(get_args(Mark))

# Closed over `VisualSource` -- the deterministic line below is the one the brief and design §7
# both give for "no fallback_reason"; the fallback-reason line is a second, non-tabular override
# applied in `_provenance`, not a fourth dict entry (there is no fourth source).
_PROVENANCE_LINES: Final[dict[str, str]] = {
    "llm": "Propuesto por el asistente y comprobado por las reglas de FinGuard.",
    "deterministic": "Elegido por las reglas de FinGuard.",
    "patch": "Ajustado a petición tuya sobre el gráfico anterior.",
}
assert set(_PROVENANCE_LINES) == set(get_args(VisualSource))

_DETERMINISTIC_FALLBACK_LINE: Final = "Visualización segura seleccionada automáticamente."


def _provenance(context: VisualContext) -> str | None:
    source = context.source
    if source is None:
        return None
    if source == "deterministic" and context.fallback_reason is not None:
        return _DETERMINISTIC_FALLBACK_LINE
    return _PROVENANCE_LINES[source]


def _date_words(moment: date, *, with_year: bool) -> str:
    """«1 de junio de 2026», or «1 de junio» when `with_year` is False.

    There is no day-level Spanish date formatter in `domain/` (grep confirms it) -- only
    `answer_writer.month_name`/`month_group_name`, both month-only. `_MONTHS` is imported from
    there rather than copied a second time.
    """
    base = f"{moment.day} de {_MONTHS[moment.month - 1]}"
    return f"{base} de {moment.year}" if with_year else base


def _spec_dimension(spec: ChartSpec, profile: VisualProfile) -> VisualField | None:
    """The dimension-role field the spec actually draws, not merely the first one in the profile.

    `visual_profile_of` emits two dimension-role fields for RECURRENCE: the nominal `"dimension"`
    placeholder and the temporal `"occurred_at"`, which is the chart's real x-axis (Task 2 review
    I-1). Picking `next(iter(profile.role("dimension")), None)` always found the placeholder first
    and the temporal branch of `_ordering_reason` never fired for a recurring-charge chart. This
    instead walks the spec's own encodings -- in the order the spec lists them -- and returns the
    first one naming a dimension-role field, so whichever field the chart puts a channel on (x, y
    or color; RECURRENCE lists its temporal `x=occurred_at` before its nominal `y=dimension`) wins.
    A spec with no such encoding (there is none among today's families) falls back to a temporal
    dimension field, then to the first one, matching the old behaviour when there is only one.
    """
    dimension_fields = profile.role("dimension")
    if not dimension_fields:
        return None
    by_name = {field.name: field for field in dimension_fields}
    for encoding in spec.encodings:
        if encoding.field in by_name:
            return by_name[encoding.field]
    for field in dimension_fields:
        if field.type == "temporal":
            return field
    return dimension_fields[0]


def _encoding_sort(spec: ChartSpec, field_name: str | None) -> tuple[str | None, str | None]:
    """The dimension encoding's own sort, or the spec's, when the field carries none.

    Not restricted to `x`/`y`: a dimension can sit on `color` (RATIO_SHARE) with no axis encoding
    at all, and this still needs an answer for it (`None`, via the fallback).
    """
    if field_name is not None:
        for encoding in spec.encodings:
            if encoding.field == field_name and encoding.sort is not None:
                return encoding.sort.mode, encoding.sort.field
    return spec.sort, spec.sort_field


def _ordering_reason(
    spec: ChartSpec,
    profile: VisualProfile,
    dimension: VisualField | None,
    medida: str,
    plural: str,
) -> str | None:
    mode, sort_field = _encoding_sort(spec, dimension.name if dimension is not None else None)
    measures = profile.role("measure")
    measure_name = measures[0].name if measures else None

    base: str | None
    if mode in {"descending", "ascending"} and (sort_field is None or sort_field == measure_name):
        # `ranked` (data ties) is deliberately not read here: the order stated is what the spec
        # says it draws, whether or not the DATA happens to make every band distinct.
        base = (
            f"Orden: de mayor a menor {medida}."
            if mode == "descending"
            else f"Orden: de menor a mayor {medida}."
        )
    elif dimension is not None and dimension.type == "temporal":
        base = "Orden: cronológico."
    else:
        base = None

    if spec.limit is not None:
        suffix = f"Solo {spec.limit} {plural}."
        base = f"{base} {suffix}" if base else suffix
    return base


def _scope_summary(context: VisualContext, singular: str, plural: str, indef: str) -> str:
    # I-2 (fix wave, D-4.5-8), updated by Task 2 (Wave 3 hardening, U-4/C-5): `profile.row_count`
    # now crosses the wire too, but it is still not what this counts -- it is not authority (a
    # client-sent number, read only by `measure_metric()`'s sibling `plan_metric`, never by a rule
    # that decides), and `context.labels` is what a holder can actually resolve a reference
    # against. The count comes from `len(labels)` so the sentence names exactly what a later turn
    # can point at. `labels` is capped at
    # `MAX_DRAWN_LABELS`, so a full label list means "at least this many, we stopped counting" --
    # stating that as a bare number would read as a total, so the count is omitted instead (chosen
    # over "al menos N": an omitted clause cannot be misread as a bound stated as fact). An empty
    # label list (no chart drew any group -- shouldn't normally happen, but nothing here should
    # guess) omits the count for the same reason. The noun-with-article phrasing
    # (`f"{article} {plural}"`) is the one reading that stays grammatical whether or not a window
    # follows -- "Datos: los grupos." and "Datos: los grupos entre el ... y el ..." both read as
    # sentences, where "Datos: entre el ... y el ..." (no noun at all) would not survive the
    # no-window case. The article agrees in gender with the noun (`las categorías`, not `los
    # categorías`): `_nouns_of`'s own `indef` ("una categoría" vs "un mes") is what already says
    # which gender the triple is, so it is read here rather than a second table.
    profile = context.profile
    count = len(context.labels)
    omit_count = not context.labels or count == MAX_DRAWN_LABELS
    article = "las" if indef.startswith("una") else "los"
    noun_phrase = (
        f"{article} {plural}" if omit_count else f"{count} {singular if count == 1 else plural}"
    )
    if context.window is not None:
        start, end = context.window.start, context.window.end
        start_words = _date_words(start, with_year=start.year != end.year)
        end_words = _date_words(end, with_year=True)
        base = f"Datos: {noun_phrase} entre el {start_words} y el {end_words}."
    else:
        base = f"Datos: {noun_phrase}."
    if profile.plan_limited:
        base = f"{base} (los primeros que pediste)"
    return base


@dataclass(frozen=True)
class VisualExplanation:
    chart_reason: str
    ordering_reason: str | None
    scope_summary: str
    provenance: str | None

    def as_text(self) -> str:
        parts = (self.chart_reason, self.ordering_reason, self.scope_summary, self.provenance)
        return " ".join(part for part in parts if part is not None)


def explain_chart(context: VisualContext) -> VisualExplanation:
    """A pure function of `context` -- no plan, no decision, no wire, no controller."""
    profile = context.profile
    spec = context.spec
    dimension = _spec_dimension(spec, profile)
    singular, plural, indef = _nouns_of(context.group_by, context.labels)
    medida = _measure_word(profile)

    if profile.comparison:
        chart_reason = f"Dos periodos lado a lado, para que compares el mismo {medida} en cada uno."
    else:
        chart_reason = _MARK_REASONS[spec.mark](profile.temporal(), medida, singular, plural, indef)

    ordering_reason = _ordering_reason(spec, profile, dimension, medida, plural)
    scope_summary = _scope_summary(context, singular, plural, indef)
    provenance = _provenance(context)

    return VisualExplanation(chart_reason, ordering_reason, scope_summary, provenance)


@dataclass(frozen=True)
class WhyAsked:
    """A holder asked «¿por qué?» about a chart. `target` is the `TARGET` group's own match,
    folded, exactly as `interpret_visual` would have found it -- `None` when the sentence names no
    ordinal (the last chart, the ordinary case). `resolve_target` (`visual_interpreter`) turns this
    into a real `chart_id` the same way `interpret_visual` does for a restyle."""

    target: str | None


# R5/R6: the recogniser claims a turn, never a chart. It knows nothing about `contexts` -- whether
# there is one to explain, and which, is the controller's question (`was.visual_contexts`,
# `resolve_target`), asked only after this abstains or claims. Anchored to the whole sentence, the
# same principle as `visual_interpreter._VISUAL_ALL`'s own comment: a longer sentence carries a
# real financial question or a drill and is not this, however it starts.
_WHY = re.compile(
    r"por que\s+(?:"
    r"(?:este|ese|el) grafico"
    r"|(?:(?:el )?(?:ultimo|last|primer[oa]?|segund[oa]?) grafico)"
    r"|(?:en |con )?(?:barras|lineas|linea|area|puntos|donut|anillo|circular)"
    # A-9 (Wave 3 hardening): "¿Por qué has elegido/puesto/usado barras?" names the same mark
    # words above, behind the verb a holder actually uses to ask why the chart looks that way
    # rather than to change it -- `interpret_visual`'s own `_VISUAL` grammar reads the bare mark
    # word alone and would otherwise restyle a question.
    r"|(?:has |he |habeis )?(?:elegido|puesto|usado|escogido) "
    r"(?:barras?|lineas?|areas?|puntos?|donut|anillo|circular)"
    r"|(?:este|ese|el) orden"
    r"|(?:asi|de esta forma) ordenado"
    r"|(?:me )?lo has puesto asi"
    r"|esta asi"
    r")"
    # B-6 (Wave 3 hardening): design §3.3/§7's own explanation wording -- "explícame el gráfico",
    # "cuéntame el gráfico", "¿qué significa este gráfico?" -- none of it built on "por qué" at
    # all, so it could not live inside the group above.
    r"|explica(?:me)?(?: el grafico)?"
    r"|cuentame(?: el grafico)?"
    r"|que significa (?:este|el) grafico"
    r"$"
)


def asks_why(sentence: str) -> WhyAsked | None:
    """«¿Por qué este gráfico?», typed or spoken, read the same way `interpret_visual` reads a
    restyle: fold, strip the leading/trailing punctuation a question mark leaves behind, then
    match the closed grammar above. A sentence naming a `_FINANCIAL` word is a drill or a
    financial question dressed as "por qué" -- «¿Por qué gasté tanto en junio?» -- and is never
    this, checked first for the same reason `interpret_visual` checks it first. «¿Por qué?» alone,
    with nothing named to explain, is not claimed either: it goes wherever it went before this
    recogniser existed.
    """
    folded = fold(sentence).strip(" ¿?¡!.,")
    if _FINANCIAL.search(folded):
        return None
    if not _WHY.fullmatch(folded):
        return None
    match = TARGET.search(folded)
    return WhyAsked(target=match.group(0) if match else None)
