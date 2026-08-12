"""Nobody types accents into a chat box.

From the reported conversation, verbatim:

    "De esos gastos cuantos fueron en el supermercado ?"
    → "449,62 € el mes pasado, en 8 movimientos."

A "cuántos" question answered with an amount is exactly what `COUNT_ANSWERED_AS_AMOUNT` exists to
refuse, and it did not fire — its pattern is `\\bcuánt[oa]s\\b` and the user typed "cuantos". The
figure was a total presented as an answer to "how many", which is the same class of error as the
largest-expense one: a real number about a different question.

The guard's families survived by accident, because whoever wrote them happened to spell the
alternatives out as `[áa]`. Relying on remembering that in every future pattern is not a rule, it is
a habit — so the sentence is folded before matching and the patterns are written unaccented.

Folding already existed in three places (the category vocabulary, the window vocabulary, and a
private copy in each) and neither of the two rule sets used it.
"""

import pytest

from finguard_orchestrator.domain.enums import Direction, Metric, Period
from finguard_orchestrator.domain.services import (
    capability_guard,
    payment_router,
    plan_verifier,
    spanish,
)
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan, RelativePeriod

SPENT_ON_GROCERIES = FinancialPlan(
    direction=Direction.EXPENSE,
    period=RelativePeriod(period=Period.LAST_MONTH),
    categories=("GROCERIES",),
    metric=Metric.SUM,
)


def test_folding_is_one_function_now() -> None:
    """Three private copies existed and the two rule sets that needed it had none."""
    assert spanish.folded("¿Cuántos días?") == "¿cuantos dias?"
    assert spanish.folded("MÁS") == "mas"
    assert spanish.folded("ñ") == "n", "ñ folds too, which is what makes 'anos' match 'años'"


@pytest.mark.parametrize(
    "sentence",
    [
        "De esos gastos cuantos fueron en el supermercado ?",
        "De esos gastos, ¿cuántos fueron en el supermercado?",
        "cuantas compras hice en el supermercado",
        "¿cuántas compras hice en el supermercado?",
    ],
)
def test_a_count_question_is_caught_with_or_without_accents(sentence: str) -> None:
    assert plan_verifier.disagreement(sentence, SPENT_ON_GROCERIES) == "COUNT_ANSWERED_AS_AMOUNT"


@pytest.mark.parametrize(
    ("sentence", "family"),
    [
        ("he gastado mas este mes que el anterior", "COMPARISON"),
        ("¿he gastado más este mes que el anterior?", "COMPARISON"),
        ("que parte de mi sueldo se va en ocio", "RATIO"),
        ("¿qué parte de mi sueldo se va en ocio?", "RATIO"),
        ("cual es mi gasto medio", "AVERAGE"),
        ("deberia gastar menos", "ADVICE"),
        ("¿debería gastar menos?", "ADVICE"),
        ("a este ritmo cuanto acabare gastando", "FORECAST"),
        ("¿a este ritmo cuánto acabaré gastando?", "FORECAST"),
        ("envia 20 euros a Marta", "ACTION"),
        ("envía 20 euros a Marta", "ACTION"),
    ],
)
def test_every_family_is_caught_with_or_without_accents(sentence: str, family: str) -> None:
    """Written out per family rather than trusting the fold, because a family whose pattern still
    carries an accent would pass the folded case and fail the accented one — or the reverse."""
    assert capability_guard.unsupported_reason(sentence) == family


@pytest.mark.parametrize("sentence", ["si no contamos el alquiler", "quitando el alquiler"])
def test_a_precise_category_exclusion_is_no_longer_refused(sentence: str) -> None:
    assert capability_guard.unsupported_reason(sentence) is None


@pytest.mark.parametrize(
    "sentence",
    [
        # Still not refusals. Folding widens what matches, so the discriminations that were
        # deliberate have to survive it: a ranking is not a comparison, and "gaste mas" is a verb.
        "en que categoria he gastado mas este mes",
        "¿dónde gasto más?",
        "cuanto gaste en el supermercado el mes pasado",
        "cuantas compras hice en Mercadona",
    ],
)
def test_folding_does_not_widen_a_rule_into_a_false_refusal(sentence: str) -> None:
    assert capability_guard.unsupported_reason(sentence) is None


def test_account_exclusion_is_not_mistaken_for_an_unsupported_category_exclusion() -> None:
    assert capability_guard.unsupported_reason("todas menos la cuenta del fondo") is None


@pytest.mark.parametrize(
    "sentence",
    [
        # What the habit cost, in the rule set that decides whether to start a payment. `envies`
        # was the one form nobody spelled twice, so "que le envíes 20 euros" fell through to
        # `_ASKING` — the "que" it starts with — and was a question for as long as it existed.
        "que le envies 20 euros a Marta",
        "que le envíes 20 euros a Marta",
        "mandale 20 euros a Marta",
        "mándale 20 euros a Marta",
        "pagale 20 euros a Marta",
        "págale 20 euros a Marta",
        "transfierele 20 euros a Marta",
        "transfiérele 20 euros a Marta",
    ],
)
def test_an_order_to_pay_survives_the_accents_it_was_typed_with(sentence: str) -> None:
    """Both spellings of every order form, for the reason the family test above spells them out:
    a pattern still carrying an accent class passes one of the two and fails the other."""
    assert payment_router.is_payment(sentence) is True
    assert payment_router.is_explicit_order(sentence) is True
