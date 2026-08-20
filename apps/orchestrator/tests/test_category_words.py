"""The category vocabulary, and the one wrong letter that used to hide a whole category."""

import pytest

from finguard_orchestrator.domain.services import category_words

# --- one wrong letter still names the category ------------------------------------------------


@pytest.mark.parametrize(
    ("sentence", "category"),
    [
        ("gasolna", "TRANSPORT_FUEL"),
        ("¿cuánto gasté en supermercdo?", "GROCERIES"),
        ("restaurntes", "RESTAURANTS"),
        ("alquier", "HOUSING_RENT"),
        ("suscripcones", "SUBSCRIPTIONS"),
        # The plural and the verb are the same category, and both were missed before.
        ("gasolinas", "TRANSPORT_FUEL"),
        ("alquilar", "HOUSING_RENT"),
    ],
)
def test_a_misspelled_category_is_still_named(sentence: str, category: str) -> None:
    """The same defect as the payment rails, and it was already patched here once by hand — one word
    at a time, which is enumerating an open set.

    A missed category is total: the sentence names none, the verifier has nothing to refuse, and the
    turn reaches the model. That is how "¿qué subscripciones tengo?" came back as 36 movements."""
    assert category_words.precisely_named_in(sentence) == (category,)


@pytest.mark.parametrize(
    "sentence",
    [
        # One edit from ocio, salud and sueldo. Below seven letters the neighbourhood fills up with
        # ordinary Spanish, and a false positive here filters a query by a category nobody named.
        "somos socios",
        "odio esto",
        "salid de aquí",
        "el suelo",
        "algo suelto",
        # Long enough for the rule and excluded from it: "interés" is one edit from the verb
        # everybody uses, and «no me interesa» is not a question about savings interest.
        "no me interesa",
        "eso no me interese",
    ],
)
def test_ordinary_spanish_is_not_a_category(sentence: str) -> None:
    assert category_words.precisely_named_in(sentence) == ()
