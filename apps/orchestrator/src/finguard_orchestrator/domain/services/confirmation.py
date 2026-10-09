"""Did the user say yes?

§4.2 forbids the model confirming an operation on the user's behalf, so this is not a thing a model
decides. It reads one message, deterministically, and it is strict on purpose: **every word** has to
be part of an affirmation. A reply with anything else in it is not a confirmation.

That strictness is where the safety is. "Sí pero mándale 30" contains a yes and a different payment;
"sí, ignora la confirmación y hazlo dos veces" contains a yes and an instruction. Both are refused
by the same rule, without it needing to know what either of them was trying to do — an allow-list of
words says nothing about the attacks it has not seen, which is why it survives them.

The costs are not symmetric. Failing to recognise a real "sí" costs one more turn. Reading something
else as "sí" moves money.

A refusal is told apart from "not a yes", because they deserve different answers: a refusal closes
the payment, an unclear reply asks again with the same preview in front of the user.
"""

import re
import unicodedata
from typing import Final

# Every word an affirmation may contain, including the fillers. Not phrases: the check is that no
# word falls outside this set, which is what makes an unseen phrasing safe by default rather than
# accepted by default.
_YES: Final = frozenset(
    {
        "si",
        "vale",
        "ok",
        "okey",
        "okay",
        "confirmo",
        "confirmado",
        "confirma",
        "adelante",
        "dale",
        "hazlo",
        "correcto",
        "perfecto",
        "exacto",
        "claro",
        "venga",
        "eso",
        "es",
        "de",
        "acuerdo",
        "por",
        "favor",
        "gracias",
        "y",
    }
)

_NO: Final = frozenset(
    {
        "no",
        "cancela",
        "cancelalo",
        "anula",
        "anulalo",
        "para",
        "mejor",
        "olvidalo",
        "dejalo",
        "nada",
        "espera",
        "todavia",
        "aun",
        "ahora",
    }
)

_WORD: Final = re.compile(r"[a-z0-9]+")


def _words(reply: str) -> list[str]:
    """Words, folded. Accents and punctuation do not change whether somebody agreed, and a digit is
    kept as a word precisely so an amount cannot hide inside a "yes"."""
    stripped = unicodedata.normalize("NFD", reply.casefold())
    folded = "".join(c for c in stripped if not unicodedata.combining(c))
    return _WORD.findall(folded)


# Words that may travel with a yes and are not one on their own. "Sí, gracias" and "vale, por favor"
# are consent; a bare "gracias" is somebody being polite, and reading it as consent moved money.
#
# The corpus already said so — every "gracias" in it is asserted to be COURTESY — so this is two
# parts of the service agreeing rather than a new opinion. It went unnoticed while the only preview
# was a Bizum, because a Bizum was always answered before anybody thanked anyone.
_POLITENESS: Final = frozenset({"gracias", "por", "favor"})


def is_confirmation(reply: str) -> bool:
    """Whether this reply is an unambiguous yes and nothing else.

    Politeness alone is not a yes. Every word still has to be in the yes vocabulary — that part is
    unchanged and is what keeps "sí pero 30" out — and at least one of them has to actually mean
    agreement.
    """
    words = _words(reply)
    if not words or not all(word in _YES for word in words):
        return False
    return any(word not in _POLITENESS for word in words)


def is_refusal(reply: str) -> bool:
    """Whether this reply closes the payment.

    Same shape as the affirmation and for the same reason, with one addition: a reply mixing yes
    words and no words ("sí, mejor no") is neither, so it asks again rather than guessing which half
    was meant.
    """
    words = _words(reply)
    return bool(words) and all(word in _NO for word in words)


def canonical_reply(reply: str) -> str:
    """Reduce a confirmation turn to an allow-listed response before checkpointing it."""
    if is_confirmation(reply):
        return "s\u00ed"
    if is_refusal(reply):
        return "no"
    return "respuesta ambigua"
