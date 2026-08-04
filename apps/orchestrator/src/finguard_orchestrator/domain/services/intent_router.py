"""What kind of thing the user just said.

There used to be two answers — payment or financial query — so everything that was neither reached
the query planner and came back as a refusal about averages and forecasts. "¿Quién eres?" was
answered with *"solo consulto lo que ya ha pasado, sin medias, comparaciones ni previsiones"*: true
about the product, and not an answer to the question.

Deterministic, like the payment router and for the same reason. Which conversation this is must
not depend on the disposition of whichever model happens to be configured — and three of these five
need no model at all, because the answer is in the previous plan or in this repository.

**The order is the design.**

    LIST_CONTACTS      who can be paid. *Before* PAYMENT, which is the one place that rule is
                       reversed — see the pattern for why it is safe.
    PAYMENT            then this. It is the one where being wrong costs money.
    IDENTITY_OR_HELP   about the assistant. Answered from here, never from the ledger.
    CORRECTION         "no me has entendido". What matters is what happens next: a correction must
                       not inherit the plan it is correcting.
    EXPLAIN_PREVIOUS   about the last answer. Reads the plan, not the ledger.
    ACCOUNT_BALANCE    how much money there is. A state, not a sum over a window — and the one
                       question the financial planner cannot express at all.
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
    ACCOUNT_BALANCE = "ACCOUNT_BALANCE"
    LIST_CONTACTS = "LIST_CONTACTS"
    FINANCIAL_QUERY = "FINANCIAL_QUERY"


# How much money there is, which is a state and not a sum over a window.
#
# The first question of the reported conversation — "¿podrías decirme cuánto dinero tengo en mis
# cuentas?" — came back as *"no he encontrado movimientos del 2026-08-04 al 2026-08-04"*. This is
# where that starts: there were five intents and none of them was the balance, so it fell through
# to the financial planner, and `FinancialPlan` cannot express a balance at all. The model built the
# nearest thing available to it, which was today's movements.
#
# **The discriminator is the verb.** "Tengo", "queda", "dispongo" and "saldo" are about a state;
# "gasté", "ingresé" and "he gastado" are sums over a period. The two overlap in "¿cuántos
# movimientos tengo este mes?", so anything counting movements is excluded explicitly: that is a
# question the planner answers well, and answering it with a balance would be the same class of
# error this fixes — a real figure about something else.
_ABOUT_THE_BALANCE: Final = re.compile(
    r"\bsaldos?\b"
    r"|\bcu[áa]nto\s+(dinero\s+)?(tengo|ten[íi]a|me\s+queda|hay)\b"
    r"|\bcu[áa]nto\s+(tengo\s+)?disponible\b"
    r"|\bde\s+cu[áa]nto\s+dispongo\b"
    r"|\bdinero\s+(disponible|tengo)\b",
    re.IGNORECASE,
)

# What "tengo" means when it is not about the balance. Checked first, so a question that counts
# movements or lists things keeps going to the path that can answer it.
_NOT_THE_BALANCE: Final = re.compile(
    r"\bcu[áa]nt[oa]s?\s+(movimientos|compras|cargos|pagos|operaciones|veces|bizums?)\b"
    r"|\bgast[éeoa]|\bingres[éeoa]|\bhe\s+gastado\b|\bme\s+he\s+gastado\b"
    r"|\bqu[ée]\s+(contactos|tarjetas|cuentas)\b",
    re.IGNORECASE,
)


# Who can be paid, which is a question and not an order.
#
# From the reported conversation: "¿podrías decirme qué contactos tengo para mandar bizum?" was read
# as a payment — "bizum" is a payment word — so the assistant asked for the recipient of a payment
# nobody had asked to make. The list has been on /v1/contacts since it was written; the conversation
# could not reach it.
#
# **Checked before PAYMENT**, reversing the rule that payments come first. That rule exists because
# being wrong about a payment costs money, and listing contacts cannot move any — so here the risk
# runs the other way, and the expensive mistake is refusing to answer a question. What keeps it safe
# is narrowness: it needs the word "contactos", or an explicit "a quién puedo". No order to pay
# contains either, which is asserted rather than assumed.
_ABOUT_CONTACTS: Final = re.compile(
    r"\bcontactos?\b"
    r"|\ba\s+qui[ée]n\s+(le\s+)?(puedo|podr[íi]a|puedes|se\s+(le\s+)?puede)\b",
    re.IGNORECASE,
)


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
    if _ABOUT_CONTACTS.search(sentence):
        # Before the payment check, and the comment on the pattern is the argument for why. A
        # sentence naming a contact list is asking who exists, even when it also says "bizum".
        return Intent.LIST_CONTACTS
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
    if _ABOUT_THE_BALANCE.search(sentence) and not _NOT_THE_BALANCE.search(sentence):
        # Last of the deterministic ones, and not gated on `has_previous`: "¿cuánto tengo?" is a
        # whole question on the first turn. The exclusion is what keeps "¿cuántos movimientos
        # tengo este mes?" on the path that can answer it.
        return Intent.ACCOUNT_BALANCE
    return Intent.FINANCIAL_QUERY
