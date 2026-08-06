"""«Gracias», «hola», «adiós» — the turns that are not about money at all.

Ninety-eight of the corpus's 879 turns are one of these, and until this existed every one of them
reached the financial planner. A language model was invoked to build a query about last month's
spending for the word «gracias», and the answer was whatever it managed to invent — a period
question, or a figure.

**It was found by disagreeing with a model, not by reading the code.** Six of the nine intents were
about money and the seventh was a catch-all, so a pleasantry landed in the catch-all and the
catch-all plans queries. `qwen3.5:9b` answered IDENTITY_OR_HELP for fifty-seven of them, which was
the closest thing available to "this is not about their account", and it was more right than the
rules were. Nothing in the service was wrong on its own; the vocabulary of intents was simply
missing one, and a missing category is invisible until something is forced to choose within it.

**There are two tiers, and the second one needs the conversation.**

«Gracias», «hola» and «adiós» cannot be an answer to anything: nobody replies «gracias» to «¿de
cuánto quieres el bizum?» and means fifteen euros. Those stand on their own.

«Vale», «perfecto», «eso es», «ya» are manners after an answer and might be an answer while
something is waiting for one — and thirty of the corpus's turns are a bare «vale», so this is not a
corner to wave at. They only count when `conversation.at_rest`: nothing open, nothing asked. The
proof that the distinction is real is «bueno», which appears four times and is inside a payment for
three of them.

«Sí» is in neither tier and never will be. It is the word that confirms a payment, and a rule that
could read it as manners is a rule that could swallow a confirmation. It is intercepted before any
of this anyway; the exclusion is here so nobody adds it later for symmetry.
"""

import re
from typing import Final

from finguard_orchestrator.domain.services import spanish

# Three kinds, because they get three different replies and a single "de nada" to «hola» reads like
# a machine. Ordered longest-first inside each pattern so «muchas gracias» is matched whole.
_THANKS: Final = re.compile(
    r"^(muchas|mil|muchisimas)?\s*gracias(\s+(a\s+ti|de\s+nada|por\s+todo))?$"
    r"|^(vale|ok|okay|genial|perfecto|pues)\s+gracias$"
    r"|^te\s+lo\s+agradezco$"
    # "Muy amable" was the model's word and not ours: it read it as courtesy, the rules sent it to
    # a planner, and there is no reading under which it is a question about money.
    r"|^(muy\s+amable|mil\s+gracias|un\s+placer)$",
)
_GREETING: Final = re.compile(
    r"^(hola|buenas|hey|holi)$"
    r"|^buenos\s+dias$"
    r"|^buenas\s+(tardes|noches)$"
    r"|^(hola|buenas)[,\s]+(que\s+tal|como\s+estas|buenas)$",
)
_FAREWELL: Final = re.compile(
    r"^(adios|chao|chau|bye)$|^hasta\s+(luego|pronto|otra|manana)$|^nos\s+vemos$",
)

# The second tier: manners only when nothing is waiting for a reply. See the module docstring.
_ACKNOWLEDGEMENT: Final = re.compile(
    r"^(vale|ok|okay|oki|perfecto|genial|estupendo|guay|bueno|ya|entendido|correcto)$"
    r"|^(eso\s+es|de\s+acuerdo|esta\s+bien|muy\s+bien|asi\s+es)$",
)

# Reply text lives here rather than in `answer_writer` because there is nothing to compose: these
# are three constants with no figure, no period and no account in them. A writer earns its own
# module when it has to turn data into a sentence, and this turns nothing into a sentence.
_THANKS_REPLY: Final = "De nada. Si necesitas algo más de tus cuentas, dímelo."
_GREETING_REPLY: Final = "¡Hola! Pregúntame por tus gastos, tu saldo o un bizum."
_FAREWELL_REPLY: Final = "¡Hasta luego!"
_ACKNOWLEDGED_REPLY: Final = "De acuerdo. ¿Necesitas algo más de tus cuentas?"

# Two pleasantries in one turn — «gracias, adiós», «hola, buenas» — which the anchors reject
# separately and which a person writes without thinking twice. Split on the comma, and the turn is
# manners only when *every* half is.
_JOINED: Final = re.compile(r"\s*[,;]\s*|\s+y\s+")


def _cleaned(sentence: str) -> str:
    """Folded and stripped of the punctuation that decorates a pleasantry.

    «GRACIAS», «gracias!!», «¡Hola!» and «Buenas.» are all in the corpus, typed by somebody who was
    not thinking about our regular expressions. Exclamation marks are the whole point of half these
    words, so stripping them is not tidying — it is the difference between matching and not.
    """
    return spanish.folded(sentence).strip(" \t¿?¡!.,;:").strip()


def is_courtesy(sentence: str, *, at_rest: bool) -> bool:
    """Whether this turn is a pleasantry and nothing else.

    Anchored end to end, which is what keeps it narrow: «gracias, ¿y en la hucha?» is a question
    with manners on it and belongs to whatever answers the question. Only a turn that is *entirely*
    a pleasantry has nothing else in it to answer.

    `at_rest` is `Conversation.at_rest` — whether anything is waiting for a reply. It gates the
    acknowledgement tier and nothing else, so «gracias» is manners in the middle of a Bizum and
    «vale» is not.
    """
    return reply(sentence, at_rest=at_rest) is not None


def reply(sentence: str, *, at_rest: bool) -> str | None:
    """What to say back, or None when this is not a pleasantry.

    One function for both because the caller that needs the text has already asked the question,
    and a service where `is_courtesy` and `reply` can disagree is a service with two vocabularies.
    """
    cleaned = _cleaned(sentence)
    halves = [half for half in _JOINED.split(cleaned) if half]
    if len(halves) > 1:
        # Every half has to be manners, and the reply is the first half's. «gracias, adiós» answers
        # like a thank-you; «gracias, ¿y en la hucha?» is not courtesy at all because the second
        # half is a question.
        if all(_one(half, at_rest=at_rest) for half in halves):
            return _one(halves[0], at_rest=at_rest)
        return None
    return _one(cleaned, at_rest=at_rest)


def _one(cleaned: str, *, at_rest: bool) -> str | None:
    """One already-folded pleasantry, or None."""
    if _THANKS.search(cleaned):
        return _THANKS_REPLY
    if _GREETING.search(cleaned):
        return _GREETING_REPLY
    if _FAREWELL.search(cleaned):
        return _FAREWELL_REPLY
    if at_rest and _ACKNOWLEDGEMENT.search(cleaned):
        return _ACKNOWLEDGED_REPLY
    return None
