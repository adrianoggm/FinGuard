"""Is this sentence an instruction to pay somebody, or a question?

The "Intent" step of §4.1, and deterministic on purpose. Asking the model would put the decision to
start a payment inside the component that is explicitly not a source of truth, and it would answer
differently between two models — we have measured two of them 60 points apart on disposition alone.

The costs are not symmetric, and the rules are shaped around that:

    a question read as a payment      the extraction finds no grounded amount; refused
    a payment read as a question      the capability guard refuses it as an action
    another action read as a payment  refused the same way as a question read as one

None of the three moves money, because the confirmation lives on the other side of a second
request. So the rules lean towards reading a sentence as a question, which is the harmless side.

Three signals, and the order matters:

    a question word         "¿cuánto...", "¿qué..." — a question about facts, never an order. Note
                            that a question *mark* is not enough: "¿puedes hacerle un bizum?" is
                            an instruction phrased politely.
    the past               "le he mandado", "le mandé" — a statement about what happened. This is
                            the costly direction: somebody asking what they sent last month must
                            not be shown a prompt to send it again.
    a payment verb + money  what is left. "bizum" counts as both.
"""

import re
from typing import Final

# Questions about facts. Spanish marks these, and the mark is the signal — unlike the question mark,
# which polite instructions carry too.
_ASKING: Final = re.compile(
    r"\b(cu[áa]nt[oa]s?|qu[ée]|cu[áa]l(es)?|cu[áa]ndo|d[óo]nde|c[óo]mo|por\s+qu[ée])\b",
    re.IGNORECASE,
)

# Already happened. A payment verb in the past is a statement or a question about it, never an
# order — and reading it as one would offer to repeat a payment somebody merely mentioned.
_ALREADY: Final = re.compile(
    r"\b(he|has|ha|hemos|hab[ée]is|han|hab[ií]a|hice|hiciste|hizo|hecho"
    r"|mand[eéo]|mandaste|envi[eéo]|envi[aá]ste|pagu[eé]|pag[oó]|transfer[ií])\b",
    re.IGNORECASE,
)

# Sending money to somebody. Conjugations only, never participles: "mandado" and "mandé" are the
# past, and they are handled above rather than here — but leaving them out of this list means a
# sentence has to pass two checks to be read as an order instead of one.
_PAYING: Final = re.compile(
    r"\b(bizum|transferencia|transferir|transfiere|transfi[eé]rele"
    r"|env[ií]a|env[ií]ale|manda|m[áa]ndale|paga|p[áa]gale|ingresa|ingr[ée]sale"
    r"|devuelve|devu[eé]lvele)\b",
    re.IGNORECASE,
)

# Money in the sentence: a figure, a symbol, the word, or a means of payment that is only ever
# money. Without one, "manda un correo a Marta" would be read as a payment.
_MONEY: Final = re.compile(r"(\d|€|\beuros?\b|\bbizum\b|\btransferencia\b)", re.IGNORECASE)


def is_payment(sentence: str) -> bool:
    """Whether the payment path should handle this sentence.

    Not a guard rule and not switchable: this is dispatch, not policy. A deployment that does not
    want payments withholds the `payments:prepare` scope, and the sentence is then refused for the
    honest reason rather than quietly read as something else.
    """
    if _ASKING.search(sentence) or _ALREADY.search(sentence):
        return False
    return bool(_PAYING.search(sentence)) and bool(_MONEY.search(sentence))
