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
from finguard_orchestrator.domain.services import account_words, payment_words, spanish
from finguard_orchestrator.domain.value_objects.payment_draft import PaymentDraft, PaymentField

# Questions about facts. Spanish marks these, and the mark is the signal — unlike the question mark,
# which polite instructions carry too.
_ASKING: Final = re.compile(
    r"\b(cu[áa]nt[oa]s?|qu[ée]|cu[áa]l(es)?|cu[áa]ndo|d[óo]nde|c[óo]mo|por\s+qu[ée])\b",
    re.IGNORECASE,
)

# The past of the verbs that move money, conjugated rather than remembered.
#
# The lists this replaces were written by hand and were missing forms: `mandé` and `mandaste` but
# neither `mandamos` nor `mandasteis`, `había mandado` but not `habíamos`. This is the one
# vocabulary where a gap fails towards the dangerous side — a past form nobody enumerated is read
# as an order, so "¿cuánto le mandé a Marta?" comes back offering to send it again. Completeness is
# the whole property, and a person extending an alternation is not how you get it.
#
# The enumeration itself stays. Widening it to a stem (`mand\w*`) would invert what the failure
# costs: it would swallow `mándale`, and the payment path is then unreachable for the sentence that
# actually orders a payment.
#
# The preterite in its six persons, the participle, and the imperfect — five spellings for six,
# because `mandaba` is the first person and the third alike. `-ir` conjugates exactly like `-er` in
# every past form, so the two share one list. Unaccented: the sentence is folded before it is
# matched.
_PAST_ER: Final = "i iste io imos isteis ieron ido ia ias iamos iais ian".split()
_PAST_ENDINGS: Final = {
    "ar": "e aste o amos asteis aron ado aba abas abamos abais aban".split(),
    "er": _PAST_ER,
    "ir": _PAST_ER,
}

# What the regular endings get wrong: `pagué` keeps the hard g, `transfirió` changes its stem,
# `devuelto` is not built from `devolv-`. Regular endings on those verbs also produce forms that are
# not words — `devolvido` — and a form that is not a word matches nothing, so they cost nothing.
#
# `hacer` is absent on purpose. It is irregular throughout, and it is also the verb of "hazle un
# bizum": its participle only counts pinned to a payment noun or to "me", never on its own.
_IRREGULAR_PAST: Final = {
    "pagar": ("pague",),
    "llegar": ("llegue",),
    "devolver": ("devuelto",),
    "transferir": ("transfirio", "transfirieron"),
}
_PAYMENT_VERBS: Final = ("mandar", "enviar", "pagar", "transferir", "ingresar", "devolver")
_HABER: Final = "he|has|ha|hemos|habeis|han|habia|habias|habiamos|habiais|habian"
_MADE: Final = "hice|hiciste|hizo|hicimos|hicisteis|hicieron"


def _past(*verbs: str) -> str:
    """Every past form of these verbs, as a regex alternation."""
    forms: set[str] = set()
    for verb in verbs:
        forms.update(verb[:-2] + ending for ending in _PAST_ENDINGS[verb[-2:]])
        forms.update(_IRREGULAR_PAST.get(verb, ()))
    return "|".join(sorted(forms))


# A *payment* that already happened. A payment verb in the past is a statement or a question about
# it, never an order — and reading it as one would offer to repeat a payment somebody merely
# mentioned.
#
# A bare auxiliary is not the past. The first version matched any `has`, which made "no me has
# entendido, hazle un bizum de 20 euros a Marta" not a payment: a plain instruction, refused as an
# action because of a complaint in front of it. The past that matters here belongs to the verb that
# moves money, and only `hacer` needs its auxiliary spelled out — because "he hecho la compra" is
# not a payment and "he hecho un bizum" is.
_ALREADY: Final = re.compile(
    rf"\b(?:{_past(*_PAYMENT_VERBS)})\b"
    rf"|\b(?:{_HABER})\s+hecho\s+(?:un|una)\s+(?:bizum|transferencia)\b"
    rf"|\b(?:{_MADE})\b"
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
#
# `me` is what pins the direction, so behind it the auxiliary is optional and `hecho` needs no
# payment noun: "me han hecho un bizum" and "me hicieron uno" are both money arriving. `llegar`
# belongs to this half only — money never leaves by arriving.
_RECEIVED: Final = re.compile(
    rf"\b(?:{_past('recibir')})\b"
    rf"|\bme\s+(?:(?:{_HABER})\s+)?(?:{_past(*_PAYMENT_VERBS, 'llegar')}|hecho|{_MADE})\b"
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
    # The second-person subjunctive, which is the form the order above is given in: "que le mandes
    # 20 euros". These are payment verbs and nothing else — unlike "hagas", which is deliberately
    # absent because "que hagas el desglose" is not a payment.
    r"|mandes|env[ií]es|pagues|transfieras"
    r"|devuelve|devu[eé]lvele)\b",
    re.IGNORECASE,
)

# A fully stated instruction remains an instruction when the user appends ``¿cómo lo hago?`` or
# ``¿verdad?``. The volitional prefix is required; without it, ``¿cómo puedo enviar dinero?`` is a
# capability question and must not start a payment.
_EXPLICIT_ORDER: Final = re.compile(
    r"\b(?:quiero|necesito|voy\s+a|vamos\s+a|tengo\s+que)\s+"
    r"(?:hacer\s+(?:una?\s+)?|dar(?:le|les|me)?\s+(?:el\s+|un\s+|una\s+)?)?"
    r"(?:bi[xz]um|transferencia|tra[nm]sferir|enviar|mandar|mandrle|pagar)\b",
    re.IGNORECASE,
)

# The same instruction in the subjunctive, which is how Spanish actually gives one: "que le hagas el
# bizum", "quiero que hagas un bizum". Reported, four turns in a row and then three more — every one
# of them read as a question, because they start with "que" and `_ASKING` cannot tell that "que"
# from an interrogative. The user ended up shouting.
#
# **The second person is what makes this safe.** "Hagas" is addressed to the assistant and nobody
# asks a capability question with it — "¿cómo hago un bizum?" is first person, "¿puedes hacer un
# bizum?" is an infinitive. So this needs no volitional prefix, unlike the rule above, and it still
# cannot fire without `_PAYING` and `_MONEY` agreeing further down.
#
# `que\s*h?\s*agas` tolerates one transposed space, for the same reason `bi[xz]um` tolerates one
# transposed letter: "QUe quiero queh agas un bizum" was a real turn, and it was the third attempt.
_SUBJUNCTIVE_ORDER: Final = re.compile(
    r"\bque\s*h?\s*(?:agas|agais)\b|\bque\s+(?:me|le|les|nos)\s*h?\s*(?:agas|agais)\b"
    r"|\bque\s+(?:me|le|les|nos)\s+(?:mandes|envies|pagues|transfieras|des)\b"
    r"|\bque\s+(?:mandes|envies|pagues|transfieras)\b",
    re.IGNORECASE,
)

# A question about what this assistant can do. It names a rail and orders nothing: "¿puedes hacer
# transferencias?" is about the product.  A second copy of a rule the intent router already has, and
# that is a cost worth naming. The router catches it first on the deterministic path, but
# `is_payment` is consulted directly now that a model answering the catch-all no longer settles the
# routing — so the rule has to hold in both places, and importing the router's copy would be a
# cycle: it imports this module. Narrow and in the safe direction, which is refusing to pay.
_A_CAPABILITY: Final = re.compile(
    r"\b(?:puedes|sabes|podr[íi]as|sabr[íi]as|se\s+puede)\s+(?:hacer\s+)?"
    r"(?:transferencias?|transferir|bi[xz]ums?|pagos?|pagar|ingresos?|domiciliaciones)\b",
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
_ANOTHER_PAYMENT: Final = re.compile(
    r"^\s*(?:y\s+)?(?:ahora\s+)?(?:haz|manda|envia)?\s*(?:un\s+)?otr[oa]\b",
    re.IGNORECASE,
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


def repeats(sentence: str, directory: tuple[Contact, ...]) -> bool:
    """Whether an immediate ellipsis fully states the new amount and grounded recipient.

    ``otro`` alone never starts a payment. The previous successful rail must be present in the
    conversation, and this predicate additionally requires a fresh valid amount and a contact from
    the authenticated holder's directory.
    """
    return bool(
        _ANOTHER_PAYMENT.search(spanish.folded(sentence))
        and payment_words.amount_in(sentence) is not None
        and payment_words.contacts_in(sentence, directory)
    )


# The imperative, which is the third way to give the order: "hazle un bizum a Carmen". The order
# forms of `hacer`, which is the verb that pairs with the rail noun: "hazle un bizum", "hagamos un
# bizum". Every form that commands or proposes, and none that state or ask — "hago" and "hacemos"
# are first person, "hacer" is the infinitive of "¿puedes hacer un bizum?", and those are questions
# about a capability. Written as the stem plus the endings rather than as an alternation of whole
# words, because the alternation is what kept missing one: "hagamos un bisum" was reported after
# "que hagas" had already been added.
_MAKING: Final = re.compile(
    r"\bh(?:az|aga|agas|agamos|agan|aced)(?:me|le|les|nos)?\b",
    re.IGNORECASE,
)

_IMPERATIVE_ORDER: Final = re.compile(
    r"\b(?:manda(?:le|les|me)?|env[ií]a(?:le|les|me)?"
    r"|p[áa]ga(?:le|les)?|transfi[eé]re(?:le)?|ingr[ée]sa(?:le)?)\b",
    re.IGNORECASE,
)


def is_explicit_order(sentence: str) -> bool:
    """Whether the words *instruct*, rather than merely naming a payment.

    The distinction a bare noun cannot survive. "Pues bizum", offered as the answer to "¿de qué
    comercio o categoría?", contains a payment word and no instruction — and reading it as an order
    asked the user who to send money to, about a payment nobody had ordered. "Que le hagas el bizum"
    and "hazle un bizum a Carmen" are instructions in every sense, and an open question about a
    period has no claim on them.

    Three forms because Spanish has three: volitional ("quiero hacer un bizum"), subjunctive ("que
    le hagas un bizum") and imperative ("hazle un bizum"). Missing two of them is what let an open
    clarification swallow seven consecutive attempts to send somebody twenty-five euros.
    """
    return bool(
        _EXPLICIT_ORDER.search(sentence)
        or _SUBJUNCTIVE_ORDER.search(sentence)
        or _IMPERATIVE_ORDER.search(sentence)
        or _MAKING.search(sentence)
    )


def is_payment(sentence: str) -> bool:
    """Whether the payment path should handle this sentence.

    Not a guard rule and not switchable: this is dispatch, not policy. A deployment that does not
    want payments withholds the `payments:prepare` scope, and the sentence is then refused for the
    honest reason rather than quietly read as something else.
    """
    folded = spanish.folded(sentence)
    if _OWN_ACCOUNTS.search(folded):
        return False
    if account_words.destination_in(sentence):
        # The money is going to one of the holder's own account *kinds*, so there is nobody to pay.
        # Asked of the account vocabulary rather than added to the phrase list, which knew only
        # the explicit framings — "entre mis cuentas" and three variations. "Pasa 100 € del
        # fondo a la hucha" was none of them, so it reached the payment path and came back "no tengo
        # a Fondo indexado en tus contactos": an account answered as a person who does not exist.
        #
        # Both tests, not one. The phrase list still catches "quiero traspasar dinero entre mis
        # cuentas", which names no account at all and so has no destination to read.
        return False
    if _CLASSIFYING_A_MOVEMENT.search(folded):
        return False
    if _A_CAPABILITY.search(sentence):
        return False
    if _ALREADY.search(folded) or _RECEIVED.search(folded):
        return False
    if (
        _ASKING.search(sentence)
        and _EXPLICIT_ORDER.search(sentence) is None
        and _SUBJUNCTIVE_ORDER.search(sentence) is None
    ):
        return False
    # A rail named however it was spelled counts as both the verb and the money, which is what
    # `_MONEY` already said about "bizum" — said here as well so a misspelling keeps that property
    # instead of losing it in two places at once.
    rail = payment_words.names_a_rail(sentence)
    return (bool(_PAYING.search(sentence)) or rail) and (bool(_MONEY.search(sentence)) or rail)
