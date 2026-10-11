"""Deterministic, value-free visual follow-up interpreter."""

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal, cast

from finguard_orchestrator.domain.errors import InvalidValueError
from finguard_orchestrator.domain.models.chart import (
    EncodingPatch,
    Orientation,
    SortMode,
    Stack,
    VisualContext,
    merge_encoding_patch,
)
from finguard_orchestrator.domain.services import category_words, spanish_numbers


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
    # Item 3 (Wave 3 hardening, T8b): set only by the "go back" ask below. `explanation` alone
    # already means "no patch, speak this instead" (a refusal); a non-empty `ask_chips` on top of
    # that means the sentence is genuinely ambiguous about *which* chart-related thing was meant,
    # not merely unsupported -- `llm_controller._visual_only_response` reads this to answer
    # `VISUAL_AMBIGUOUS` with these as `suggestions` instead of `VISUAL_INCOMPATIBLE` with none.
    ask_chips: tuple[str, ...] | None = None


_FINANCIAL = re.compile(
    r"\b(?:por\s+(?:dias?|semanas?|meses?|trimestres?|anos?)|semanal|mensual|"
    r"enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|"
    r"diciembre|compar(?:a|ar)|restaurantes?|categorias?|gastos?|ingresos?)\b"
)
# A-10 (Wave 3 hardening): the subset of `_FINANCIAL` that actually asks something new -- a
# window or a comparison verb. `_FINANCIAL` also matches "categorías"/"gastos"/"ingresos", the
# *generic* words for the axis or for money in general -- what a chart's own axis is called, not a
# second question; "Ordena las categorías de mayor a menor" and "Ponme los gastos en barras" both
# matched only through one of those and were refused `VISUAL_AMBIGUOUS` for asking "two things at
# once" when they asked for one. Real category words ("restaurantes", "ocio", ...) are not generic
# the same way: "solo restaurantes" beside "en barras" genuinely asks to filter and restyle in the
# same turn (`category_words.named_in`, checked below rather than folded into this regex, is the
# closed vocabulary that already draws exactly that line for every other reader in this service).
_FINANCIAL_QUERY_SIGNAL = re.compile(
    r"\b(?:por\s+(?:dias?|semanas?|meses?|trimestres?|anos?)|semanal|mensual|"
    r"enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|"
    r"diciembre|compar(?:a|ar))\b"
)
# F-H1 (Wave 3 hardening, fix round): A-10's fix above left no `else` for the sentence that is
# neither a clean restyle nor caught by `_FINANCIAL_QUERY_SIGNAL`/`category_words.named_in` --
# "Ordena mis gastos por categoría y dime el total" names only the generic axis noun
# ("categoría") beside a sort word, so it slipped through both AMBIGUOUS gates and fell all the
# way to the ordinary restyle logic below, answering a sort patch and silently discarding "dime
# el total". This is the closed vocabulary of a sentence that is unmistakably asking for data,
# not naming its axis: an imperative to speak an answer ("dime", "cuéntame"), a quantity word
# ("cuánto", "cuánta", "cuántos", "cuántas"), "total", the first-person past of "to spend"
# ("gasté"), any form of "ingres-" (income, distinct from the bare plural axis noun "ingresos"
# already carved out above), "saldo", a bare digit, or a month name (the last already trips
# `_FINANCIAL_QUERY_SIGNAL` above and is repeated here only as a second line of defence).
_DATA_ASKING = re.compile(
    r"\b(?:dime|cuentame|cuant[oa]s?|total(?:es)?|gaste|ingres\w*|saldo|\d+|"
    r"enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre)\b"
)
# A-8 (Wave 3 hardening): plurals `_VISUAL` never learned ("líneas", "áreas") and a mark, "puntos",
# it never had a word for at all -- design §3.1's own "Prefiero líneas" fell through both gates
# and reached the financial planner as a query no vocabulary here had claimed.
_VISUAL = re.compile(
    r"\b(?:barras?|lineas?|areas?|puntos?|apil\w*|desapil\w*|ordena\w*|ascendente|descendente|"
    r"top\s*\d+|circular|donut|anillo|tarta|pastel|quesito|horizontal|vertical|tumba\w*|de\s+pie)\b"
)
_VISUAL_STYLE = re.compile(
    r"\b(?:barras?|lineas?|areas?|puntos?|apil\w*|desapil\w*|ordena\w*|ascendente|descendente|"
    r"circular|donut|anillo|tarta|pastel|quesito|horizontal|vertical|tumba\w*|de\s+pie)\b"
)
# B-9 (Wave 3 hardening, T8b): a request about the chart's own *size*, never its shape, order or
# data -- "hazlo más grande/pequeño", "agrándalo", "amplía", "zoom". Read as its own signal (not
# folded into `_VISUAL`) because it never produces a patch: every match below is refused with the
# one honest reason, "the screen decides the size", regardless of what else the sentence names.
_SIZE = re.compile(r"\b(?:mas\s+grand\w*|mas\s+pequen\w*|agrand\w*|ampli[ae]\w*|zoom\w*)\b")
# Item 3 (Wave 3 hardening, T8b): "vuelve al anterior", "el de antes" with no style word beside it
# -- genuinely ambiguous between "show the previous chart" and "undo the last change", which is
# why it asks rather than restyles (§5's own AMBIGUOUS shape, not a refusal). "Deshaz" alone is
# already a closed-stop-list word (B-2a) answered before this ever runs; this regex is for the
# longer phrasings the stop-list's own two-word ceiling cannot reach.
_GO_BACK = re.compile(r"\bvuelve\s+al?\s+anterior\b|\b(?:el|lo)\s+de\s+antes\b")
# U-1 (Wave 3 hardening): "desapil\w*" already fires `_VISUAL`/`_VISUAL_STYLE` above (it contains
# "apil"), but the plain substring check below used to read that same "apil" as "apila" (stack
# zero) and only "desapil" as "clear the stack" -- leaving "sin apilar" (no "desapil" substring)
# misread as a request TO stack, and giving no way to ask at all through "separa las barras". Named
# here once so both the gate and the value derivation agree on what "wants no stacking" means.
_UNSTACK = re.compile(r"\bdesapil\w*\b|\bsin\s+apilar\b|\bseparar?\s+las\s+barras\b")
# B-8 (Wave 3 hardening): "solo los tres primeros", "los cinco primeros", "los 10 últimos" -- a
# row limit in words or digits. Not folded into `_VISUAL`'s own alternation because, exactly like
# "top N" beside it, this phrase means something different in a financial sentence ("los tres
# primeros gastos de gasolina" is a query limit, not a restyle) and is read as one only when
# `interpret_visual` has already decided the turn is not financial.
#
# `(?!\s+movimiento)` refuses "los cinco últimos movimientos" -- a real financial query for the
# five most recent transactions, not a chart row cut, and one no word in `_FINANCIAL`'s own noun
# list already caught the way "gastos"/"restaurantes" would have.
_LIMIT_PHRASE = re.compile(
    r"\b(?:solo\s+)?(?:los?|las?)\s+(?P<num>\d{1,2}|[a-z]+)\s+"
    r"(?:primer[oa]s?|ultim[oa]s?|mayores?|mas\s+altos?)\b(?!\s+movimiento)"
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
    # A-9 (Wave 3 hardening): a second line of defence, beside `asks_why` claiming a "por qué"
    # sentence before this ever runs. "Por qué barras" names a mark word and nothing else this
    # function's own grammar can tell apart from an instruction to restyle TO it -- refusing any
    # sentence that starts with the question word closes that gap however the "why" grammar is
    # phrased next, rather than only for the phrasings it already knows.
    if folded.strip(" ¿?¡!.,").startswith("por que"):
        return None
    financial = _FINANCIAL.search(folded)
    show_every_row = _VISUAL_ALL.fullmatch(folded.strip(" ¿?¡!.,")) is not None
    limit_phrase = _LIMIT_PHRASE.search(folded)
    size_only = _SIZE.search(folded) is not None
    go_back = _GO_BACK.search(folded) is not None
    visual_signal = (
        _VISUAL.search(folded) or show_every_row or limit_phrase is not None or size_only or go_back
    )
    if financial:
        # In a financial sentence, "top N" is a query limit (for example "top 2 categories by
        # average ticket"), not a request to trim the already rendered rows. Only an actual style
        # instruction makes the turn mixed -- and only when something beside the style word
        # actually asks a new financial question (A-10): a bare axis noun ("categorías", "gastos")
        # is what the chart's own axis is called, not a second thing to plan.
        if _VISUAL_STYLE.search(folded):
            if _FINANCIAL_QUERY_SIGNAL.search(folded) or category_words.named_in(sentence):
                return VisualIntent(
                    None,
                    None,
                    "Has pedido cambiar los datos y el formato a la vez. Pide primero el cambio "
                    "financiero y después el formato del gráfico en otro turno.",
                    mixed=True,
                )
            # F-H1: neither AMBIGUOUS gate fired, but the sentence may still be asking a real
            # financial question the narrow checks above don't catch (no window, no comparison
            # verb, no named category -- "y dime el total" is exactly this shape). Restore the
            # missing `else`: only a sentence with no data-asking token left in it is a pure
            # restyle; anything else falls through to the planner rather than being silently
            # answered as a chart change.
            if _DATA_ASKING.search(folded):
                return None
        else:
            return None
    if not visual_signal:
        return None
    if not contexts:
        # F-3 (fix wave, Important 6), narrowed by two regressions found measuring D-H-8: a bare
        # style/mark/orientation word (`_VISUAL` -- "barras", "líneas", "circular", "ordena"...)
        # is the one no-context signal with no other honest reading, so that alone is claimed and
        # answered honestly instead of falling to the planner and inventing a window. Every other
        # route to `visual_signal` here abstains instead of claiming, because each has a real
        # non-chart reading this interpreter cannot tell apart from the chart one with no chart to
        # check against: `show_every_row` ("todos"/"sin límite") may be answering a pending
        # clarification C-3 already owns; `go_back` ("lo de antes") is ordinary Spanish for "what
        # we were saying before", not only "the previous chart" ("Volviendo a lo de antes, ¿cuál
        # fue la nómina más alta?", episode 14); `_LIMIT_PHRASE` ("los 2 últimos") reads a window
        # just as naturally as a row limit ("¿Y de algún otro en los 2 últimos meses?", episode
        # 13-messy); and a bare size word alone is B-9's own ambiguous case one layer up. Each of
        # those already falls through to a reader that exists for its non-chart reading -- exactly
        # as every no-context visual signal did before F-3 introduced this branch at all.
        if not _VISUAL.search(folded):
            return None
        return VisualIntent(None, None, "No hay ningún gráfico anterior que pueda cambiar.")
    target_match = TARGET.search(folded)
    word = target_match.group(0).casefold() if target_match else None
    if word is not None and "segund" in word and len(contexts) < 2:
        return VisualIntent(None, None, "No existe un segundo gráfico anterior.")
    target = resolve_target(word, contexts)
    # B-9: a bare size word, with no real style word beside it, never reaches the mark/orientation/
    # sort logic below -- the honest refusal names what CAN change instead of silently answering an
    # unrelated total.
    if size_only and not _VISUAL_STYLE.search(folded) and limit_phrase is None:
        return VisualIntent(
            None,
            target,
            "El tamaño lo decide la pantalla; puedo cambiar el tipo de gráfico, el orden o "
            "los datos.",
        )
    # Item 3: "vuelve al anterior"/"el de antes" with no style word beside it, over two or more
    # charts, is genuinely ambiguous -- it could mean "show me the previous chart" or "undo the
    # last change" -- so it asks rather than guesses. The two chips are sentences the interpreter
    # already claims on its own: "Deshacer el último cambio" is `deshaz` (B-2a's own closed-list
    # word, answered as a CLARIFY before this function ever runs -- offered here as a chip, not
    # read back through this module) and "Pon el gráfico anterior en barras" is an ordinary
    # restyle this same function claims (a style word, "barras", beside a target it already
    # resolves). With fewer than two charts there is nothing to go back TO, so this falls through
    # unclaimed exactly as before -- the ordinary, single-chart case this task's scope never
    # touched.
    if go_back and not _VISUAL_STYLE.search(folded) and limit_phrase is None and len(contexts) >= 2:
        return VisualIntent(
            None,
            target,
            "¿Quieres ver el gráfico anterior o deshacer el último cambio?",
            ask_chips=("Pon el gráfico anterior en barras", "Deshacer el último cambio"),
        )
    if show_every_row:
        # The one patch that clears rather than sets. `EncodingPatch.clear` exists for exactly this
        # and nothing produced it until now, so the limit the chart applies by default was one the
        # conversation could impose and never lift.
        return VisualIntent(EncodingPatch.clear("limit"), target)
    mark = None
    if re.search(r"\bbarras?\b", folded):
        mark = "bar"
    elif re.search(r"\blineas?\b", folded):
        mark = "line"
    elif re.search(r"\bareas?\b", folded):
        mark = "area"
    elif re.search(r"\bpuntos?\b", folded):
        mark = "point"
    elif re.search(r"\b(?:circular|donut|anillo|tarta|pastel|quesito)\b", folded):
        mark = "donut"
    orientation: Orientation | None = None
    # M-10 (Wave 4.2b fix wave): a sentence naming both resolves to horizontal -- first-listed
    # wins, the same rule as everywhere else in this function (`mark` above, `sort` below).
    if re.search(r"\b(?:horizontal|tumba\w*)\b", folded):
        orientation = "horizontal"
    elif re.search(r"\b(?:vertical|de\s+pie)\b", folded):
        orientation = "vertical"
    # U-1: the presenter (`vega_lite.py`) emits an explicit `null` only for `stack="disabled"` --
    # `stack=None` means "Vega-Lite decides", and its default for a bar/area with a colour series
    # is to stack. So "desapílalo"/"sin apilar"/"separa las barras" have to name the disabled
    # value outright, never fall back to the absent one, or the verifier's own `_grouped` rule
    # (which requires `stack == "disabled"` on a comparison) refuses the very change asked for.
    wants_unstack = _UNSTACK.search(folded) is not None
    stack = (
        "normalize"
        if "100%" in folded or "porcentaje" in folded or "normalizado" in folded
        else ("disabled" if wants_unstack else ("zero" if "apil" in folded else None))
    )
    sort = (
        "ascending"
        if re.search(r"\b(?:ascendente|menor\s+a\s+mayor)\b", folded)
        else (
            "descending"
            if re.search(r"\b(?:ordena\w*|descendente|mayor\s+a\s+menor)\b", folded)
            else None
        )
    )
    limit: int | None = None
    limit_range = (1, 100)
    top_match = re.search(r"\btop\s*(\d{1,3})\b", folded)
    if top_match:
        limit = int(top_match.group(1))
    elif limit_phrase:
        # B-8: the same phrase, in words or digits -- "los cinco primeros" and "los 5 primeros"
        # read the same limit. 1-20, not top's 1-100: this is a chip-sized "just the first few",
        # never a hundred-row cut.
        num_token = limit_phrase.group("num")
        limit = int(num_token) if num_token.isdigit() else spanish_numbers.integer(num_token)
        limit_range = (1, 20)
    if limit is not None and not limit_range[0] <= limit <= limit_range[1]:
        return VisualIntent(
            None, target, f"El top debe estar entre {limit_range[0]} y {limit_range[1]}."
        )
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
            stack=cast(Stack | None, stack),
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
        raise InvalidValueError(intent.explanation or "No se pudo interpretar el cambio visual")
    # `source`/`fallback_reason` stay `None` here (Task 1, Wave 4.5): this context is a scratch
    # merge, read only for its `.spec` by `_visual_only_response` before `_derived_context` builds
    # the one actually sealed and returned -- an echoed chart is not re-provenanced here, the same
    # reasoning `record_visual_decision`'s own refusal of a false provenance claim already states.
    return VisualContext(
        context.chart_id,
        merge_encoding_patch(context.spec, intent.patch, context.profile),
        context.profile,
    )
