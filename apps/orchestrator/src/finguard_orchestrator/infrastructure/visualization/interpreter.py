"""Deterministic, value-free visual follow-up interpreter."""

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal, cast

from finguard_orchestrator.domain.models.chart import (
    EncodingPatch,
    VisualContext,
    merge_encoding_patch,
)


@dataclass(frozen=True)
class VisualIntent:
    patch: EncodingPatch | None
    target: str | None
    explanation: str | None = None


_FINANCIAL = re.compile(
    r"\b(?:por\s+(?:dias?|semanas?|meses?|trimestres?|anos?)|semanal|mensual|"
    r"enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|"
    r"diciembre|compar(?:a|ar)|restaurantes?|categorias?|gastos?|ingresos?)\b"
)
_VISUAL = re.compile(
    r"\b(?:barra|barras|linea|area|apil\w*|desapil\w*|ordena\w*|ascendente|descendente|"
    r"top\s*\d+|circular|donut|anillo)\b"
)
_VISUAL_STYLE = re.compile(
    r"\b(?:barra|barras|linea|area|apil\w*|desapil\w*|ordena\w*|ascendente|descendente|"
    r"circular|donut|anillo)\b"
)
_TARGET = re.compile(
    r"\b(?:(?:el\s+)?(?:ultimo|last)|(?:el\s+)?primer(?:o|a)?|"
    r"(?:el\s+)?segund(?:o|a)?)\s+grafic(?:o|a)\b"
)


def _fold(sentence: str) -> str:
    return "".join(
        character
        for character in unicodedata.normalize("NFKD", sentence.casefold())
        if not unicodedata.combining(character)
    )


def interpret_visual(
    sentence: str, contexts: tuple[VisualContext, ...], *, voice: bool = False
) -> VisualIntent | None:
    folded = _fold(sentence)
    financial = _FINANCIAL.search(folded)
    visual_signal = _VISUAL.search(folded)
    if financial:
        # In a financial sentence, "top N" is a query limit (for example "top 2 categories by
        # average ticket"), not a request to trim the already rendered rows. Only an actual style
        # instruction makes the turn mixed.
        if _VISUAL_STYLE.search(folded):
            return VisualIntent(
                None,
                None,
                "Has pedido cambiar los datos y el formato a la vez. Pide primero el cambio "
                "financiero y después el formato del gráfico en otro turno.",
            )
        return None
    if not visual_signal:
        return None
    if voice:
        return VisualIntent(None, None, "Para cambiar un gráfico, escríbelo en el chat.")
    if not contexts:
        return VisualIntent(None, None, "No hay ningún gráfico anterior que pueda cambiar.")
    target_match = _TARGET.search(folded)
    target = contexts[-1].chart_id
    if target_match:
        word = target_match.group(0).casefold()
        if "primer" in word:
            target = contexts[0].chart_id
        elif "segund" in word:
            if len(contexts) < 2:
                return VisualIntent(None, None, "No existe un segundo gráfico anterior.")
            target = contexts[1].chart_id
    mark = None
    if re.search(r"\b(?:barra|barras)\b", folded):
        mark = "bar"
    elif re.search(r"\blinea\b", folded):
        mark = "line"
    elif re.search(r"\barea\b", folded):
        mark = "area"
    elif re.search(r"\b(?:circular|donut|anillo)\b", folded):
        mark = "donut"
    stack = (
        "normalize"
        if "100%" in folded or "porcentaje" in folded
        else ("zero" if "apil" in folded else None)
    )
    if "desapil" in folded:
        stack = None
    sort = (
        "ascending"
        if re.search(r"\b(?:ascendente|menor\s+a\s+mayor)\b", folded)
        else (
            "descending"
            if re.search(r"\b(?:ordena\w*|descendente|mayor\s+a\s+menor)\b", folded)
            else None
        )
    )
    limit_match = re.search(r"\btop\s*(\d{1,3})\b", folded)
    limit = int(limit_match.group(1)) if limit_match else None
    if limit is not None and not 1 <= limit <= 100:
        return VisualIntent(None, target, "El top debe estar entre 1 y 100.")
    changed = frozenset(
        name
        for name, value in (("mark", mark), ("stack", stack), ("sort", sort), ("limit", limit))
        if value is not None
    )
    if "desapil" in folded:
        changed = changed | {"stack"}
    target_context = next((item for item in contexts if item.chart_id == target), None)
    sort_field = None
    if sort is not None and target_context is not None:
        sort_field = next(
            (item.field for item in target_context.spec.encodings if item.type == "quantitative"),
            None,
        )
        if sort_field is not None:
            changed = changed | {"sort_field"}
    return VisualIntent(
        EncodingPatch(
            mark=cast(Literal["line", "area", "bar", "point", "donut"] | None, mark),
            stack=cast(Literal["zero", "normalize"] | None, stack),
            sort=cast(Literal["ascending", "descending", "input"] | None, sort),
            sort_field=sort_field,
            limit=limit,
            changed=changed,
        ),
        target,
    )


def apply_visual_intent(intent: VisualIntent, context: VisualContext) -> VisualContext:
    if intent.patch is None:
        raise ValueError(intent.explanation or "No se pudo interpretar el cambio visual")
    return VisualContext(
        context.chart_id,
        merge_encoding_patch(context.spec, intent.patch, context.profile),
        context.profile,
    )
