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

**The vocabulary is deliberately narrow.** «Sí», «vale», «ok», «claro», «perfecto» and «eso es» are
not here, and each of them is a pleasantry roughly half the time — the other half they answer a
question. «¿De qué periodo?» → «vale» is not gratitude, and «¿lo envío?» → «sí» is a payment. What
is here cannot be an answer to anything: nobody replies «gracias» to «¿de cuánto quieres el bizum?»
and means fifteen euros.

    ponytail: only the unambiguous words. The ambiguous half needs the open slot to disambiguate —
    `conversation.awaiting` and `last_asked` already say — and that is a second reader, not a
    longer list. Worth doing when a measurement says those turns cost something.
"""

import re
from typing import Final

from finguard_orchestrator.domain.services import spanish

# Three kinds, because they get three different replies and a single "de nada" to «hola» reads like
# a machine. Ordered longest-first inside each pattern so «muchas gracias» is matched whole.
_THANKS: Final = re.compile(
    r"^(muchas|mil|muchisimas)?\s*gracias(\s+(a\s+ti|de\s+nada|por\s+todo))?$"
    r"|^(vale|ok|okay|genial|perfecto)\s+gracias$"
    r"|^te\s+lo\s+agradezco$",
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

# Reply text lives here rather than in `answer_writer` because there is nothing to compose: these
# are three constants with no figure, no period and no account in them. A writer earns its own
# module when it has to turn data into a sentence, and this turns nothing into a sentence.
_THANKS_REPLY: Final = "De nada. Si necesitas algo más de tus cuentas, dímelo."
_GREETING_REPLY: Final = "¡Hola! Pregúntame por tus gastos, tu saldo o un bizum."
_FAREWELL_REPLY: Final = "¡Hasta luego!"


def _cleaned(sentence: str) -> str:
    """Folded and stripped of the punctuation that decorates a pleasantry.

    «GRACIAS», «gracias!!», «¡Hola!» and «Buenas.» are all in the corpus, typed by somebody who was
    not thinking about our regular expressions. Exclamation marks are the whole point of half these
    words, so stripping them is not tidying — it is the difference between matching and not.
    """
    return spanish.folded(sentence).strip(" \t¿?¡!.,;:").strip()


def is_courtesy(sentence: str) -> bool:
    """Whether this turn is a pleasantry and nothing else.

    Anchored end to end, which is what keeps it narrow: «gracias, ¿y en la hucha?» is a question
    with manners on it and belongs to whatever answers the question. Only a turn that is *entirely*
    a pleasantry has nothing else in it to answer.
    """
    return reply(sentence) is not None


def reply(sentence: str) -> str | None:
    """What to say back, or None when this is not a pleasantry.

    One function for both because the caller that needs the text has already asked the question,
    and a service where `is_courtesy` and `reply` can disagree is a service with two vocabularies.
    """
    cleaned = _cleaned(sentence)
    if _THANKS.search(cleaned):
        return _THANKS_REPLY
    if _GREETING.search(cleaned):
        return _GREETING_REPLY
    if _FAREWELL.search(cleaned):
        return _FAREWELL_REPLY
    return None
