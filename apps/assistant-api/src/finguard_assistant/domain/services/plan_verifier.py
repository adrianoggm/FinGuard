"""Does the plan answer the question that was asked?

The other half of the capability guard, and the check nothing else in this pipeline
makes. Structural validation asks whether the plan is well formed; the domain invariants
ask whether it is coherent; the evaluation asks whether it matches an expected plan. None
of them asks whether it corresponds to the sentence in front of it, and a plan that
passes all three while answering something else is precisely what a successful prompt
injection produces.
"""

import re
from typing import Final

from finguard_assistant.domain.enums import Direction, Metric
from finguard_assistant.domain.value_objects.financial_plan import FinancialPlan

_SPENDING: Final = re.compile(r"\bgast", re.IGNORECASE)

# Spanish agreement does the work: "¿cuánto gasté?" asks for an amount and "¿cuántas
# veces?" for a tally, and the plural is the whole signal. It holds across every metric
# case in the dataset — movimientos, operaciones, compras, viajes, cargos, ingresos.
_COUNTING: Final = re.compile(r"\bcuánt[oa]s\b", re.IGNORECASE)


def disagreement(sentence: str, plan: FinancialPlan) -> str | None:
    """The rule the plan breaks against the sentence, or None."""
    if _SPENDING.search(sentence) and plan.direction is Direction.ALL:
        return "SPENDING_ANSWERED_AS_EVERYTHING"
    if _COUNTING.search(sentence) and plan.metric is not Metric.COUNT:
        return "COUNT_ANSWERED_AS_AMOUNT"
    return None
