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
from finguard_orchestrator.domain.services import account_words, explicit_facts, spanish
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan


class PatchStatus(StrEnum):
    NOT_APPLICABLE = "NOT_APPLICABLE"
    APPLIED = "APPLIED"
    AMBIGUOUS = "AMBIGUOUS"


@dataclass(frozen=True)
class GroundedContinuationPatch:
    status: PatchStatus
    plan: FinancialPlan | None = None
    changed: frozenset[str] = frozenset()

    @classmethod
    def not_applicable(cls) -> "GroundedContinuationPatch":
        return cls(PatchStatus.NOT_APPLICABLE)

    @classmethod
    def ambiguous(cls) -> "GroundedContinuationPatch":
        return cls(PatchStatus.AMBIGUOUS)


# Short anaphora and correction markers are the evidence that this is an ellipsis.  A complete
# question such as "cuanto gaste en 2025" has no such marker and must go to the normal planner.
_CONTINUATION = re.compile(
    r"^(?:y\b|e\b|pero\b|no\b|mejor\b|en realidad\b|que va\b|sin\b|excepto\b|salvo\b|quitando\b)"
    r"|\b(?:eso|esa|ese|estos|estas|aquello|aquella|lo mismo)\b"
)
_UNSUPPORTED_SCOPE = re.compile(
    r"\b(?:cuanto|cuanta|cuantos|cuantas|que|cual|donde|quien|quienes|gaste|gastado|pague|compras?)\b"
)
_NON_TEMPORAL_EN = re.compile(r"\ben\s+(?P<word>[a-záéíóúüñ]+)")
_TEMPORAL_EN = {
    "2025",
    "2026",
    "abril",
    "enero",
    "febrero",
    "marzo",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
    "último",
    "última",
    "pasado",
    "pasada",
    "año",
    "mes",
}


def apply(
    sentence: str,
    previous: FinancialPlan | None,
    *,
    today: date | None = None,
) -> GroundedContinuationPatch:
    """Apply only explicit window/metric/group/filter facts to ``previous``.

    ``AMBIGUOUS`` is returned for a continuation-shaped sentence whose recognized facts are
    insufficient or mixed with a fresh question.  In both non-applied cases the caller must retain
    its existing safe mechanism; this function never returns a broadened plan.
    """
    if previous is None:
        return GroundedContinuationPatch.not_applicable()
    folded = spanish.folded(sentence).strip(" ¿?¡!.,;:")
    if not _CONTINUATION.search(folded):
        return GroundedContinuationPatch.not_applicable()

    facts = explicit_facts.read(sentence, today=today)
    # Widening is deliberately owned by the normal merge path: enforce() is monotone and
    # cannot remove an inherited filter, so treating this as a grounded patch would silently
    # preserve a category the user explicitly asked to clear.
    if facts.widens:
        return GroundedContinuationPatch.ambiguous()
    # A period correction may not smuggle in a new dimension. Those facts belong to the planner.
    if (
        facts.direction is not None
        or (facts.names_a_category and not facts.excluded_categories)
        or facts.asks_account_breakdown
        or facts.asks_comparison_reference
        or account_words.named_in(sentence)
        or account_words.explicitly_names_a_custom_account(sentence)
    ):
        return GroundedContinuationPatch.ambiguous()
    # Keep the temporal preposition, but fail closed on residual entities ("en Amazon", "en ocio").
    if facts.window is not None:
        for match in _NON_TEMPORAL_EN.finditer(folded):
            if match.group("word") not in _TEMPORAL_EN:
                return GroundedContinuationPatch.ambiguous()
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
        return GroundedContinuationPatch.ambiguous()
    changed: set[str] = set()
    if facts.window is not None:
        changed.add("window")
    if facts.metric is not None:
        changed.add("metric")
    if facts.group_by is not None:
        changed.add("groupBy")
    if facts.excluded_categories or facts.excluded_merchants or facts.widens:
        changed.add("exclusions")
    if not changed:
        return GroundedContinuationPatch.ambiguous()

    # enforce() uses an atomic period/date_range pair and dataclass replacement, so account,
    # entities, internal flags and transaction ids remain byte-for-byte unchanged.
    patched = explicit_facts.enforce(previous, facts)
    return GroundedContinuationPatch(PatchStatus.APPLIED, patched, frozenset(changed))
