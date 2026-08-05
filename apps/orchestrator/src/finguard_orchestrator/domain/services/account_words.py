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
            pattern = rf"(?<![a-z0-9]){re.escape(spanish.folded(word))}(?![a-z0-9])"
            match = re.search(pattern, haystack)
            if match:
                # The earliest position this kind is mentioned at, so the order of the answer
                # follows the order of the question.
                found.setdefault(kind, match.start())
                break
    return tuple(sorted(found, key=lambda kind: found[kind]))
