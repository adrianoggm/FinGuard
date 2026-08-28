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
        # The family every other test here pins from the outside: these are the sentences on which
        # CATEGORY_EXCLUSION is supposed to fire. Without them the whole family could be switched
        # off and the suite stayed green, because every test that touched it asserted it did *not*
        # fire.
        "cuanto gaste en gasolina sin contar algunas cosas",
        "cuanto gaste en ocio excepto algunas cosas",
        "cuanto gaste en gasolina si no contamos lo que ya sabes",
        "cuanto gaste en ocio quitando lo de siempre",
        "cuanto gaste en ocio sin contar eso",
        # The verb of the exclusion phrase, alone. «sin contar el alquiler» is what the refusal
        # message recommends to users, and its verb was being read as the name of a shop — which
        # exempted this guard and produced a plan filtered on a merchant called "contar".
        "cuanto gaste en gasolina sin contar",
        "cuanto gaste en gasolina sin incluir",
        # Accented, because that is what this file is about.
        "¿cuánto gasté en gasolina sin contar algunas cosas?",
    ],
)
def test_a_vague_exclusion_beside_a_named_category_is_refused(sentence: str) -> None:
    """The mirror of `test_every_family_is_caught_with_or_without_accents`, which pins the other
    six families and omits this one.

    A vague exclusion has no domain representation. Executing it silently drops the filter and
    answers the unfiltered question, which is a real number about a different question.
    """
    assert capability_guard.unsupported_reason(sentence) == "CATEGORY_EXCLUSION"


@pytest.mark.parametrize(
    "sentence",
    [
        # The other direction, and it matters more than the refusal: a merchant exclusion is
        # expressible, so naming a real shop must keep exempting the guard.
        "cuanto gaste en gasolina sin Mercadona",
        "cuanto gaste en gasolina sin contar Cepsa",
        "cuanto gaste en supermercado excepto Carrefour",
        # Shops whose names are ordinary Spanish words, or open with a determiner. Tightening the
        # vague-exclusion vocabulary must not reach these.
        "cuanto gaste en supermercado sin contar Dia",
        "cuanto gaste en ropa sin El Corte Ingles",
        "cuanto gaste en ocio sin contar Casa del Libro",
        "cuanto gaste en ocio sin contar La Sirena",
        "cuanto gaste en gasolina sin meter Cepsa",
        "¿cuánto gasté en gasolina sin contar Cepsa?",
    ],
)
def test_a_named_shop_still_exempts_the_exclusion_guard(sentence: str) -> None:
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
        "devuelvele los 20 euros a Marta",
        "devuélvele los 20 euros a Marta",
    ],
)
def test_an_order_to_pay_survives_the_accents_it_was_typed_with(sentence: str) -> None:
    """Both spellings of every order form, for the reason the family test above spells them out:
    a pattern still carrying an accent class passes one of the two and fails the other."""
    assert payment_router.is_payment(sentence) is True
    assert payment_router.is_explicit_order(sentence) is True


def test_a_transposition_is_derived_rather_than_remembered() -> None:
    """The other half of the same idea, for the slip that is not an accent.

    A pattern that spells its misspellings out is a list somebody has to extend, and the spelling
    they miss is the one nobody thought of: the balance question carried `tiengo`, `tieno` and
    `tngo` for the verb and nothing at all for "cuánto", so "cunato tengo" was planned as a query.
    """
    assert spanish.transposed("cuanto") == (
        "cuanto",
        "ucanto",
        "caunto",
        "cunato",
        "cuatno",
        "cuanot",
    )


def test_a_transposition_never_reaches_the_word_next_door() -> None:
    """Why this and not `one_edit_apart`, which the closed lexicons already use.

    One edit from "cuanto" is "cuando" and one from "hucha" is "mucha" — a different question and
    a word in every other sentence. Swapping two neighbours reaches neither, which is what makes it
    safe to apply to a question word rather than only to a lexicon of three.
    """
    assert "cuando" not in spanish.transposed("cuanto")
    assert "mucha" not in spanish.transposed("hucha")
    assert spanish.one_edit_apart("cuando", "cuanto") is True
