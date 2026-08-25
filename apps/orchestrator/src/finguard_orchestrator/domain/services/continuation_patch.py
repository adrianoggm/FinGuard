"""Deterministic, grounded patches for a validated financial follow-up.

This module deliberately does not try to understand a new financial question.  It only applies
closed-vocabulary facts when the wording makes continuation (or correction) explicit.  Callers can
therefore put it in front of an LLM planner without risking an accidental widening of scope.
"""

import re
from dataclasses import dataclass, replace
from datetime import date
from enum import StrEnum

from finguard_orchestrator.domain.enums import GroupBy
from finguard_orchestrator.domain.errors import InvalidValueError
from finguard_orchestrator.domain.services import (
    account_words,
    category_words,
    explicit_facts,
    spanish,
)
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan
from finguard_orchestrator.domain.value_objects.plan_patch import PatchField, PlanPatch


class PatchStatus(StrEnum):
    NOT_APPLICABLE = "NOT_APPLICABLE"
    APPLIED = "APPLIED"
    AMBIGUOUS = "AMBIGUOUS"


@dataclass(frozen=True)
class GroundedContinuation:
    status: PatchStatus
    patch: PlanPatch | None = None

    def __post_init__(self) -> None:
        if (self.status is PatchStatus.APPLIED) != (self.patch is not None):
            raise InvalidValueError("APPLIED requires a patch, and only APPLIED may carry one")

    @classmethod
    def not_applicable(cls) -> "GroundedContinuation":
        return cls(PatchStatus.NOT_APPLICABLE)

    @classmethod
    def ambiguous(cls) -> "GroundedContinuation":
        return cls(PatchStatus.AMBIGUOUS)

    @property
    def changed(self) -> frozenset[PatchField]:
        return self.patch.fields if self.patch else frozenset()


@dataclass(frozen=True)
class GroundedContinuationPatch:
    """Compatibility result for callers that still ask this module to apply a patch."""

    status: PatchStatus
    patch: PlanPatch | None = None
    plan: FinancialPlan | None = None

    @property
    def changed(self) -> frozenset[PatchField]:
        return self.patch.fields if self.patch else frozenset()


# Short anaphora and correction markers are the evidence that this is an ellipsis.  A complete
# question such as "cuanto gaste en 2025" has no such marker and must go to the normal planner.
_CONTINUATION = re.compile(
    r"^(?:y\b|e\b|pero\b|no\b|mejor\b|en realidad\b|que va\b|sin\b|excepto\b|salvo\b|quitando\b)"
    r"|\b(?:eso|esa|ese|estos|estas|aquello|aquella|lo mismo)\b"
)
_UNSUPPORTED_SCOPE = re.compile(
    r"\b(?:cuanto|cuanta|cuantos|cuantas|que|cual|donde|quien|quienes|gaste|gastado|pague|compras?)\b"
)
_LEXICAL_TOKEN = re.compile(r"[a-z0-9]+")
_WINDOW_PATCH_WORDS = {
    "abril",
    "a",
    "agosto",
    "ahora",
    "al",
    "ano",
    "anos",
    "anterior",
    "anteriores",
    "ayer",
    "categoria",
    "categorias",
    "comercio",
    "comercios",
    "completa",
    "completo",
    "cuantas",
    "cuantos",
    "cuenta",
    "cuentas",
    "de",
    "del",
    "desde",
    "dia",
    "dias",
    "diciembre",
    "durante",
    "el",
    "en",
    "enero",
    "entera",
    "entero",
    "entre",
    "esa",
    "ese",
    "esta",
    "este",
    "estos",
    "fin",
    "febrero",
    "grupo",
    "grupos",
    "hasta",
    "hoy",
    "la",
    "las",
    "llevamos",
    "lo",
    "los",
    "marzo",
    "mayo",
    "media",
    "me",
    "mejor",
    "mes",
    "meses",
    "movimiento",
    "movimientos",
    "no",
    "noviembre",
    "octubre",
    "operacion",
    "operaciones",
    "pasada",
    "pasado",
    "pasadas",
    "pasados",
    "periodo",
    "pero",
    "por",
    "promedio",
    "primera",
    "primer",
    "quincena",
    "referia",
    "realidad",
    "salvo",
    "segunda",
    "segundo",
    "semana",
    "semanas",
    "septiembre",
    "sin",
    "todo",
    "toda",
    "total",
    "trimestre",
    "ultimo",
    "ultima",
    "ultimos",
    "ultimas",
    "va",
    "y",
    "e",
    "excepto",
    "quitando",
    "junio",
    "julio",
    # Number words accepted by the window reader. Digits are accepted separately.
    "un",
    "una",
    "uno",
    "dos",
    "tres",
    "cuatro",
    "cinco",
    "seis",
    "siete",
    "ocho",
    "nueve",
    "diez",
    "once",
    "doce",
    "trece",
    "catorce",
    "quince",
    "veinte",
    "treinta",
}


def _has_unclaimed_window_token(
    folded: str,
    excluded_categories: tuple[str, ...],
    excluded_merchants: tuple[str, ...],
) -> bool:
    """Fail closed when a grounded window leaves an entity-like word unexplained."""
    claimed = set(_WINDOW_PATCH_WORDS)
    for category in excluded_categories:
        description = category_words.words_for(category) or ""
        claimed.update(_LEXICAL_TOKEN.findall(spanish.folded(description)))
        claimed.update(_LEXICAL_TOKEN.findall(spanish.folded(category_words.name_of(category))))
    for merchant in excluded_merchants:
        claimed.update(_LEXICAL_TOKEN.findall(spanish.folded(merchant)))
    return any(
        not token.isdigit() and token not in claimed for token in _LEXICAL_TOKEN.findall(folded)
    )


def read(
    sentence: str,
    *,
    today: date | None = None,
) -> GroundedContinuation:
    """Read a closed-vocabulary continuation without consulting conversational state.

    ``AMBIGUOUS`` is returned for a continuation-shaped sentence whose recognized facts are
    insufficient or mixed with a fresh question. The caller decides whether a base exists.
    """
    folded = spanish.folded(sentence).strip(" ¿?¡!.,;:")
    if not _CONTINUATION.search(folded):
        return GroundedContinuation.not_applicable()

    facts = explicit_facts.read(sentence, today=today)
    # Widening is deliberately owned by the normal merge path: enforce() is monotone and
    # cannot remove an inherited filter, so treating this as a grounded patch would silently
    # preserve a category the user explicitly asked to clear.
    if facts.widens:
        return GroundedContinuation.ambiguous()
    # A period correction may not smuggle in a new dimension. Those facts belong to the planner.
    if (
        facts.direction is not None
        or (facts.names_a_category and not facts.excluded_categories)
        or facts.asks_account_breakdown
        or facts.asks_comparison_reference
        or account_words.named_in(sentence)
        or account_words.explicitly_names_a_custom_account(sentence)
    ):
        return GroundedContinuation.ambiguous()
    if facts.window is not None and _has_unclaimed_window_token(
        folded, facts.excluded_categories, facts.excluded_merchants
    ):
        return GroundedContinuation.ambiguous()
    if facts.group_by is None and re.search(r"\bpor\s+meses\b", folded):
        facts = replace(facts, group_by=GroupBy.MONTH)
    # A bare metric question ("y cuántos movimientos") is still a closed-vocabulary
    # continuation.  Do not mistake its interrogative wording for an unsupported fresh scope.
    if (
        _UNSUPPORTED_SCOPE.search(folded)
        and facts.window is None
        and facts.group_by is None
        and facts.metric is None
    ):
        return GroundedContinuation.ambiguous()
    if not any(
        (
            facts.window,
            facts.metric,
            facts.group_by,
            facts.excluded_categories,
            facts.excluded_merchants,
        )
    ):
        return GroundedContinuation.ambiguous()
    patch = PlanPatch(
        window=facts.window,
        metric=facts.metric,
        group_by=facts.group_by,
        excluded_categories=facts.excluded_categories,
        excluded_merchants=facts.excluded_merchants,
    )
    return GroundedContinuation(PatchStatus.APPLIED, patch)


def apply(
    sentence: str,
    previous: FinancialPlan | None,
    *,
    today: date | None = None,
) -> GroundedContinuationPatch:
    """Compatibility wrapper around ``read`` and the immutable patch operation."""
    if previous is None:
        return GroundedContinuationPatch(PatchStatus.NOT_APPLICABLE)
    continuation = read(sentence, today=today)
    plan = continuation.patch.apply_to(previous) if continuation.patch is not None else None
    return GroundedContinuationPatch(continuation.status, continuation.patch, plan)
