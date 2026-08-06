"""Which of the holder's accounts a sentence is talking about.

"¿Cuánto tengo?" stopped having one answer the day a holder had a current account, a piggy bank and
a fund. Most of the time the sentence says which one — "en la hucha", "en el fondo" — and this is
what reads it, so that the total with a breakdown under it is what somebody gets when they genuinely
did not say.

It answers with a `kind` and not with a name. The names belong to the holder ("Hucha", "Fondo
indexado") and change; the three kinds belong to the ledger and do not.

Spanish in the table and English around it, like `category_words` and for the same reason: these
are words users type, which makes them data. What they map to is `accounts.kind`, which is the
ledger's own vocabulary.
"""

import re
from typing import Final

from finguard_orchestrator.domain.services import spanish

# Keyed on `accounts.kind` (migration 0014).
#
# What is deliberately *not* in here is "cuenta" on its own, and it is the most tempting entry on
# the list. "¿Cuánto tengo en cuenta?" is the current account to one person and everything to
# another, so reading it as CURRENT would answer a different question with a real figure — and the
# breakdown is a better answer than a guess anyway. It is also a verb: "cuenta cuánto gasté" is an
# instruction and names no account, and word boundaries are no help against that.
_KINDS: Final[dict[str, tuple[str, ...]]] = {
    # "La corriente" without the noun, because that is how the second half of a sentence says it:
    # "en la hucha y en la corriente". Reading only the first half there answers about one account
    # and looks like a complete answer.
    "CURRENT": (
        "la cuenta corriente",
        "cuenta corriente",
        "la corriente",
        "la nómina",
        "la cuenta nómina",
    ),
    "SAVINGS": (
        "la hucha",
        "hucha",
        # Observed clipped spelling in a balance follow-up. Exact alias, not fuzzy matching.
        "huch",
        "los ahorros",
        "la cuenta de ahorro",
        "la cuenta de ahorros",
    ),
    # "al fondo" as well as "el fondo": Spanish contracts "a el" and a sentence about moving money
    # reaches for the preposition every time — "traspasa 100 al fondo" named no account without it.
    "ASSET": (
        "el fondo",
        "al fondo",
        "fondo indexado",
        "el fondo de inversión",
        "la cuenta de inversión",
    ),
}


# Which words may stand where the table writes "la", "el" or "al".
#
# The table used to carry its article literally, so "la hucha" was a phrase and "mi hucha" was not
# a match at all — and neither was "mi cuenta de ahorros", which is how somebody actually asks:
#
#     "¿cuánto dinero tengo en mi cuenta de ahorros?"  →  the total of all three, with a breakdown
#
# A real figure, about everything, to a question that named one account. Possessives are how people
# talk about their own money, and this is a vocabulary about somebody's own accounts.
#
# "Al" is here as a determiner rather than as a separate entry because it *is* one: Spanish
# contracts "a el", and a sentence about moving money reaches for the preposition every time.
_DETERMINERS: Final = r"(?:el|la|los|las|mi|mis|tu|tus|su|sus|al|a\s+la|a\s+mi|una|un)"

# Articles the table writes to mean "a determiner belongs here". A phrase that starts with one is
# matched with any of them; a phrase that does not is matched literally, which is what keeps
# "corriente" — an adjective, and a property of water — from naming an account on its own.
_ARTICLES: Final = ("la ", "el ", "los ", "las ", "al ")

_CUSTOM_ACCOUNT: Final = re.compile(
    r"\bcuenta\s+(?:(?:que\s+se\s+llama|llamada)\s+)?"
    r"(?!corriente\b|nomina\b|de\s+ahorros?\b|de\s+inversion\b|"
    r"cuanto\b|que\b|como\b|cuando\b|donde\b)[a-z][a-z0-9 -]{1,60}",
)
_NOT_A_CHOICE: Final = re.compile(
    r"\b(cuanto|que|como|cuando|donde|gaste|ingrese|movimientos|compras|saldo|"
    r"bizum|pago|transferencia|no|cancel|gracias|vale)\b"
)


def _pattern(word: str) -> str:
    folded = spanish.folded(word)
    for article in _ARTICLES:
        if folded.startswith(article):
            noun = re.escape(folded.removeprefix(article))
            return rf"(?<![a-z0-9]){_DETERMINERS}\s+{noun}(?![a-z0-9])"
    return rf"(?<![a-z0-9]){re.escape(folded)}(?![a-z0-9])"


# Prepositions that point at where money is going. "En" is here because "mete 30 en la hucha" is an
# instruction with a destination, and it is only ever consulted about instructions — a question
# carrying these words never reaches this function, because the router excludes interrogatives from
# a transfer before anything is read.
_TOWARDS: Final = r"(?:al|a|hacia|para|en)"
_CUSTOM_DESTINATION: Final = re.compile(
    rf"\b{_TOWARDS}\s+(?:{_DETERMINERS}\s+)?{_CUSTOM_ACCOUNT.pattern}"
)


def _noun_of(word: str) -> str:
    folded = spanish.folded(word)
    for article in _ARTICLES:
        if folded.startswith(article):
            return folded.removeprefix(article)
    return folded


def named_in(sentence: str) -> tuple[str, ...]:
    """Which kinds of account this sentence names, in the order they appear.

    Empty is the ordinary case and not a failure: "¿cuánto tengo?" names none, and the answer to
    that is the total with every account listed under it. A caller that read empty as an error
    would ask a clarifying question to somebody who asked a perfectly clear one.

    More than one is ordinary too. "La hucha y la cuenta corriente" names two, and the order is the
    order of the question so that an answer can follow it.

    Longest match first, so "la cuenta de ahorros" is found before "la cuenta de ahorro" can claim
    the same words. Case and accents are folded: somebody typing "nomina" means "nómina", and a
    match that needed the exact spelling would fail on a word it knows.

    ponytail: a kind, not an account. With two savings accounts this stops distinguishing them; the
    upgrade is matching `display_name` against the holder's own list, once something returns one.
    """
    haystack = spanish.folded(sentence)
    found: dict[str, int] = {}
    for kind, words in _KINDS.items():
        for word in sorted(words, key=len, reverse=True):
            match = re.search(_pattern(word), haystack)
            if match:
                # The earliest position this kind is mentioned at, so the order of the answer
                # follows the order of the question.
                found.setdefault(kind, match.start())
                break
    return tuple(sorted(found, key=lambda kind: found[kind]))


def destination_in(sentence: str) -> tuple[str, ...]:
    """Which account an instruction moves money *to*, by the preposition in front of it.

    `named_in` answers "which accounts does this sentence mention", in the order it mentions them.
    That is the wrong question for a transfer, and reading the answer as a destination produced
    this:

        "traspasar el total de fondo indexado a la cuenta de ahorros"
        → "Tienes más de una cuenta de ese tipo: Hucha y Fondo indexado. ¿A cuál lo muevo?"

    The sentence says exactly where the money goes. What made it ambiguous was counting the account
    it comes *from* as a candidate to move it *into* — an ambiguity the reader manufactured, and
    then asked the user to resolve.

    Empty when nothing follows a preposition of direction, and the caller falls back to whatever the
    sentence named: "la hucha, mándale 200" is not how anybody writes it, but reading nothing at all
    there would be worse than reading the one account present.
    """
    haystack = spanish.folded(sentence)
    found: dict[str, int] = {}
    for kind, words in _KINDS.items():
        for word in sorted(words, key=len, reverse=True):
            noun = re.escape(_noun_of(word))
            towards = rf"(?<![a-z0-9]){_TOWARDS}\s+(?:{_DETERMINERS}\s+)?{noun}(?![a-z0-9])"
            match = re.search(towards, haystack)
            if match:
                found.setdefault(kind, match.start())
                break
    return tuple(sorted(found, key=lambda kind: found[kind]))


def chosen_among(sentence: str, names: tuple[str, ...]) -> tuple[str, ...]:
    """Which of the holder's own account names this sentence contains, longest first.

    The rest of this module answers with a *kind*, which is right for reading a question — the
    three kinds belong to the ledger and the names belong to the holder and change. It is not
    enough to answer one: asked "¿a cuál de tus cuentas lo muevo? Tienes Hucha y Hucha viajes", a
    reply of "a Hucha viajes" names the same kind as "a Hucha" and resolves nothing.

    Longest first is the whole of it. "Hucha viajes" contains "Hucha", so matching the shorter one
    would move the money into the account the user did not pick — out of the two they were just
    shown, which is worse than not understanding at all.

    The names come from the caller because they come from banking. Nothing here holds a list of
    somebody's accounts, and the day this reads one from a table is the day it stops being about
    words.
    """
    haystack = spanish.folded(sentence)
    found: list[tuple[int, str]] = []
    for name in sorted(names, key=len, reverse=True):
        pattern = rf"(?<![a-z0-9]){re.escape(spanish.folded(name))}(?![a-z0-9])"
        match = re.search(pattern, haystack)
        # A name already claimed by a longer one is the same words read twice.
        if match and not any(taken <= match.start() < taken + len(other) for taken, other in found):
            found.append((match.start(), spanish.folded(name)))
    chosen = {spanish.folded(name): name for name in names}
    return tuple(chosen[folded] for _, folded in sorted(found))


def explicitly_names_a_custom_account(sentence: str) -> bool:
    """Whether the sentence uses the explicit ``cuenta <nombre>`` form."""
    return _CUSTOM_ACCOUNT.search(spanish.folded(sentence)) is not None


def names_a_custom_destination(sentence: str) -> bool:
    """Whether an instruction explicitly points towards ``cuenta <nombre>``."""
    return _CUSTOM_DESTINATION.search(spanish.folded(sentence)) is not None


def _only_a_generic_name(name: str) -> bool:
    folded = spanish.folded(name).strip()
    for determiner in ("mi ", "la ", "el ", "los ", "las "):
        folded = folded.removeprefix(determiner)
    generic = {_noun_of(word) for words in _KINDS.values() for word in words}
    return folded in generic


def custom_chosen_among(sentence: str, names: tuple[str, ...]) -> tuple[str, ...]:
    """Real display names in the sentence, excluding labels that are only generic kinds.

    If two savings accounts are called ``Hucha`` and ``Hucha viajes``, saying ``la hucha`` keeps
    the genuine ambiguity and saying ``Hucha viajes`` resolves it. An exact display-name accident
    may therefore never turn a generic word into an instruction to use the first account.
    """
    custom = tuple(name for name in names if not _only_a_generic_name(name))
    return chosen_among(sentence, custom)


def custom_destination_among(sentence: str, names: tuple[str, ...]) -> tuple[str, ...]:
    """A custom display name after a directional preposition, longest match first."""
    chosen = custom_chosen_among(sentence, names)
    haystack = spanish.folded(sentence)
    directed = tuple(
        name
        for name in chosen
        if re.search(
            rf"\b(?:a|hacia|para|en)\s+(?:(?:la|mi)\s+)?(?:cuenta\s+)?"
            rf"{re.escape(spanish.folded(name))}(?![a-z0-9])",
            haystack,
        )
    )
    return directed


def looks_like_account_choice(sentence: str) -> bool:
    """Whether a short reply could be one of the account names just offered.

    This only gates an already-open account slot. The next layer still compares it exactly with
    the holder's directory, so this predicate can make the assistant repeat a question but cannot
    select an account by itself.
    """
    folded = spanish.folded(sentence).strip(" ¿?¡!.,;:")
    return bool(folded) and len(folded.split()) <= 6 and _NOT_A_CHOICE.search(folded) is None
