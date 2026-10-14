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

from finguard_orchestrator.domain.services import category_words

# --- which words are precise enough to gate on -------------------------------------


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
