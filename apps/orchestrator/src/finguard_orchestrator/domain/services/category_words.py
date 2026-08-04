"""What each category is called when a person says it out loud.

This lived inside the planner's prompt, which was the right place while the prompt was the only
thing that needed it. Two things need it now: the prompt still tells the model that "el súper" is
GROCERIES, and the explainer has to answer "¿en qué categoría has clasificado el cine?" without
asking a model what its own catalogue means.

Spanish, because these are the words users type — the one exception the English-everywhere rule
makes. The keys are the ledger's identifiers, which are data.

It is deliberately not a two-way mapping built by splitting the glosses. `words_for` renders the
line the prompt shows; `named_in` finds which categories a sentence mentions, and it needs word
boundaries and a longest-match rule that a naive split would not give — "el súper" and "super" are
the same category, and "compras de capricho" must not be found inside a sentence about "la compra".
"""

import re
from typing import Final

from finguard_orchestrator.domain.services import spanish

_GLOSS: Final[dict[str, tuple[str, ...]]] = {
    "GROCERIES": ("el súper", "el supermercado", "la compra", "comida de casa", "supermercado"),
    "TRANSPORT_FUEL": ("gasolina", "gasoil", "diésel", "repostar", "la gasolinera"),
    "RESTAURANTS": ("restaurantes", "bares", "comer fuera", "cenar fuera", "cañas", "café"),
    "TRANSPORT_PUBLIC": ("transporte público", "tren", "metro", "autobús", "taxi", "VTC"),
    "HEALTH": ("salud", "farmacia", "médico", "dentista", "óptica"),
    "LEISURE": ("ocio", "cine", "libros", "videojuegos", "deporte", "compras de capricho"),
    "SUBSCRIPTIONS": ("suscripciones", "cuotas mensuales", "streaming", "gimnasio"),
    "HOUSING_RENT": ("alquiler", "la renta", "la casa", "la vivienda"),
    "SALARY": ("nómina", "sueldo", "salario", "la paga"),
    "TRANSFER_BIZUM": ("bizum", "transferencias entre personas"),
}

# What a category is called in a sentence the user reads. Not the gloss: a list of five synonyms is
# for a model choosing between them, and a person reading an explanation wants one word.
_NAMES: Final[dict[str, str]] = {
    "GROCERIES": "supermercado",
    "TRANSPORT_FUEL": "gasolina",
    "RESTAURANTS": "restaurantes",
    "TRANSPORT_PUBLIC": "transporte público",
    "HEALTH": "salud",
    "LEISURE": "ocio",
    "SUBSCRIPTIONS": "suscripciones",
    "HOUSING_RENT": "alquiler",
    "SALARY": "nómina",
    "TRANSFER_BIZUM": "bizum",
}


def words_for(category: str) -> str | None:
    """The gloss the prompt shows, or None for a category nobody described."""
    words = _GLOSS.get(category)
    return ", ".join(words) if words else None


def name_of(category: str) -> str:
    """One word for a person. Falls back to the identifier, which beats inventing a word."""
    return _NAMES.get(category, category)


# The words that mean exactly one category, for the gate that refuses a misread one.
#
# A deliberate subset of the glossary above, and the exclusions are the design. "La compra" and "la
# casa" are in the glossary because a model choosing between ten categories benefits from them;
# they are not here because somebody asking about "cosas de la casa" may well mean the supermarket,
# and a gate that forced HOUSING_RENT there would break correct plans in the name of fixing wrong
# ones. Same for "café" (a coffee or a coffee machine), "deporte" (a gym subscription or a bike),
# "libros" (leisure or a course) and "la paga" (a salary or pocket money).
#
# **Shop names are not here either, and leaving them in was a real mistake.** The first version
# listed "mercadona" and "carrefour" under GROCERIES, and the gate then refused the two dataset
# cases whose correct plan filters by *merchant* and names no category at all. A shop is a merchant.
#
# "Transporte" is out for the same reason as "la casa": in one dataset question it means the public
# kind and in another it is the parent of both, and a gate cannot be right about both.
#
# What is left is equivalences the product itself defines: cine is ocio the way EUR is euros.
_PRECISE: Final[dict[str, tuple[str, ...]]] = {
    "GROCERIES": ("supermercado", "súper"),
    "TRANSPORT_FUEL": ("gasolina", "gasoil", "diésel", "gasolinera", "repostar"),
    "RESTAURANTS": ("restaurantes", "restaurante", "comer fuera", "cenar fuera"),
    "TRANSPORT_PUBLIC": ("transporte público", "metro", "autobús", "taxi"),
    "HEALTH": ("salud", "farmacia", "dentista", "óptica"),
    "LEISURE": ("cine", "videojuegos", "ocio"),
    "SUBSCRIPTIONS": ("suscripciones", "suscripción", "gimnasio", "streaming"),
    "HOUSING_RENT": ("alquiler",),
    "SALARY": ("nómina", "sueldo", "salario"),
    "TRANSFER_BIZUM": ("bizum", "bizums"),
}


def _matches(sentence: str, vocabulary: dict[str, tuple[str, ...]]) -> tuple[str, ...]:
    haystack = spanish.folded(sentence)
    found: dict[str, int] = {}
    for category, words in vocabulary.items():
        for word in sorted(words, key=len, reverse=True):
            match = re.search(
                rf"(?<![a-z0-9]){re.escape(spanish.folded(word))}(?![a-z0-9])", haystack
            )
            if match:
                # The earliest position at which this category is mentioned, so the order of the
                # answer follows the order of the question.
                found.setdefault(category, match.start())
                break
    return tuple(sorted(found, key=lambda category: found[category]))


def named_in(sentence: str) -> tuple[str, ...]:
    """Which categories this sentence mentions, in the order their words appear.

    Longest gloss first, so "compras de capricho" is found as LEISURE before "la compra" can claim
    the same words for GROCERIES. Word boundaries, so "café" is not found inside a merchant name
    that merely contains it.

    Case and accents are folded: somebody typing "cafe" or "CINE" means the same thing, and a match
    that needed the exact spelling would answer "I do not know that word" to a word it knows.
    """
    return _matches(sentence, _GLOSS)


def precisely_named_in(sentence: str) -> tuple[str, ...]:
    """Which categories this sentence names *unambiguously*, in the order they appear.

    The gate reads this rather than `named_in`, and the difference is the point: `named_in` answers
    "what is this sentence about" for an explanation, where a loose match costs a slightly wrong
    sentence. This answers "what must the plan filter by", where a loose match costs a refused plan
    that was right.
    """
    return _matches(sentence, _PRECISE)
