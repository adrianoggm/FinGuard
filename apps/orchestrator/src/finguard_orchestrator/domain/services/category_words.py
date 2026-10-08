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
        # The gym as people write it (validation fix 3, G3). A spelling of the kind «gimnasio»
        # (`_KIND_SPELLINGS`), never a kind of its own: no shop of the ledger says «gym».
        "gym",
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

# The member words: the precise words above that name one *kind* of thing inside a category rather
# than the category itself. Every other precise word is the category's own name, said one way or
# another, and that is the distinction this marks: «gimnasio» is a subscription and not every
# subscription is a gym, while «supermercado» is every shop GROCERIES holds. Reported: «¿cuánto
# gasté en el gimnasio este año?» answered every subscription -- Netflix, Spotify and Steam with
# the gym -- because nothing could tell those two apart. `explicit_facts.of_its_kind` reads this
# when the question's plan is built, and the plan carries the kind as a merchant term: the
# category stays, and the bank keeps only the shops whose name contains the word.
#
# A mark on the words above and not a vocabulary of its own: a kind is a precise word of the same
# category, so the gate and this rule cannot disagree about how it is spelled. A precise word that
# is not here is the category's own name, which narrows nothing -- so a word added up there keeps
# the old answer until somebody decides it names a kind.
#
# Out, though the glossary would suggest otherwise: «gasolinera», «coche», «repostar» and the fuels
# are the whole of TRANSPORT_FUEL, because every shop in it is a petrol station; «comer fuera» is
# every restaurant. And only the precise words, for `_PRECISE`'s own reason: «café» is a kind of
# restaurant in the glossary and "Cafe Central" a shop in the ledger, and "¿cuánto gasto en café?"
# is every coffee at every bar, not the one bar whose name says so.
#
# And only the kinds whose shops carry the word in their names -- a gym is "Gimnasio Altafit", a
# cinema "Cines Yelmo", a pharmacy "Farmacia Gran Vía". «Streaming», «dentista», «autobús»,
# «videojuegos» and «apuestas» are kinds too, but the shop rarely says so: the dentist is "Clínica
# Dental Sur", the bus "EMT Madrid", the streaming service "Netflix". For those the rule could only
# ever reach its note, and "ningún comercio lleva «dentista» en el nombre" above the dental clinic
# in the evidence reads as wrong even when it is literally true. They answer the category, with no
# note, as they did before this rule.
#
# What "carries the word" means is the bank's, not ours: a merchant term is a case-insensitive
# substring of the shop's name (`query_compiler`, `strpos(lower(...))`), so «cine» is "Cines Yelmo"
# and "Cinesa" both, and «taxi» is "Taxistas Unidos" too. Those are cinemas and taxis, and the
# category beside the term keeps a shop of any other kind out. It is not accent-folded: the term
# goes as spelled here, «óptica», which a shop that writes "Optica" without the accent does not
# contain.
_KINDS: Final[dict[str, tuple[str, ...]]] = {
    "SUBSCRIPTIONS": ("gimnasio",),
    "HEALTH": ("farmacia", "óptica"),
    "TRANSPORT_PUBLIC": ("metro", "taxi"),
    "LEISURE": ("cine",),
}

# Other ways a kind is said, each read as the kind itself (validation fix 3, G3). «¿Q me cuesta el
# gym al mes?» was every subscription on 2026-10-08, 54,88 € in three, because «gym» was no word
# of the kind. The plan still carries the kind as `_KINDS` spells it, because the term is matched
# against the shop's name and that is the word the name carries: "Gimnasio Altafit" says no «gym».
# A spelling is a precise word of the kind's category, like the kind, so the gate and this rule
# read the sentence alike.
_KIND_SPELLINGS: Final[dict[str, tuple[str, ...]]] = {"gimnasio": ("gym",)}

# B-1 (Wave 3 hardening): the gerund ("quitando") was here; the imperative a holder actually
# types was not. "Quita restaurantes", "elimina restaurantes", "borra restaurantes", "saca
# restaurantes", "fuera restaurantes" and "excluye restaurantes" all named the category and
# nothing marked it negative, so a plan built from `precisely_named_in` alone (never checked
# against `precisely_excluded_in`) read every one of them as the opposite of what was asked —
# "en restaurantes" for a request to drop restaurants, the worst failure mode this vocabulary can
# produce because it is wrong with full confidence. `ya no` (`"ya no cuentes"`) already matched
# through the existing `no\s+cuentes` branch and is not repeated here.
# «Sin» is a whole word (validation fix 3, fix round 2): «no solo restaurantes sino también
# gasolina» read «sino» as «sin» and left the fuel out of the very answer it asked to add it to.
_EXCLUSION_PREFIX: Final = re.compile(
    r"(?:\bsin\b(?:\s+contar|\s+incluir)?|\bquitando|\bquita(?:r)?|\belimina(?:r)?|\bborra(?:r)?"
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
    «Netflix» inside «suscripciones de Netflix» is not this vocabulary talking, and a reader that
    gave up on the whole sentence because it mentioned a category answered the wider question with
    a real figure -- the one failure this service is built around.

    «Gimnasio Altafit» matched by «gimnasio» *is* this vocabulary talking, and the shop reader still
    drops the word -- but no longer so that a question about the gym stays about every subscription.
    That was the old decision, and its reason was only ever not to answer about one gym when the
    holder may pay two: the shop reader returns the first shop that matches. A word that names a
    kind inside its category (`kinds_named_in`) now narrows the plan to *every* shop of the
    holder's that carries it, as a merchant term the plan takes when it is built
    (`explicit_facts.of_its_kind`), and a category called by its own name -- «suscripciones»,
    «supermercado» -- still narrows nothing.

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


def kinds_named_in(sentence: str, category: str) -> tuple[str, ...]:
    """The member words (`_KINDS`) this sentence called `category` by, spelled as the vocabulary
    spells them -- empty unless a kind is *all* it called the category.

    Any other word for the category anywhere in the sentence -- its own name, or a glossary word
    that is not a kind -- means the kind is not all the holder asked about: «suscripciones,
    gimnasio incluido» is every subscription, «metro y tren» is Renfe as well as Metro de Madrid,
    and «taxi o VTC» is Cabify too. Those keep the answer they always had. Both vocabularies,
    because the planner reads the glossary: «tren», «VTC», «libros» and «médico» are not precise
    words, and a check that read only those narrowed every one of the four to the kind's shops. A
    kind behind an exclusion marker («sin el gimnasio») is what the holder asked to leave out, so it
    is not a kind they asked about.

    Read the way `_hits` reads a category -- folded, whole words, one wrong letter in a long one --
    so a sentence the gate recognised as this category is the sentence this recognises.

    The kinds are looked for first and the category's other words only when one was said: every
    sentence a plan is built from passes through here (`explicit_facts.read`), and almost none of
    them says a kind.
    """
    kinds = _KINDS.get(category, ())
    haystack = spanish.folded(sentence)
    said = tuple(
        kind
        for kind in kinds
        if any(
            not _EXCLUSION_PREFIX.search(haystack[max(0, start - 100) : start])
            and not _SET_APART.search(haystack[max(0, start - 100) : start])
            for spelling in (kind, *_KIND_SPELLINGS.get(kind, ()))
            for start in _said_at(spelling, haystack)
        )
    )
    if not said:
        return ()
    folded_kinds = {
        spanish.folded(spelling)
        for kind in kinds
        for spelling in (kind, *_KIND_SPELLINGS.get(kind, ()))
    }
    others = {*_PRECISE.get(category, ()), *_GLOSS.get(category, ())}
    if any(_said_at(word, haystack) for word in others if spanish.folded(word) not in folded_kinds):
        return ()
    return said


def categories_with_kinds() -> tuple[str, ...]:
    """The categories a kind can be named inside of -- the ones `kinds_named_in` can answer for."""
    return tuple(_KINDS)


def category_of_kind(term: str) -> str | None:
    """The category `term` is a kind of, or None when it is not one of the kinds.

    How a merchant term the kinds rule put on a plan is told from one a reader put there for a
    family of its own («apuest», «bet»): only the first belongs to a category, and only it may be
    dropped when the plan moves to another category or widened back to that category's figure.
    """
    folded = spanish.folded(term)
    return next(
        (
            category
            for category, kinds in _KINDS.items()
            if any(spanish.folded(kind) == folded for kind in kinds)
        ),
        None,
    )


# What else puts a kind aside, for `kinds_named_in` alone. Probed by review: «¿y aparte del
# gimnasio?» after a question about subscriptions was answered with the gym, the one shop the holder
# had set apart. Not in `_EXCLUSION_PREFIX`, which builds negative category filters for the gate and
# the readers, and whose reach is measured; here the cost of reading one too many is only the
# category's figure, which is the answer the holder had before the kinds rule.
#
# «No solo» too (validation fix 3, fix round 1, M-1): «no solo el gym, también Spotify» asks about
# more than the gym, and the kind put on the model's plan narrowed it to the gym all the same. «No,
# solo el gym», with its comma, is the narrowing, and is not read here.
_SET_APART: Final = re.compile(
    r"(?:\ba\s*parte\s+de|\bademas\s+de|\bque\s+no\s+sean?|\bno\s+solo)"
    r"(?:\s+(?:el|la|los|las|de|gastos?|movimientos?|y|ni))*"
    r"[a-z0-9\s,/-]{0,60}$"
)


def _said_at(word: str, haystack: str) -> list[int]:
    """Where a folded sentence says `word`: as a whole word, or one wrong letter from it when the
    word is long enough for `_one_wrong_letter`'s rule to hold."""
    folded = spanish.folded(word)
    exact = [
        match.start()
        for match in re.finditer(rf"(?<![a-z0-9]){re.escape(folded)}(?![a-z0-9])", haystack)
    ]
    if exact or " " in folded or len(folded) < _FUZZY_FLOOR or folded in _NEVER_FUZZY:
        return exact
    return [
        token.start()
        for token in _WORDS.finditer(haystack)
        if spanish.one_edit_apart(token.group(), folded)
    ]
