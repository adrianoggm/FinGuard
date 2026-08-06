"""Deterministic controls offered after a failed or empty financial query.

Every label returned here is also understood here (or by the category catalogue router). That
pairing is the contract: a suggestion that merely looks helpful but falls through to a language
model is a button whose behaviour changes with the configured provider.
"""

from dataclasses import replace
from enum import StrEnum

from finguard_orchestrator.domain.enums import Period
from finguard_orchestrator.domain.services import spanish
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan, RelativePeriod
from finguard_orchestrator.domain.value_objects.top_movements import TopMovements

type PlannedQuery = FinancialPlan | TopMovements

RETRY = "Reintentar"
CHANGE = "Cambiar consulta"
CANCEL = "Cancelar consulta"
WITHOUT_CATEGORY = "Buscar sin categoría"
WITHOUT_MERCHANT = "Buscar sin comercio"
THREE_MONTHS = "Ampliar a tres meses"
SHOW_CATEGORIES = "Mostrar categorías"

# Offered when the same question has been asked twice. Every one of these is a phrase
# `window_words` reads, which is this module's whole contract: a suggestion that falls through to a
# model is a button whose behaviour depends on the configured provider.
#
# The reported conversation asked "¿de qué periodo?" three times in a row about a period the user
# had already named twice. A third identical question is not a clarification, it is a loop, and the
# way out of one is to stop asking an open question and offer the answers this service can act on.
WINDOW_CHOICES = ["Este mes", "El mes pasado", "Los últimos 12 meses", "En 2025"]


def repeated_question(slot: str) -> tuple[str, list[str]]:
    """What to say instead of asking the same thing a second time, and the replies that work.

    `slot` is the name of the field being asked about. Only the window has choices worth listing —
    it is a closed vocabulary and it is the slot that loops — so everything else gets the same
    honest sentence with nothing to press.
    """
    if slot in {"WINDOW", "YEAR"}:
        return (
            "Sigo sin tener claro el periodo. Dime uno de estos, o unas fechas concretas.",
            list(WINDOW_CHOICES),
        )
    return ("No he conseguido entender esa parte. ¿Puedes decírmelo de otra forma?", [])


class RetryAction(StrEnum):
    RETRY = "RETRY"
    CHANGE = "CHANGE"
    CANCEL = "CANCEL"


def retry_action(sentence: str) -> RetryAction | None:
    """A whole-turn instruction about the failed query, or None.

    Anchored meanings keep a sentence such as "reintenta el bizum" out of this read-only path.
    Payment confirmations are routed before this service, but the boundary remains worth making
    explicit in the vocabulary itself.
    """
    said = spanish.folded(sentence).strip(" ¿?¡!.,")
    if said in {
        "reintentar",
        "reintenta",
        "intentalo otra vez",
        "vuelve a intentarlo",
        "reintentar consulta",
    }:
        return RetryAction.RETRY
    if said in {"cambiar consulta", "quiero cambiar la consulta", "otra consulta"}:
        return RetryAction.CHANGE
    if said in {"cancelar consulta", "cancela la consulta", "deja la consulta"}:
        return RetryAction.CANCEL
    return None


def retry_suggestions() -> list[str]:
    return [RETRY, CHANGE, CANCEL]


def empty_suggestions(plan: PlannedQuery) -> list[str]:
    """Useful relaxations for the filters that actually made this query narrower."""
    choices: list[str] = []
    if plan.categories:
        choices.append(WITHOUT_CATEGORY)
    if plan.merchants:
        choices.append(WITHOUT_MERCHANT)

    already_wide = (
        plan.period is not None
        and plan.period.period is Period.LAST_N_MONTHS
        and (plan.period.n or 0) >= 3
    )
    if not already_wide:
        choices.append(THREE_MONTHS)
    choices.append(SHOW_CATEGORIES)
    return choices[:3]


def relaxation(sentence: str, previous: PlannedQuery | None) -> PlannedQuery | None:
    """Apply one action advertised after an empty answer without invoking a model."""
    if previous is None:
        return None
    said = spanish.folded(sentence).strip(" ¿?¡!.,")
    if said in {"buscar sin categoria", "sin categoria", "quita la categoria"}:
        return replace(previous, categories=())
    if said in {"buscar sin comercio", "sin comercio", "quita el comercio"}:
        return replace(previous, merchants=())
    if said in {
        "ampliar a tres meses",
        "amplia a tres meses",
        "buscar en los ultimos tres meses",
    }:
        return replace(
            previous,
            period=RelativePeriod(period=Period.LAST_N_MONTHS, n=3),
            date_range=None,
        )
    return None


__all__ = [
    "CANCEL",
    "CHANGE",
    "RETRY",
    "RetryAction",
    "empty_suggestions",
    "relaxation",
    "retry_action",
    "retry_suggestions",
]
