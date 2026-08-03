"""What kind of thing the user just said.

There used to be two answers — payment or financial query — so everything that was neither reached
the query planner and came back as a refusal about averages and forecasts. "¿Quién eres?" was
answered with *"solo consulto lo que ya ha pasado, sin medias, comparaciones ni previsiones"*: true
about the product, and not an answer to the question.

Deterministic, like the payment router and for the same reason. Which conversation this is must
not depend on the disposition of whichever model happens to be configured — and three of these five
need no model at all, because the answer is in the previous plan or in this repository.

**The order is the design.**

    PAYMENT            first. It is the one where being wrong costs money.
    IDENTITY_OR_HELP   about the assistant. Answered from here, never from the ledger.
    CORRECTION         "no me has entendido". What matters is what happens next: a correction must
                       not inherit the plan it is correcting.
    EXPLAIN_PREVIOUS   about the last answer. Reads the plan, not the ledger.
    FINANCIAL_QUERY    everything left.

`has_previous` gates the two that only mean something after an answer. "¿Por qué me has mostrado
eso?" with nothing behind it is not about anything, and answering "the query was…" with no query
would be inventing one.
"""

import re
from enum import StrEnum
from typing import Final

from finguard_orchestrator.domain.services import payment_router


class Intent(StrEnum):
    PAYMENT = "PAYMENT"
    IDENTITY_OR_HELP = "IDENTITY_OR_HELP"
    CORRECTION = "CORRECTION"
    EXPLAIN_PREVIOUS = "EXPLAIN_PREVIOUS"
    FINANCIAL_QUERY = "FINANCIAL_QUERY"


# About the assistant rather than about the account. `puedes hacer` and not a bare `puedes`, because
# "¿puedes decirme cuánto gasté?" is a question about money phrased politely.
_ABOUT_ITSELF: Final = re.compile(
    r"\b(qui[ée]n\s+eres|qu[ée]\s+eres|c[óo]mo\s+funcionas|c[óo]mo\s+te\s+llamas"
    r"|eres\s+(una?\s+)?(ia|bot|robot|m[áa]quina|humano|persona)"
    r"|qu[ée]\s+(puedes|sabes|sabr[íi]as)\s+hacer|para\s+qu[ée]\s+sirves"
    r"|^\s*ayuda\s*[?!.]*$|qu[ée]\s+es\s+finguard)\b",
    re.IGNORECASE,
)

# The user says the last answer was wrong. Two shapes: naming the misunderstanding, or restating
# what they meant.
_CORRECTING: Final = re.compile(
    r"\b(no\s+me\s+has\s+entendido|no\s+es\s+eso|no\s+era\s+eso|eso\s+no\s+es\s+lo\s+que"
    r"|me\s+refer[íi]a|me\s+refiero|quer[íi]a\s+decir|te\s+has\s+equivocado"
    r"|no\s+te\s+he\s+preguntado)\b",
    re.IGNORECASE,
)

# About the previous answer: how it was reached, or what it was filed under. The `por qué` case is
# narrowed to what *the assistant* did — "¿por qué gasté tanto en junio?" is a question about money,
# and it is not one this pipeline can answer either, but the refusal it deserves is a different one.
_ABOUT_THE_ANSWER: Final = re.compile(
    r"\b(qu[ée]\s+consulta|c[óo]mo\s+(lo\s+)?has\s+(calculado|hecho|sacado)"
    r"|de\s+d[óo]nde\s+(sale|viene|sacas)"
    r"|(por\s+qu[ée]|c[óo]mo)\s+(me\s+)?has\s+(mostrado|ense[ñn]ado|clasificado|puesto|contado)"
    r"|en\s+qu[ée]\s+categor[íi]a|no\s+entiendo\s+(el|este|ese)?\s*desglose"
    r"|qu[ée]\s+has\s+entendido"
    # Asking whether two things are filed together. This is the shape the real question took —
    # "¿es que los restaurantes pertenecen a la misma categoría que el cine?" — and the first
    # version of this router missed it, so it reached the planner and came back asking which
    # period. A question about the catalogue does not have a period.
    r"|misma\s+categor[íi]a|pertenecen?\s+a|cuenta[n]?\s+como|es\s+lo\s+mismo"
    r"|est[áa]n?\s+en\s+la\s+misma)\b",
    re.IGNORECASE,
)


def route(sentence: str, *, has_previous: bool) -> Intent:
    """Which conversation this sentence belongs to.

    `has_previous` is whether there is an answer behind it — a plan the client echoed back. It
    gates the two intents that are *about* that answer, so the same words mean different things on
    the first turn and on the second, which is what "conversation" means.
    """
    if payment_router.is_payment(sentence):
        # Before everything else. A sentence that both complains and asks for money is a payment:
        # the preview and the confirmation are still in front of it, and the other reading would
        # drop a real payment into a path that cannot make one.
        return Intent.PAYMENT
    if _ABOUT_ITSELF.search(sentence):
        return Intent.IDENTITY_OR_HELP
    if has_previous and _CORRECTING.search(sentence):
        return Intent.CORRECTION
    if has_previous and _ABOUT_THE_ANSWER.search(sentence):
        return Intent.EXPLAIN_PREVIOUS
    return Intent.FINANCIAL_QUERY
