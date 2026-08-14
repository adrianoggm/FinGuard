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
from dataclasses import replace
from typing import Final

from finguard_orchestrator.domain.enums import Direction, GroupBy, Metric
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


# Asking how something divides up. "¿Cómo se reparte mi sueldo?", "¿en qué se me va el dinero?",
# "desglósame el año", "por categoría" — the last of these `explicit_facts` also reads, and the
# overlap is deliberate: this rule has to fire whether the grouping came from the sentence or from a
# model, because the failure below is the same either way.
_DIVIDES_UP: Final = re.compile(
    # `se me va` and `se va` both, because the pronoun is what a person actually says and
    # leaving it out matched neither of the two corpus sentences this rule was written for.
    r"\b(?:como|en que)\s+se\s+(?:me\s+)?(?:reparte|reparten|va|van)\b"
    r"|\breparte|\bdesglos|\bdistribu"
    r"|\bpor\s+(?:categoria|comercio|cuenta)\b"
)

# Which filter collapses which grouping. Only the four list-shaped dimensions: a window can also
# leave DAY or MONTH with a single bucket, but how many buckets a window covers is banking's
# arithmetic and not a property of the plan, so this does not pretend to know it.
_ONE_VALUE_COLLAPSES: Final = (
    (GroupBy.CATEGORY, "categories"),
    (GroupBy.MERCHANT, "merchants"),
    (GroupBy.COUNTERPARTY, "counterparties"),
    (GroupBy.ACCOUNT, "only_account_ids"),
)


def _cannot_discriminate(plan: FinancialPlan) -> bool:
    """The plan groups by a dimension it has already filtered to one value.

    A breakdown by category of a query filtered to one category is one row. Not an error and not a
    wrong figure — the row's total equals the ungrouped total — which is exactly why nothing else
    here sees it: every field is defensible on its own, the domain accepts it, banking runs it, and
    what comes back is a real amount wearing the shape of an answer it is not.

    `merchant_terms` is not in the table above. A term is a fragment matching a family of shops, so
    filtering by one term and grouping by merchant can legitimately return many rows.
    """
    for dimension, filtered in _ONE_VALUE_COLLAPSES:
        if plan.group_by is not dimension:
            continue
        if dimension is GroupBy.ACCOUNT and plan.only_account_id is not None:
            return not plan.only_account_ids
        return len(getattr(plan, filtered)) == 1
    return False


def _breakdown_answered_as_one_group(sentence: str, plan: FinancialPlan | TopMovements) -> bool:
    """The sentence asks how something divides up and the plan cannot divide it.

    Found by `judge_plans.py` on "¿cómo se reparte mi sueldo este mes?", which came back
    `categories: ["SALARY"]` with `group_by: CATEGORY` — a breakdown of one category into itself.
    The category was ours, read from "sueldo" by `category_words`; the grouping was the model's.
    Neither half is wrong alone, which is why this needs a rule of its own rather than a stricter
    version of an existing one.

    **No repair, and the reason is the one already written in `repaired`.** There are two ways out
    and the plan does not say which: drop the filter and the answer widens to money the question was
    not about, or drop the grouping and a total is served to somebody who asked for a distribution.
    The second is tempting because the figure does not change — and a figure that does not change is
    precisely how this class of defect reads as correct. So the caller refuses, as it does for a
    misread category.

    Our own deterministic paths already obey this rule; they just obeyed it one site at a time.
    `_shop_names_to_suggest` and the top-merchant follow-up both clear `merchants` before setting
    `group_by=MERCHANT`, with nothing beside them saying why. This is that why, in one place.
    """
    if isinstance(plan, TopMovements):
        # A ranking has no grouping to collapse. `_RANKING_RULES` excludes this rule before it is
        # reached; the check is here so the function is total on its own type.
        return False
    return bool(_DIVIDES_UP.search(spanish.folded(sentence))) and _cannot_discriminate(plan)


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
    named = set(category_words.precisely_named_in(sentence))
    excluded = set(category_words.precisely_excluded_in(sentence))
    included = named - excluded
    return bool(named) and (
        not included.issubset(plan.categories) or not excluded.issubset(plan.excluded_categories)
    )


# Same shape as the guard's registry, for the same reasons: named so it can be switched
# off, switchable so its worth can be measured.
_RULES: Final[tuple[tuple[str, Callable[[str, FinancialPlan | TopMovements], bool]], ...]] = (
    ("SPENDING_ANSWERED_AS_EVERYTHING", _spending_answered_as_everything),
    ("COUNT_ANSWERED_AS_AMOUNT", _count_answered_as_amount),
    ("CATEGORY_MISREAD", _category_misread),
    ("BREAKDOWN_ANSWERED_AS_ONE_GROUP", _breakdown_answered_as_one_group),
)

RULES: Final = frozenset(name for name, _ in _RULES)

# Which of them mean anything for a ranking. Named rather than derived, so adding a rule forces the
# question "does this apply to a maximum?" instead of silently answering yes.
_RANKING_RULES: Final = frozenset({"SPENDING_ANSWERED_AS_EVERYTHING", "CATEGORY_MISREAD"})


def repaired(
    broken: str, plan: FinancialPlan | TopMovements
) -> FinancialPlan | TopMovements | None:
    """The plan the sentence asked for, when the rule it broke has one determinate correction.

    A rule certain enough to refuse a well-formed plan is certain enough to correct one — where the
    correction exists. COUNT_ANSWERED_AS_AMOUNT is the case: it fires because Spanish agreement is
    unambiguous about "cuántos", and the field that has to change to satisfy it is the field the
    rule reads. Refusing while holding the answer is the assistant reporting on itself.

    None for everything else, which is most of it. A misread category could be *changed* to what
    the sentence names, and that is a guess wearing a correction's confidence: the vocabulary is
    sure the word was said, not that the plan's other category was wrong. The caller refuses
    instead, which is what it did before this existed.
    """
    if broken == "COUNT_ANSWERED_AS_AMOUNT" and isinstance(plan, FinancialPlan):
        return replace(plan, metric=Metric.COUNT)
    return None


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
