"""Does the plan answer the question that was asked?

The other half of the capability guard, and the check nothing else in this pipeline
makes. Structural validation asks whether the plan is well formed; the domain invariants
ask whether it is coherent; the evaluation asks whether it matches an expected plan. None
of them asks whether it corresponds to the sentence in front of it, and a plan that
passes all three while answering something else is precisely what a successful prompt
injection produces.
"""

import re
from collections.abc import Callable
from typing import Final

from finguard_orchestrator.domain.enums import Direction, Metric
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan
from finguard_orchestrator.domain.value_objects.guard_policy import EVERY_RULE, GuardPolicy

_SPENDING: Final = re.compile(r"\bgast", re.IGNORECASE)

# Spanish agreement does the work: "¿cuánto gasté?" asks for an amount and "¿cuántas
# veces?" for a tally, and the plural is the whole signal. It holds across every metric
# case in the dataset — movimientos, operaciones, compras, viajes, cargos, ingresos.
_COUNTING: Final = re.compile(r"\bcuánt[oa]s\b", re.IGNORECASE)


def _spending_answered_as_everything(sentence: str, plan: FinancialPlan) -> bool:
    """Adding income to a spending total is a wrong number, not a broader answer. This is
    also what the live prompt injection has to get past, without being about injections."""
    return bool(_SPENDING.search(sentence)) and plan.direction is Direction.ALL


def _count_answered_as_amount(sentence: str, plan: FinancialPlan) -> bool:
    return bool(_COUNTING.search(sentence)) and plan.metric is not Metric.COUNT


# Same shape as the guard's registry, for the same reasons: named so it can be switched
# off, switchable so its worth can be measured.
_RULES: Final[tuple[tuple[str, Callable[[str, FinancialPlan], bool]], ...]] = (
    ("SPENDING_ANSWERED_AS_EVERYTHING", _spending_answered_as_everything),
    ("COUNT_ANSWERED_AS_AMOUNT", _count_answered_as_amount),
)

RULES: Final = frozenset(name for name, _ in _RULES)


def disagreement(
    sentence: str, plan: FinancialPlan, policy: GuardPolicy = EVERY_RULE
) -> str | None:
    """The rule the plan breaks against the sentence, or None."""
    for name, broken in _RULES:
        if policy.allows(name) and broken(sentence, plan):
            return name
    return None
