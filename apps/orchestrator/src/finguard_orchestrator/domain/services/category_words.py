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
    "GROCERIES": (
        "el súper",
        "el supermercado",
        "la compra",
        "comida de casa",
        "supermercado",
        "supermercados",
    ),
    "TRANSPORT_FUEL": (
        "gasolina",
        "gasoil",
        "diésel",
        "repostar",
        "la gasolinera",
        "el coche",
        "combustible",
    ),
    "RESTAURANTS": (
        "restaurantes",
        "bares",
        "comer fuera",
        "comiendo fuera",
        "cenar fuera",
        "cañas",
        "café",
    ),
    "TRANSPORT_PUBLIC": ("transporte público", "tren", "metro", "autobús", "taxi", "VTC"),
    "HEALTH": ("salud", "farmacia", "médico", "dentista", "óptica"),
    "LEISURE": (
        "ocio",
        "cine",
        "libros",
        "videojuegos",
        "deporte",
        "compras de capricho",
        "apuestas",
        "casa de apuestas",
        "casas de apuestas",
    ),
    "SUBSCRIPTIONS": (
        "suscripciones",
        "subscripciones",
        "cuotas mensuales",
        "streaming",
        "gimnasio",
    ),
    "HOUSING_RENT": ("alquiler", "la renta", "la casa", "la vivienda"),
    "SALARY": ("nómina", "sueldo", "salario", "la paga"),
    "TRANSFER_BIZUM": ("bizum", "transferencias entre personas"),
    "INTEREST": ("intereses", "lo que me da la hucha", "rendimiento"),
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
    "INTEREST": "intereses",
}


def words_for(category: str) -> str | None:
    """The gloss the prompt shows, or None for a category nobody described."""
    words = _GLOSS.get(category)
    return ", ".join(words) if words else None


def name_of(category: str) -> str:
    """One word for a person. Falls back to the identifier, which beats inventing a word."""
    return _NAMES.get(category, category)


def names() -> tuple[str, ...]:
    """One stable, user-facing name for every category in the product catalogue."""
    return tuple(_NAMES.values())


def keys() -> tuple[str, ...]:
    """The ledger's own category identifiers ("GROCERIES", "RESTAURANTS", ...) -- never the
    Spanish words `names()` returns. D-4.5-4 (fix wave): `visual_explanation.py` needs the set of
    real category keys to tell a category-grouped chart's labels from a merchant's or an
    account's, and reading `_NAMES` directly reached across a module boundary into a private
    name. This is that public door."""
    return tuple(_NAMES)


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
    "GROCERIES": ("supermercado", "supermercados", "súper"),
    # "Coche" is here and "transporte" is not, which looks inconsistent until you read the ledger:
    # TRANSPORT_FUEL is 184 movements of BP and Cepsa and there is no insurance, no repairs and no
    # parking category, so spending "on the car" is fuel *entirely* rather than approximately.
    # "Transporte" stays out because it is the parent of two categories and a gate cannot be right
    # about both.
    #
    # What it costs to leave out is measured rather than argued. "¿Qué he gastado con el coche este
    # año?" was answered three different ways by one model on three runs: TRANSPORT_FUEL, then
    # TRANSPORT_PUBLIC — a car is not a bus — and then no filter at all, which reported 14.019,05 €
    # of everything as the car. Any deterministic answer beats a three-way coin flip, and this one
    # also arms `plan_verifier`: with the word recognised, the two wrong readings stop being
    # answers and become refusals.
    #
    # ponytail: true of this ledger. The day a CAR_INSURANCE or a REPAIRS category exists, "coche"
    # becomes the parent of several and belongs out here with "transporte" — the upgrade is to
    # delete this line, not to grow it.
    "TRANSPORT_FUEL": ("gasolina", "gasoil", "diésel", "gasolinera", "repostar", "coche"),
    "RESTAURANTS": (
        "restaurantes",
        "restaurante",
        "comer fuera",
        "comiendo fuera",
        "cenar fuera",
    ),
    "TRANSPORT_PUBLIC": ("transporte público", "metro", "autobús", "taxi"),
    "HEALTH": ("salud", "farmacia", "dentista", "óptica"),
    "LEISURE": ("cine", "videojuegos", "ocio", "apuestas", "casa de apuestas", "casas de apuestas"),
    # "Subscripción" with the b is how a great many Spanish speakers write it, and it is not a
    # different word — it is the same one, influenced by English. It was reported: "que
    # subscripciones tengo activas" named no category at all, so the sentence missed this vocabulary
    # and reached the model, which counted movements. A misspelling that costs a wrong answer is a
    # word this reader has to know.
    "SUBSCRIPTIONS": (
        "suscripciones",
        "suscripción",
        "subscripciones",
        "subscripción",
        "gimnasio",
        "streaming",
    ),
    "HOUSING_RENT": ("alquiler",),
    "SALARY": ("nómina", "sueldo", "salario"),
    "TRANSFER_BIZUM": ("bizum", "bizums"),
    # An equivalence the product itself defines, like cine and ocio. "Intereses" in a sentence
    # about money is what a savings account paid and nothing else — there is no second reading to
    # break by being sure about it, and leaving it out meant the answer depended on the model
    # choosing a category it had only just been told about.
    "INTEREST": ("intereses", "interes"),
}

# B-1 (Wave 3 hardening): the gerund ("quitando") was here; the imperative a holder actually
# types was not. "Quita restaurantes", "elimina restaurantes", "borra restaurantes", "saca
# restaurantes", "fuera restaurantes" and "excluye restaurantes" all named the category and
# nothing marked it negative, so a plan built from `precisely_named_in` alone (never checked
# against `precisely_excluded_in`) read every one of them as the opposite of what was asked —
# "en restaurantes" for a request to drop restaurants, the worst failure mode this vocabulary can
# produce because it is wrong with full confidence. `ya no` (`"ya no cuentes"`) already matched
# through the existing `no\s+cuentes` branch and is not repeated here.
_EXCLUSION_PREFIX: Final = re.compile(
    r"(?:\bsin(?:\s+contar|\s+incluir)?|\bquitando|\bquita(?:r)?|\belimina(?:r)?|\bborra(?:r)?"
    r"|\bsaca(?:r)?|\bexcluye(?:ndo)?|\bexcepto|\bsalvo|\bmenos|\bfuera(?:\s+de)?"
    r"|\bno\s+(?:contamos|cuentes|incluimos|incluyas|metas))"
    r"(?:\s+(?:el|la|los|las|de|gastos?|movimientos?|categorias?|y|ni))*"
    r"[a-z0-9\s,/-]{0,60}$"
)


# How long a word has to be before one wrong letter still identifies it. Measured rather than
# chosen: below seven the neighbourhood fills up with ordinary Spanish — "socio" and "odio" are one
# edit from "ocio", "salid" from "salud", "suelo" and "suelto" from "sueldo". Every one of those
# would filter a query by a category nobody named, which is a wrong figure and worse than the miss
# it fixes.
#
# At seven and above the neighbours are the word's own plurals and conjugations — "gasolinas",
# "dentistas", "alquilar" — which are the same category and were previously missed.
_FUZZY_FLOOR = 7

# Words long enough for the rule and still excluded from it, because they collide with a verb
# everybody uses. "Interés" is one edit from "interesa", "interese" and "intereso": «no me interesa»
# would become a question about savings interest. Exact matching still finds them.
_NEVER_FUZZY: Final = frozenset({"interes", "intereses"})


def _one_wrong_letter(word: str, haystack: str) -> bool:
    """Whether any word of the sentence is this one with a single letter wrong.

    The same rule the payment rails use, for the same reason: "gasolna", "supermercdo" and
    "restaurntes" named no category at all, so the sentence missed every deterministic reader and
    reached the model — which is how "36 movimientos" came back for a question about how many
    subscriptions somebody has. Enumerating the misspellings is enumerating an open set, and it was
    already patched once by hand, one word at a time.
    """
    if len(word) < _FUZZY_FLOOR or word in _NEVER_FUZZY:
        return False
    return any(spanish.one_edit_apart(token, word) for token in _WORDS.findall(haystack))


_WORDS: Final = re.compile(r"[a-z0-9]+")


def _matches(sentence: str, vocabulary: dict[str, tuple[str, ...]]) -> tuple[str, ...]:
    found = _hits(sentence, vocabulary)
    return tuple(sorted(found, key=lambda category: found[category][0]))


def _hits(
    sentence: str, vocabulary: dict[str, tuple[str, ...]]
) -> dict[str, tuple[int, frozenset[str]]]:
    """Every category the sentence names: where it was named, and the words that named it.

    `_matches` only ever needed the position. `words_named_in` needs the words themselves, and
    reading them out of the vocabulary a second time is how two readings of one sentence drift
    apart -- so the rule lives here once and both callers take it from the same place.
    """
    haystack = spanish.folded(sentence)
    found: dict[str, tuple[int, frozenset[str]]] = {}
    for category, words in vocabulary.items():
        for word in sorted(words, key=len, reverse=True):
            folded = spanish.folded(word)
            match = re.search(rf"(?<![a-z0-9]){re.escape(folded)}(?![a-z0-9])", haystack)
            if match is None and " " not in folded and _one_wrong_letter(folded, haystack):
                # Position unknown, so it sorts last among the categories the sentence names. A
                # misspelling is still a mention; where it sits only decides the order of an answer.
                #
                # The words recorded are the sentence's own spelling and not the vocabulary's: a
                # caller subtracting them from the sentence's tokens has to find them there, and
                # "suscripcionse" is what the holder typed.
                found.setdefault(
                    category,
                    (
                        len(haystack),
                        frozenset(
                            token
                            for token in _WORDS.findall(haystack)
                            if spanish.one_edit_apart(token, folded)
                        ),
                    ),
                )
                break
            if match:
                # The earliest position at which this category is mentioned, so the order of the
                # answer follows the order of the question.
                found.setdefault(category, (match.start(), frozenset(_WORDS.findall(folded))))
                break
    return found


def named_in(sentence: str) -> tuple[str, ...]:
    """Which categories this sentence mentions, in the order their words appear.

    Longest gloss first, so "compras de capricho" is found as LEISURE before "la compra" can claim
    the same words for GROCERIES. Word boundaries, so "café" is not found inside a merchant name
    that merely contains it.

    Case and accents are folded: somebody typing "cafe" or "CINE" means the same thing, and a match
    that needed the exact spelling would answer "I do not know that word" to a word it knows.
    """
    return _matches(sentence, _GLOSS)


def words_named_in(sentence: str) -> frozenset[str]:
    """The words this sentence used to name a category, folded, as whole tokens.

    `named_in` answers *which* categories a sentence mentions; this answers *with which words*, and
    the difference is what lets a shop be recognised inside a sentence that also names a category.
    «Gimnasio Altafit» matched by «gimnasio» is this vocabulary talking and must not narrow a
    question about the gym to one gym; «Netflix» inside «suscripciones de Netflix» is not, and a
    reader that gave up on the whole sentence because it mentioned a category answered the wider
    question with a real figure -- the one failure this service is built around.

    Empty for a sentence that names no category, which is the common case and the cheap one.
    """
    named: set[str] = set()
    for _, words in _hits(sentence, _GLOSS).values():
        named |= words
    return frozenset(named)


def precisely_named_in(sentence: str) -> tuple[str, ...]:
    """Which categories this sentence names *unambiguously*, in the order they appear.

    The gate reads this rather than `named_in`, and the difference is the point: `named_in` answers
    "what is this sentence about" for an explanation, where a loose match costs a slightly wrong
    sentence. This answers "what must the plan filter by", where a loose match costs a refused plan
    that was right.
    """
    return _matches(sentence, _PRECISE)


def precisely_excluded_in(sentence: str) -> tuple[str, ...]:
    """Categories explicitly placed behind a negative filter, in sentence order.

    The ordinary matcher intentionally cannot distinguish "alquiler" from "sin alquiler".
    This one keeps the distinction structural: it only returns a precise category word when an
    exclusion marker governs it. A model may still read every other phrase; this covers the cases
    where executing the inclusive interpretation would return the exact opposite answer.
    """
    haystack = spanish.folded(sentence)
    found: dict[str, int] = {}
    for category, words in _PRECISE.items():
        for word in sorted(words, key=len, reverse=True):
            for match in re.finditer(
                rf"(?<![a-z0-9]){re.escape(spanish.folded(word))}(?![a-z0-9])", haystack
            ):
                prefix = haystack[max(0, match.start() - 100) : match.start()]
                if _EXCLUSION_PREFIX.search(prefix):
                    found.setdefault(category, match.start())
                    break
            if category in found:
                break
    return tuple(sorted(found, key=lambda category: found[category]))
