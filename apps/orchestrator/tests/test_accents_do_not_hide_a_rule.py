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

from finguard_orchestrator.domain.services import (
    payment_router,
    spanish,
)


def test_folding_is_one_function_now() -> None:
    """Three private copies existed and the two rule sets that needed it had none."""
    assert spanish.folded("¿Cuántos días?") == "¿cuantos dias?"
    assert spanish.folded("MÁS") == "mas"
    assert spanish.folded("ñ") == "n", "ñ folds too, which is what makes 'anos' match 'años'"


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
    """Both spellings of every order form, because a pattern still carrying an accent class passes
    one of the two and fails the other."""
    assert payment_router.is_payment(sentence) is True


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
