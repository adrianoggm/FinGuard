"""Deterministic, value-free visual follow-up interpreter."""

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal, cast

from finguard_orchestrator.domain.models.chart import (
    EncodingPatch,
    Orientation,
    SortMode,
    VisualContext,
    merge_encoding_patch,
)


@dataclass(frozen=True)
class VisualIntent:
    patch: EncodingPatch | None
    target: str | None
    explanation: str | None = None
    # Set only by the mixed-turn refusal below: the sentence asked to change the data and the
    # format in the same turn, so this intent already speaks for two claimants rather than one.
    # `application/queries/readers/visual_follow_up.py` reads this to record AMBIGUOUS instead of
    # PRESENTATION_ONLY, without re-deriving "mixed" from the explanation text.
    mixed: bool = False


_FINANCIAL = re.compile(
    r"\b(?:por\s+(?:dias?|semanas?|meses?|trimestres?|anos?)|semanal|mensual|"
    r"enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|"
    r"diciembre|compar(?:a|ar)|restaurantes?|categorias?|gastos?|ingresos?)\b"
)
_VISUAL = re.compile(
    r"\b(?:barra|barras|linea|area|apil\w*|desapil\w*|ordena\w*|ascendente|descendente|"
    r"top\s*\d+|circular|donut|anillo|horizontal|vertical|tumba\w*|de\s+pie)\b"
)
_VISUAL_STYLE = re.compile(
    r"\b(?:barra|barras|linea|area|apil\w*|desapil\w*|ordena\w*|ascendente|descendente|"
    r"circular|donut|anillo|horizontal|vertical|tumba\w*|de\s+pie)\b"
)
# Exported (Wave 4.5, Task 4): `visual_explanation.asks_why` reads this same ordinal vocabulary
# off its own «¿por qué el primer gráfico?» match rather than a second copy of it.
TARGET = re.compile(
    r"\b(?:(?:el\s+)?(?:ultimo|last)|(?:el\s+)?primer(?:o|a)?|"
    r"(?:el\s+)?segund(?:o|a)?)\s+grafic(?:o|a)\b"
)
# Undo the row limit: draw every row the chart was given. Anchored to the whole sentence on purpose,
# and deliberately narrow. "todos" is a word financial questions are full of -- "todos mis
# movimientos" is a query, not a restyle -- so this matches only a sentence that is nothing but the
# request, which is the shape of the suggestion the answer offers. Anything longer stays a question.
_VISUAL_ALL = re.compile(r"(?:ver|mostrar|muestra(?:me)?|ensename)?\s*(?:todos|todas)|sin\s+limite")


def fold(sentence: str) -> str:
    return "".join(
        character
        for character in unicodedata.normalize("NFKD", sentence.casefold())
        if not unicodedata.combining(character)
    )


def resolve_target(word: str | None, contexts: tuple[VisualContext, ...]) -> str | None:
    """`word` (a folded `TARGET` match, or `None` for "the last chart") resolved against
    `contexts` into a real `chart_id`.

    The one resolution rule, shared rather than duplicated: no contexts at all resolves to
    nothing; naming a second chart that does not exist resolves to nothing too (the caller
    decides what that means -- `interpret_visual` refuses with its own sentence before ever
    reaching here for that case, `asks_why`'s caller answers `VISUAL_NEEDS_TARGET`); anything else
    is the last chart unless the word says "primer" or "segund".
    """
    if not contexts:
        return None
    if word is None:
        return contexts[-1].chart_id
    if "primer" in word:
        return contexts[0].chart_id
    if "segund" in word:
        return contexts[1].chart_id if len(contexts) >= 2 else None
    return contexts[-1].chart_id


def interpret_visual(sentence: str, contexts: tuple[VisualContext, ...]) -> VisualIntent | None:
    folded = fold(sentence)
    financial = _FINANCIAL.search(folded)
    show_every_row = _VISUAL_ALL.fullmatch(folded.strip(" ¿?¡!.,")) is not None
    visual_signal = _VISUAL.search(folded) or show_every_row
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
                mixed=True,
            )
        return None
    if not visual_signal:
        return None
    if not contexts:
        return VisualIntent(None, None, "No hay ningún gráfico anterior que pueda cambiar.")
    target_match = TARGET.search(folded)
    word = target_match.group(0).casefold() if target_match else None
    if word is not None and "segund" in word and len(contexts) < 2:
        return VisualIntent(None, None, "No existe un segundo gráfico anterior.")
    target = resolve_target(word, contexts)
    if show_every_row:
        # The one patch that clears rather than sets. `EncodingPatch.clear` exists for exactly this
        # and nothing produced it until now, so the limit the chart applies by default was one the
        # conversation could impose and never lift.
        return VisualIntent(EncodingPatch.clear("limit"), target)
    mark = None
    if re.search(r"\b(?:barra|barras)\b", folded):
        mark = "bar"
    elif re.search(r"\blinea\b", folded):
        mark = "line"
    elif re.search(r"\barea\b", folded):
        mark = "area"
    elif re.search(r"\b(?:circular|donut|anillo)\b", folded):
        mark = "donut"
    orientation: Orientation | None = None
    # M-10 (Wave 4.2b fix wave): a sentence naming both resolves to horizontal -- first-listed
    # wins, the same rule as everywhere else in this function (`mark` above, `sort` below).
    if re.search(r"\b(?:horizontal|tumba\w*)\b", folded):
        orientation = "horizontal"
    elif re.search(r"\b(?:vertical|de\s+pie)\b", folded):
        orientation = "vertical"
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
        for name, value in (
            ("mark", mark),
            ("stack", stack),
            ("sort", sort),
            ("limit", limit),
            ("orientation", orientation),
        )
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
            sort=cast(SortMode | None, sort),
            sort_field=sort_field,
            limit=limit,
            orientation=orientation,
            changed=changed,
        ),
        target,
    )


def apply_visual_intent(intent: VisualIntent, context: VisualContext) -> VisualContext:
    if intent.patch is None:
        raise ValueError(intent.explanation or "No se pudo interpretar el cambio visual")
    # `source`/`fallback_reason` stay `None` here (Task 1, Wave 4.5): this context is a scratch
    # merge, read only for its `.spec` by `_visual_only_response` before `_derived_context` builds
    # the one actually sealed and returned -- an echoed chart is not re-provenanced here, the same
    # reasoning `record_visual_decision`'s own refusal of a false provenance claim already states.
    return VisualContext(
        context.chart_id,
        merge_encoding_patch(context.spec, intent.patch, context.profile),
        context.profile,
    )
