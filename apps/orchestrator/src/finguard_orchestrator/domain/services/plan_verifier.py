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
from finguard_orchestrator.domain.services import category_words, spanish
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan
from finguard_orchestrator.domain.value_objects.guard_policy import EVERY_RULE, GuardPolicy
from finguard_orchestrator.domain.value_objects.top_movements import TopMovements

_SPENDING: Final = re.compile(r"\bgast")

# Spanish agreement does the work: "¿cuánto gasté?" asks for an amount and "¿cuántas
# veces?" for a tally, and the plural is the whole signal. It holds across every metric
# case in the dataset — movimientos, operaciones, compras, viajes, cargos, ingresos.
# Unaccented, because the sentence is folded before it gets here. This rule missed the
# real conversation for exactly that reason: it read `\bcuánt[oa]s\b` and the user typed
# "cuantos", so a total was served as the answer to "how many".
_COUNTING: Final = re.compile(r"\bcuant[oa]s\b")


def _spending_answered_as_everything(sentence: str, plan: FinancialPlan | TopMovements) -> bool:
    """Adding income to a spending total is a wrong number, not a broader answer. This is
    also what the live prompt injection has to get past, without being about injections."""
    return bool(_SPENDING.search(spanish.folded(sentence))) and (plan.direction is Direction.ALL)


def _count_answered_as_amount(sentence: str, plan: FinancialPlan | TopMovements) -> bool:
    if isinstance(plan, TopMovements):
        # A ranking has no metric to disagree with. `_RANKING_RULES` excludes this rule before it is
        # reached; the check is here so the function is total on its own type.
        return False
    return bool(_COUNTING.search(spanish.folded(sentence))) and (plan.metric is not Metric.COUNT)


def _category_misread(sentence: str, plan: FinancialPlan | TopMovements) -> bool:
    """The sentence names a category unambiguously and the plan filters by something else.

    This is the failure that got through everything else. "¿Cuántas veces fui al cine el mes
    pasado?" came back as `categories: ["RESTAURANTS"]` with the metric, the direction and the
    window all correct — structurally valid, accepted by the domain, executed by banking, and
    answered with a real figure about a different question.

    The category vocabulary is closed and ours, so when a sentence uses one of its unambiguous words
    there is nothing to infer: cine is ocio the way EUR is euros. Which words qualify is decided in
    `category_words.precisely_named_in`, and the exclusions there matter more than the inclusions.

    **A subset, not equality, and the dataset settled that.** Requiring the plan to name exactly
    what was extracted refused ten correct cases: "¿cuánto llevo este mes en súper, gasolina,
    restaurantes, transporte, salud, ocio, suscripciones y alquiler?" names eight categories and
    this vocabulary is sure about seven, so the correct plan looked wrong. The honest rule is that
    everything the sentence says unambiguously has to be in the plan; what the vocabulary does not
    recognise, it has no opinion about.

    The cost is written down rather than hidden: an *extra* category the question never named is not
    caught here, because from this side a surplus category cannot be told apart from a word this
    vocabulary does not know.
    """
    named = category_words.precisely_named_in(sentence)
    return bool(named) and not set(named).issubset(plan.categories)


# Same shape as the guard's registry, for the same reasons: named so it can be switched
# off, switchable so its worth can be measured.
_RULES: Final[tuple[tuple[str, Callable[[str, FinancialPlan | TopMovements], bool]], ...]] = (
    ("SPENDING_ANSWERED_AS_EVERYTHING", _spending_answered_as_everything),
    ("COUNT_ANSWERED_AS_AMOUNT", _count_answered_as_amount),
    ("CATEGORY_MISREAD", _category_misread),
)

RULES: Final = frozenset(name for name, _ in _RULES)

# Which of them mean anything for a ranking. Named rather than derived, so adding a rule forces the
# question "does this apply to a maximum?" instead of silently answering yes.
_RANKING_RULES: Final = frozenset({"SPENDING_ANSWERED_AS_EVERYTHING", "CATEGORY_MISREAD"})


def disagreement(
    sentence: str, plan: FinancialPlan | TopMovements, policy: GuardPolicy = EVERY_RULE
) -> str | None:
    """The rule the plan breaks against the sentence, or None.

    A ranking is checked by the rules that apply to it. CATEGORY_MISREAD does — a maximum filtered
    by the wrong category is as wrong as a total filtered by it — and COUNT_ANSWERED_AS_AMOUNT does
    not, because a ranking has no metric to disagree with. Applying a rule to a type it cannot be
    about would be a refusal nobody could act on.
    """
    for name, broken in _RULES:
        if not policy.allows(name):
            continue
        if isinstance(plan, TopMovements) and name not in _RANKING_RULES:
            continue
        if broken(sentence, plan):
            return name
    return None
