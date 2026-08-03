"""The gate that catches "cine" answered as restaurants.

This is the failure from the real conversation, and it got through every existing check: the
structure was valid, the metric was right, the direction was right, banking executed it and returned
a real figure. The plan verifier had two rules and neither was about categories.

    "¿cuántas veces fui al cine el mes pasado?"
    → {"period": "LAST_MONTH", "direction": "EXPENSE",
       "categories": ["RESTAURANTS"], "metric": "COUNT"}

The category vocabulary is closed and ours, so when a sentence names one of its words there is
nothing to infer — "cine" is LEISURE the way "EUR" is euros. A plan that says otherwise is wrong and
can be known to be wrong without asking anybody.

**Which words count is the whole design.** Only the ones that mean exactly one category. "La compra"
and "la casa" are in the model's glossary and are deliberately *not* here: somebody asking about
"cosas de la casa" may well mean the supermarket, and a gate that forced HOUSING_RENT there would
break correct plans in the name of fixing wrong ones.
"""

import pytest

from finguard_orchestrator.domain.enums import Direction, GroupBy, Metric, Period
from finguard_orchestrator.domain.services import category_words, plan_verifier
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan, RelativePeriod
from finguard_orchestrator.domain.value_objects.guard_policy import GuardPolicy

RULE = "CATEGORY_MISREAD"


def plan(*categories: str, metric: Metric = Metric.SUM) -> FinancialPlan:
    return FinancialPlan(
        direction=Direction.EXPENSE,
        period=RelativePeriod(period=Period.LAST_MONTH),
        categories=categories,
        group_by=GroupBy.NONE,
        metric=metric,
    )


# --- which words are precise enough to gate on -------------------------------------


@pytest.mark.parametrize(
    ("sentence", "expected"),
    [
        ("¿cuántas veces fui al cine el mes pasado?", ("LEISURE",)),
        ("¿cuánto gasté en gasolina?", ("TRANSPORT_FUEL",)),
        ("¿cuánto pago de alquiler?", ("HOUSING_RENT",)),
        ("mi nómina de julio", ("SALARY",)),
        ("gastos de farmacia", ("HEALTH",)),
        ("¿cuánto me gasto en el supermercado?", ("GROCERIES",)),
        (
            "¿cuánto llevo este mes en súper, gasolina y farmacia?",
            ("GROCERIES", "TRANSPORT_FUEL", "HEALTH"),
        ),
        # A shop is a merchant, not a category. The first version listed "mercadona" under GROCERIES
        # and refused the dataset case whose correct plan filters by merchant and names no category.
        ("¿cuánto me he gastado en Mercadona este mes?", ()),
        ("¿cuánto en restaurantes?", ("RESTAURANTS",)),
        ("el gimnasio y el cine", ("SUBSCRIPTIONS", "LEISURE")),
    ],
)
def test_a_word_that_means_one_category_is_extracted(
    sentence: str, expected: tuple[str, ...]
) -> None:
    assert category_words.precisely_named_in(sentence) == expected


@pytest.mark.parametrize(
    "sentence",
    [
        # In the model's glossary, and ambiguous in real speech. Each of these could reasonably be
        # two categories, and a gate is only worth having where it cannot be wrong.
        "¿cuánto gasté en la compra?",
        "cosas de la casa",
        "un café con Marta",
        "lo que gasto en deporte",
        "mis libros",
        "la paga de este mes",
    ],
)
def test_an_ambiguous_word_is_not_extracted(sentence: str) -> None:
    """The gate stays silent rather than guessing. A rule that fires on "la casa" would force
    HOUSING_RENT onto somebody asking about household shopping, which is the same class of error it
    exists to prevent — only ours instead of the model's."""
    assert category_words.precisely_named_in(sentence) == ()


# --- the rule ----------------------------------------------------------------------


def test_the_cinema_answered_as_restaurants_is_refused() -> None:
    broken = plan_verifier.disagreement(
        "¿cuántas veces fui al cine el mes pasado?",
        plan("RESTAURANTS", metric=Metric.COUNT),
    )

    assert broken == RULE


def test_the_right_category_passes() -> None:
    assert (
        plan_verifier.disagreement(
            "¿cuántas veces fui al cine el mes pasado?", plan("LEISURE", metric=Metric.COUNT)
        )
        is None
    )


def test_a_named_category_that_was_dropped_is_refused() -> None:
    """No filter at all is not a broader answer to "¿cuánto gasté en gasolina?" — it is every
    expense there is, presented as the answer to one of them."""
    assert plan_verifier.disagreement("¿cuánto gasté en gasolina?", plan()) == RULE


def test_an_extra_category_is_not_caught_and_that_is_deliberate() -> None:
    """It is wrong — the figure includes money the question did not ask about — and this rule cannot
    know it.

    From this side a surplus category cannot be told apart from a word the vocabulary does not
    recognise: "transporte" and "la compra" are deliberately absent, so a plan naming a category the
    extractor did not find may well be right. Requiring equality refused ten correct dataset cases,
    which is how this was settled. The limit is asserted rather than left as a surprise.
    """
    assert (
        plan_verifier.disagreement("¿cuánto gasté en gasolina?", plan("TRANSPORT_FUEL", "LEISURE"))
        is None
    )


def test_two_named_and_two_used_is_fine() -> None:
    assert (
        plan_verifier.disagreement("el gimnasio y el cine", plan("SUBSCRIPTIONS", "LEISURE"))
        is None
    )


def test_a_sentence_that_names_no_category_is_left_alone() -> None:
    """Most questions name none, and the model choosing a filter for "¿cuánto gasté este mes?" is a
    different failure that this rule has no opinion about."""
    assert plan_verifier.disagreement("¿cuánto gasté este mes?", plan("GROCERIES")) is None


def test_the_rule_can_be_switched_off_by_name() -> None:
    """Same registry as the others, so `--ablate` can say what it is worth in unsafe executions
    prevented and good answers lost."""
    off = GuardPolicy(disabled=frozenset({RULE}))

    assert (
        plan_verifier.disagreement(
            "¿cuántas veces fui al cine?", plan("RESTAURANTS", metric=Metric.COUNT), off
        )
        is None
    )


def test_the_rule_is_in_the_registry() -> None:
    assert RULE in plan_verifier.RULES
