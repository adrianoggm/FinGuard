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

from finguard_orchestrator.domain.services import category_words

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


def test_the_category_catalogue_has_one_stable_name_per_supported_identifier() -> None:
    assert category_words.names() == (
        "supermercado",
        "gasolina",
        "restaurantes",
        "transporte público",
        "salud",
        "ocio",
        "suscripciones",
        "alquiler",
        "nómina",
        "bizum",
        # What a savings account pays. The ledger has written it since the piggy bank existed and
        # the catalogue did not have it, so it could not be asked about and rendered as "Otros".
        "intereses",
    )


# --- the car, which is fuel in this ledger and nothing else -----------------------------------


@pytest.mark.parametrize(
    "question",
    [
        "¿qué he gastado con el coche este año?",
        "gastos del coche",
        "cuánto me cuesta el coche al mes",
    ],
)
def test_the_car_is_read_as_fuel_rather_than_left_to_the_model(question: str) -> None:
    """One model answered this three ways on three runs: TRANSPORT_FUEL, then TRANSPORT_PUBLIC —
    a car is not a bus — and then no filter at all, which reported 14.019,05 € of everything as
    the car.

    The point is not that fuel is a clever guess. It is that TRANSPORT_FUEL is 184 movements of BP
    and Cepsa and this ledger has no insurance, repairs or parking category, so "spending on the
    car" is fuel entirely rather than approximately. Any deterministic answer beats a three-way
    coin flip, and this one is also correct.
    """
    from finguard_orchestrator.domain.services import category_words

    assert category_words.precisely_named_in(question) == ("TRANSPORT_FUEL",)
