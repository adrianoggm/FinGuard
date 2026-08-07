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
    money arriving          "he recibido", "me ha llegado" — the opposite direction, and never an
                            order however many payment words it carries.
    a payment verb + money  what is left. "bizum" counts as both.
"""

import re
from typing import Final

from finguard_orchestrator.domain.models.contact import Contact
from finguard_orchestrator.domain.services import payment_words, spanish
from finguard_orchestrator.domain.value_objects.payment_draft import PaymentDraft, PaymentField

# Questions about facts. Spanish marks these, and the mark is the signal — unlike the question mark,
# which polite instructions carry too.
_ASKING: Final = re.compile(
    r"\b(cu[áa]nt[oa]s?|qu[ée]|cu[áa]l(es)?|cu[áa]ndo|d[óo]nde|c[óo]mo|por\s+qu[ée])\b",
    re.IGNORECASE,
)

# A *payment* that already happened. A payment verb in the past is a statement or a question about
# it, never an order — and reading it as one would offer to repeat a payment somebody merely
# mentioned.
#
# An auxiliary only counts as past tense when a payment participle follows it. The first version
# matched any `has`, which made "no me has entendido, hazle un bizum de 20 euros a Marta" not a
# payment: a plain instruction, refused as an action because of a complaint in front of it. The past
# that matters here belongs to the verb that moves money, not to whatever else was said.
_ALREADY: Final = re.compile(
    r"\b((he|has|ha|hemos|hab[ée]is|han|hab[ií]a)\s+"
    r"(mandado|enviado|pagado|transferido|ingresado|devuelto"
    r"|hecho\s+(un|una)\s+(bizum|transferencia))"
    r"|mand[eé]|mandaste|envi[eé]|envi[óo]|envi[aá]ste|pagu[eé]|pag[óo]|transfer[ií]"
    r"|hice|hiciste|hizo)\b",
    re.IGNORECASE,
)

# Money arriving, which is never an order to send any.
#
# Its own signal rather than another entry in `_ALREADY`, because it is not about tense. "¿He
# recibido algún bizum de María este mes?" is a question about the past *and* about the opposite
# direction, and it was read as an instruction: "recibido" is not one of the payment participles
# above and the sentence carries no question word, so "bizum" plus "bizum" was enough. The assistant
# asked how much to send, and then — a turn later — "no tengo a María en tus contactos, así que no
# puedo enviarle nada", to somebody asking whether she had sent *them* anything.
#
# Every turn after it was inside a payment nobody had ordered, which is what makes this the
# expensive kind of misreading: it does not produce one wrong answer, it produces a wrong
# conversation.
_RECEIVED: Final = re.compile(
    r"\b(recib[íi]|recibiste|recibi[óo]|recibido|recibimos|recibieron)\b"
    r"|\bme\s+(ha|han|hab[íi]a|hab[íi]an)\s+"
    r"(mandado|enviado|hecho|ingresado|devuelto|llegado|pagado)\b"
    r"|\bme\s+(mand[óo]|envi[óo]|hizo|ingres[óo]|devolvi[óo]|lleg[óo]|pag[óo])\b"
    r"|\bme\s+(mandaron|enviaron|hicieron|ingresaron|devolvieron|llegaron|pagaron)\b",
    re.IGNORECASE,
)

# Sending money to somebody. Conjugations only, never participles: "mandado" and "mandé" are the
# past, and they are handled above rather than here — but leaving them out of this list means a
# sentence has to pass two checks to be read as an order instead of one.
#
# `bi[xz]um` and not `bizum`: reported from the demo, four turns in a row. "Le hacemos un bixum a
# marta" came back "eso no puedo calcularlo todavía", three times, until the user spelled it right.
# One transposed letter in the single word that routes money, and the whole path was unreachable —
# and it is a word most people have never had to write down.
#
# A misspelling of a payment verb is not a different intent, and the risk runs one way: `_MONEY`
# below still has to agree before any of this is read as an order, so a wider verb list cannot make
# a payment out of a sentence with no money in it.
_PAYING: Final = re.compile(
    r"\b(bi[xz]um|transferencia|tra[nm]sferir|transfiere|transfi[eé]rele"
    r"|enviar|env[ií]a|env[ií]ale|mandar|mandrle|manda|m[áa]ndale"
    r"|pagar|paga|p[áa]gale|ingresa|ingr[ée]sale"
    r"|devuelve|devu[eé]lvele)\b",
    re.IGNORECASE,
)

# A fully stated instruction remains an instruction when the user appends ``¿cómo lo hago?`` or
# ``¿verdad?``. The volitional prefix is required; without it, ``¿cómo puedo enviar dinero?`` is a
# capability question and must not start a payment.
_EXPLICIT_ORDER: Final = re.compile(
    r"\b(?:quiero|necesito|voy\s+a|tengo\s+que)\s+"
    r"(?:hacer\s+(?:una?\s+)?)?"
    r"(?:bi[xz]um|transferencia|tra[nm]sferir|enviar|mandar|mandrle|pagar)\b",
    re.IGNORECASE,
)

# A transfer explicitly framed as movement between the holder's accounts belongs to the own-account
# workflow, never to a recipient payment. This is checked before payment vocabulary because the
# word ``transferencia`` is shared by both rails.
_OWN_ACCOUNTS: Final = re.compile(
    r"\bentre\s+(?:mis|las)\s+cuentas\b"
    r"|\bde\s+una\s+(?:de\s+)?mis\s+cuentas\s+a\s+otra\b"
    r"|\bde\s+una\s+cuenta(?:\s+mia)?\s+a\s+otra\s+cuenta\b.{0,35}"
    r"\b(?:mia|mias|tengo\s+yo)\b"
    r"|\bde\s+una\s+cuenta\s+a\s+otra\b.{0,30}\bambas\s+mias\b",
    re.IGNORECASE,
)

# Money in the sentence: a figure, a symbol, the word, or a means of payment that is only ever
# money. Without one, "manda un correo a Marta" would be read as a payment.
_MONEY: Final = re.compile(r"(\d|€|\beuros?\b|\bbi[xz]um\b|\btransferencia\b)", re.IGNORECASE)

# A whole reply that says the user cannot fill the slot yet. Narrow and anchored so "no sé a
# quién tengo en contactos" remains a contact-list question, while a bare "no sé" does not fall
# through to the financial planner and come back asking for a month.
_UNCERTAIN: Final = re.compile(
    r"\s*(no\s+(lo\s+)?se|ni\s+idea|no\s+estoy\s+segur[oa]|"
    r"(aun|todavia)\s+no\s+(lo\s+)?se)\s*[.!?]*\s*"
)

# Resuming a payment after a change of subject has to name both the action and the payment. A bare
# "sigamos" could refer to the financial question in between, while a bare amount is exactly the
# silent resumption this state exists to prevent.
_RESUMING: Final = re.compile(
    r"\b(continuar|continua|continuemos|seguir|sigamos|retomar|retoma|retomemos)\b"
    r".{0,40}\b(bizum|pago|transferencia)\b"
)
_CLASSIFYING_A_MOVEMENT: Final = re.compile(
    r"\b(?:eso|esto|este|ese|el\s+movimiento|el\s+ingreso)\b.{0,25}"
    r"\b(?:es|era|fue)\b.{0,35}\b(?:bizum|nomina|transferencia)\b"
)


def continues(sentence: str, draft: PaymentDraft, directory: tuple[Contact, ...]) -> bool:
    """Whether this sentence answers the question a half-built payment asked.

    Checked *before* `is_payment`, and that ordering is the point. A conversation with a payment
    under way is a state machine, not a sequence of unrelated sentences: "15 euros" is an amount
    because something asked for one, and classifying it afresh is how it reached the financial
    planner and came back asking which month.

    The rule is narrow on purpose — the sentence has to fill a slot that is actually missing. So a
    genuine change of subject in the middle of a payment ("¿cuánto gasté este mes?") is still a
    question, and "no sé" still goes to the model rather than being read as a name.
    """
    missing = draft.missing
    if PaymentField.AMOUNT in missing and payment_words.amount_in(sentence) is not None:
        return True
    return bool(
        PaymentField.RECIPIENT in missing and payment_words.contacts_in(sentence, directory)
    )


def is_uncertain(sentence: str) -> bool:
    """Whether a reply to a missing payment field says only that the user does not know yet."""
    return _UNCERTAIN.fullmatch(spanish.folded(sentence)) is not None


def resumes(sentence: str) -> bool:
    """Whether the user explicitly brings a suspended payment back into focus."""
    return _RESUMING.search(spanish.folded(sentence)) is not None


def is_payment(sentence: str) -> bool:
    """Whether the payment path should handle this sentence.

    Not a guard rule and not switchable: this is dispatch, not policy. A deployment that does not
    want payments withholds the `payments:prepare` scope, and the sentence is then refused for the
    honest reason rather than quietly read as something else.
    """
    folded = spanish.folded(sentence)
    if _OWN_ACCOUNTS.search(folded):
        return False
    if _CLASSIFYING_A_MOVEMENT.search(folded):
        return False
    if _ALREADY.search(sentence) or _RECEIVED.search(sentence):
        return False
    if _ASKING.search(sentence) and _EXPLICIT_ORDER.search(sentence) is None:
        return False
    return bool(_PAYING.search(sentence)) and bool(_MONEY.search(sentence))
