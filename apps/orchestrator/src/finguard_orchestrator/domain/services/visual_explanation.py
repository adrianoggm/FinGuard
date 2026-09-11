"""Why a chart looks the way it does, said in Spanish, from the sealed context alone.

Design §7 (as rewritten by Task 1, Wave 4.5) rules out deriving this from `decision` or
`source_plan`: both are turn-local (§4), and the orchestrator keeps no conversation store to
resurrect either after a round trip (§13). An explanation asked on a later turn -- «¿Por qué este
gráfico?» typed after the chart has already scrolled by -- has to come from what actually crossed
the wire and came back: `spec`, `profile`, `labels`, `ranked`, `window`, `source` and
`fallback_reason`. `explain_chart` reads exactly those and nothing else.

**What this never says**: an amount, a total, a percentage, a rule name, a provider error, or the
proposal the verifier discarded. §9's invariant -- "zero explanations of a discarded spec" --
holds here too: this describes the chart finally accepted, never the one a model wanted. The
evidence (movement) count does not survive the wire either, so it is never claimed.

**Why the noun is keyed by the dimension field, not by a plan's `GroupBy`.** `spoken_presenter`'s
`_NOUNS` (the precedent for tone and rule order) is keyed by a `FinancialPlan.group_by`, because
`spoken_summary` runs inside the same turn that built the plan. This module has no plan to read at
all -- R1's whole premise is that `decision`/`source_plan` are gone by the time an explanation is
asked for -- so the only thing left to name the dimension is the `VisualField` itself,
`profile.role("dimension")[0]`, keyed by its *name*.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Final, get_args

from finguard_orchestrator.domain.models.chart import (
    ChartSpec,
    Mark,
    VisualContext,
    VisualField,
    VisualProfile,
    VisualSource,
)
from finguard_orchestrator.domain.services.answer_writer import _MONTHS

# The generic fallback, used whenever the dimension's own name (or its absence) names nothing more
# specific -- a comparison or ratio profile with no dimension field at all, or (see the report:
# every producer today names the dimension field literally "dimension", never "category"/"month"/
# "merchant") the ordinary case too.
_GENERIC_NOUN: Final[tuple[str, str, str]] = ("grupo", "grupos", "un grupo")

# Closed over the dimension field NAMES the seven families' *producers* could set (read from
# `FAMILY_DATASETS`/the visual-field literature: category, month, merchant, weekday, day, week,
# account, counterparty) plus the literal name `visual_profile_of` actually assigns today
# ("dimension") -- so the TEST that walks every family's real dimension name finds it here rather
# than falling through to the generic noun by accident. Unknown name -> `_GENERIC_NOUN`.
_NOUNS: Final[dict[str, tuple[str, str, str]]] = {
    "dimension": _GENERIC_NOUN,
    "category": ("categoría", "categorías", "una categoría"),
    "month": ("mes", "meses", "un mes"),
    "merchant": ("comercio", "comercios", "un comercio"),
    "weekday": ("día", "días", "un día"),
    "day": ("día", "días", "un día"),
    "week": ("semana", "semanas", "una semana"),
    "account": ("cuenta", "cuentas", "una cuenta"),
    "counterparty": ("contraparte", "contrapartes", "una contraparte"),
}


def _nouns(dimension: VisualField | None) -> tuple[str, str, str]:
    if dimension is None:
        return _GENERIC_NOUN
    return _NOUNS.get(dimension.name, _GENERIC_NOUN)


# What `profile.measure_metric()` actually returns across the seven `FAMILY_DATASETS` families
# (verified on the tree at dc9c436, no `plan_metric` set by any of them): `SUM`, `CUMULATIVE`,
# `FREQUENCY`, `RECURRENCE`, `RATIO_SHARE`, `COMPARISON` -- shape/measure markers, never
# `EXPENSE`/`INCOME` (those live only on a `FinancialPlan.direction`, which this module never
# sees -- R1's whole point). So the "EXPENSE-like -> gasto, INCOME-like -> ingreso" branches the
# brief describes are unreachable with today's producers; every value found maps to the "else"
# word, "movimiento", except `RATIO_SHARE`, which the brief calls out by name for "peso". Closed
# over exactly the values found, so a metric this module has never seen falls back to "movimiento"
# through `.get` rather than raising.
_MEDIDA_WORDS: Final[dict[str, str]] = {
    "SUM": "movimiento",
    "CUMULATIVE": "movimiento",
    "FREQUENCY": "movimiento",
    "RECURRENCE": "movimiento",
    "RATIO_SHARE": "peso",
    "COMPARISON": "movimiento",
}


def _medida(profile: VisualProfile) -> str:
    return _MEDIDA_WORDS.get(profile.measure_metric(), "movimiento")


def _bar_reason(temporal: bool, medida: str, singular: str, plural: str, indef: str) -> str:
    if temporal:
        return f"Barras por {plural}: ves cuánto suma cada uno."
    return f"Barras: comparas {plural} entre sí."


def _line_reason(temporal: bool, medida: str, singular: str, plural: str, indef: str) -> str:
    return f"Líneas: ves cómo cambia el {medida} a lo largo de los {plural}."


def _area_reason(temporal: bool, medida: str, singular: str, plural: str, indef: str) -> str:
    return f"Área: ves cómo se acumula el {medida} a lo largo de los {plural}."


def _point_reason(temporal: bool, medida: str, singular: str, plural: str, indef: str) -> str:
    return f"Puntos: cada uno es un {singular} y su {medida}."


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


def _scope_summary(context: VisualContext, singular: str, plural: str) -> str:
    profile = context.profile
    row_count = profile.row_count
    noun = singular if row_count == 1 else plural
    if context.window is not None:
        start, end = context.window.start, context.window.end
        start_words = _date_words(start, with_year=start.year != end.year)
        end_words = _date_words(end, with_year=True)
        base = f"Datos: {row_count} {noun} entre el {start_words} y el {end_words}."
    else:
        base = f"Datos: {row_count} {noun}."
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
    dimension = next(iter(profile.role("dimension")), None)
    singular, plural, indef = _nouns(dimension)
    medida = _medida(profile)

    if profile.comparison:
        chart_reason = f"Dos periodos lado a lado, para que compares el mismo {medida} en cada uno."
    else:
        chart_reason = _MARK_REASONS[spec.mark](profile.temporal(), medida, singular, plural, indef)

    ordering_reason = _ordering_reason(spec, profile, dimension, medida, plural)
    scope_summary = _scope_summary(context, singular, plural)
    provenance = _provenance(context)

    return VisualExplanation(chart_reason, ordering_reason, scope_summary, provenance)
