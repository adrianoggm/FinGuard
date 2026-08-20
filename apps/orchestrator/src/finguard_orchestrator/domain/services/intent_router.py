"""What kind of thing the user just said.

There used to be two answers — payment or financial query — so everything that was neither reached
the query planner and came back as a refusal about averages and forecasts. "¿Quién eres?" was
answered with *"solo consulto lo que ya ha pasado, sin medias, comparaciones ni previsiones"*: true
about the product, and not an answer to the question.

Deterministic, like the payment router and for the same reason. Which conversation this is must
not depend on the disposition of whichever model happens to be configured. Several need no model at
all, because the answer is in the previous plan or in this repository.

**The order is the design.**

    COURTESY           «gracias», «hola», «adiós» and nothing else. First, because a turn that
                       is entirely manners has nothing below it to steal.
    LIST_CONTACTS      who can be paid. *Before* PAYMENT, which is the one place that rule is
                       reversed — see the pattern for why it is safe.
    PAYMENT            then this. It is the one where being wrong costs money.
    IDENTITY_OR_HELP   about the assistant. Answered from here, never from the ledger.
    CORRECTION         "no me has entendido". It patches the plan it corrects and keeps every field
                       the user did not replace.
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

from finguard_orchestrator.domain.services import courtesy, payment_router, payment_words, spanish


class Intent(StrEnum):
    COURTESY = "COURTESY"
    LIST_CARDS = "LIST_CARDS"
    PAYMENT = "PAYMENT"
    IDENTITY_OR_HELP = "IDENTITY_OR_HELP"
    LIST_CATEGORIES = "LIST_CATEGORIES"
    CORRECTION = "CORRECTION"
    EXPLAIN_PREVIOUS = "EXPLAIN_PREVIOUS"
    ACCOUNT_BALANCE = "ACCOUNT_BALANCE"
    OWN_TRANSFER = "OWN_TRANSFER"
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
#
# **And "cuánto" is spelled by a rule now.** The verb beside it already carried three misspellings
# by hand — `tiengo`, `tieno`, `tngo` — and the question word carried none, so "cunato tengo" was a
# balance question answered by the financial planner. Both halves have to survive a slip for the
# sentence to, and enumerating the second list would repeat what the first one taught.
#
# Transpositions and not one whole edit: one substitution from "cuanto" is "cuando", and reading
# "¿cuándo cobro la nómina?" as a balance would be a real figure about a different question.
_HOW_MUCH: Final = "|".join(spanish.transposed("cuanto"))
_ABOUT_THE_BALANCE: Final = re.compile(
    r"\bsaldos?\b"
    rf"|\b(?:{_HOW_MUCH})\s+(dinero\s+)?(tengo|tiengo|tieno|tngo|ten[íi]a|me\s+queda|hay)\b"
    rf"|\b(?:{_HOW_MUCH})\s+dinero\s+dispongo\b"
    rf"|\b(?:{_HOW_MUCH})\s+(tengo\s+)?disponible\b"
    rf"|\bde\s+(?:{_HOW_MUCH})\s+dispongo\b"
    r"|\bdinero\s+(disponible|tengo)\b"
    # "¿Qué cuentas tengo?" — the accounts themselves, which is what a balance answer already is:
    # a total with every account and its figure under it. It was excluded below and nothing else
    # answered it, so it reached the planner and came back "eso no puedo calcularlo todavía" about
    # a list this service holds and had printed one turn earlier.
    r"|\bqu[ée]\s+cuentas\s+(tengo|tiene|hay)\b"
    r"|\bcu[áa]ntas\s+cuentas\s+(tengo|tiene|hay)\b"
    # "El estado actual de mi hucha", "¿cómo está mi cuenta corriente?" — a state, asked without
    # the word "cuánto". It reached the planner and came back "¿de qué periodo te gustaría saber el
    # estado actual de tu hucha de ahorros?": a question about a window, to somebody asking about a
    # balance, quoting their own sentence back at them.
    #
    # Both need an account beside them, which is what keeps "¿en qué estado está el pago?" and
    # "¿cómo va el mes?" out — neither is about how much money there is.
    r"|\b(estado\s+(actual\s+)?(de|en)|c[óo]mo\s+(est[áa]|va|anda))\b.{0,30}"
    r"\b(cuenta|hucha|ahorros?|fondo|n[óo]mina|corriente)\b",
    re.IGNORECASE,
)

# Asking which accounts exist is served by the same deterministic summary as a balance. These
# patterns require catalogue words (list/open/active/how many), so ``gastos de mis cuentas`` stays
# a ledger query.
_ABOUT_ACCOUNTS: Final = re.compile(
    r"\b(?:lista|listado)\s+de\s+(?:mis\s+)?cuentas(?:\s+bancarias)?\b"
    r"|\b(?:cu[áa]les|quales)\s+(?:son\s+)?(?:las\s+|mis\s+)?cuentas"
    r"(?:\s+bancarias)?\b.{0,45}\b(?:tengo|abiertas|activas|actuales)\b"
    r"|\b(?:qu[eé]|cu[áa]ntas)\s+cuentas(?:\s+bancarias|\s+bancarais)?\s+"
    r"(?:tengo|tiene|hay)\b"
    r"|\bmis\s+cuentas\b.{0,35}\b(?:cu[áa]les|abiertas|activas)\b",
    re.IGNORECASE,
)

# What "tengo" means when it is not about the balance. Checked first, so a question that counts
# movements or lists things keeps going to the path that can answer it.
#
# "Cuentas" left this list when the balance learned to answer about them. Contacts and cards stay:
# both have a screen of their own, neither has an answer on this path, and a balance would be a
# real figure about something nobody asked for.
_NOT_THE_BALANCE: Final = re.compile(
    r"\bcu[áa]nt[oa]s?\s+(movimientos|compras|cargos|pagos|operaciones|veces|bizums?)\b"
    r"|\bgast[éeoa]|\bingres[éeoa]|\bhe\s+gastado\b|\bme\s+he\s+gastado\b"
    r"|\bqu[ée]\s+(contactos|tarjetas)\b"
    # The noun on its own, not only after "cuántos". A balance is one figure; the moment a sentence
    # names the *things* that make it up, it is asking for a list however it is phrased.
    #
    # "En el fondo Indexado que movimientos tengo en el utlimo año?" came back as the fund's
    # balance. Eleven words, a verb, and `movimientos` in the middle of it — taken by the balance
    # ellipsis because a misspelt "utlimo" hid the period, and with the period hidden every
    # remaining condition read like a bare account name.
    r"|\b(movimientos|compras|cargos|operaciones)\b",
    re.IGNORECASE,
)


# The cards in the wallet, and what has been charged to each.
#
# The data was always there — `ListCardsQuery` is registered on the bus, the wallet screen draws it,
# and `CardRow` carries `spent_minor` per card. The conversation could not reach any of it, so
# "¿qué tarjetas tengo de crédito?" came back "eso no puedo calcularlo todavía" about a screen the
# user can open, and "¿cuánto he gastado con cada tarjeta este mes?" came back 868,97 € — the
# account total, labelled as an answer about cards.
#
# The same shape as LIST_CONTACTS and fixed the same way: a question with an answer in this
# service does not belong to a model that has no way to express it. A plan has no card dimension
# at all, so the planner could only ever approximate, and an approximation with a euro sign on it
# is the failure this service is built around not having.
#
# Narrow on purpose. It needs the noun, so "¿cuánto gasté con la tarjeta en Mercadona?" — a
# question about spending that mentions how it was paid — is not caught by the plural forms here.
_ABOUT_CARDS: Final = re.compile(
    r"\b(qu[ée]|cu[áa]ntas)\s+tarjetas\b"
    r"|\btarjetas\s+(tengo|tiene|hay|tengo\s+activas)\b"
    r"|\bmis\s+tarjetas\b"
    r"|\bcon\s+cada\s+tarjeta\b"
    r"|\bpor\s+tarjeta\b"
    r"|\bcu[áa]nto\s+.{0,20}\bcada\s+tarjeta\b",
    re.IGNORECASE,
)

_ABOUT_CARDS_BY_EACH: Final = re.compile(
    r"\b(?:tarjetas|tarjtas)\b.{0,100}"
    r"\b(?:cada\s+una|gastos?\s+asociados?|gasto\s+total)\b"
    r"|\bcada\s+una\s+de\s+(?:mis|las)\s+tarjetas\b",
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

_ABOUT_AGENDA: Final = re.compile(
    r"\b(?:quiero|quieo|necesito|puedo)\s+saber\b.{0,90}\b(?:agend(?:a)?|personas)\b"
    r"|\b(?:lista|listado)\s+de\s+(?:las\s+)?personas\s+de\s+mi\s+agend(?:a)?\b"
    r"|\ba\s+qui(?:[ée]n|e)\s+de\s+mi\s+agend(?:a)?\b",
    re.IGNORECASE,
)


# About the assistant rather than about the account. `puedes hacer` and not a bare `puedes`, because
# "¿puedes decirme cuánto gasté?" is a question about money phrased politely.
#
# The list grew from a real conversation where three turns in a row asked what this thing does and
# all three were answered with "¿de qué periodo?":
#
#     "Oye, ¿con qué cosas me puedes asistir / ayudar?"
#     "Del banco me refiero"
#     "Vale, ¿puedes hacer transferencias o solo bizums?"
#
# None of them matched, because the pattern wanted the literal "qué puedes hacer". The answer
# already existed and was good — `explainer.IDENTITY` says exactly what this does — so the whole
# defect was the recogniser, and the whole cost of it was a user concluding the assistant is
# broken. That is the cheapest kind of failure to fix and the most expensive to leave.
#
# Two shapes, and they need different patterns. **What can you do**: a verb of helping, with or
# without "qué". **Can you do X**: `puedes` followed by an operation rather than by a verb of
# telling, which is what separates "¿puedes hacer transferencias?" from "¿puedes decirme cuánto
# gasté?".
_ABOUT_ITSELF: Final = re.compile(
    # "¿Cómo puedo utilizarte?" was refused with "eso no puedo calcularlo todavía" and the very
    # next turn, "¿qué eres capaz de hacer?", was answered in full. The same question twice, and
    # the difference was which verb the person reached for. A third shape: **how do I use you**.
    r"\b(c[óo]mo\s+(puedo|se\s+puede|te\s+puedo)\s+(usar|utilizar|usarte|utilizarte)"
    r"|c[óo]mo\s+(te\s+)?(usas|utilizas)|para\s+qu[ée]\s+(vales|te\s+uso)"
    r"|qu[ée]\s+eres\s+capaz\s+de\s+hacer"
    r"|qui[ée]n\s+eres|qu[ée]\s+eres|c[óo]mo\s+funcionas|c[óo]mo\s+te\s+llamas"
    r"|eres\s+(una?\s+)?(ia|bot|robot|m[áa]quina|humano|persona)"
    r"|qu[ée]\s+(puedes|sabes|sabr[íi]as)\s+hacer|para\s+qu[ée]\s+sirves"
    r"|^\s*ayuda\s*[?!.]*$|qu[ée]\s+es\s+finguard"
    # "¿con qué cosas me puedes ayudar?", "¿en qué me puedes asistir?", "¿qué sabes hacer?"
    r"|(qu[ée]|c[óo]mo)\s+.{0,20}\bme\s+(puedes|podr[íi]as)\s+(ayudar|asistir|echar)"
    r"|\bme\s+(puedes|podr[íi]as)\s+(ayudar|asistir)\b"
    # "¿puedes hacer transferencias?", "¿sabes hacer bizums?", "¿haces pagos?" — an operation, not
    # a request to look something up. Listed rather than a wildcard: "¿puedes hacer una consulta
    # de julio?" is a question about money, and a bare `puedes hacer` would swallow it.
    r"|\b(puedes|sabes|podr[íi]as)\s+(hacer\s+)?"
    r"(transferencias|transferir|bizums|pagos|pagar|ingresos|domiciliaciones)\b"
    r")",
    re.IGNORECASE,
)

_ASSISTANT_NOUN: Final = re.compile(r"\b(?:este\s+)?asistente\b", re.IGNORECASE)
_ASSISTANT_PURPOSE: Final = re.compile(
    r"\b(?:qu[eé]|qe)\s+(?:es|hace)\b|\b(?:prop[oó]sito|objetivo)\b"
    r"|\b(?:ayudar(?:me)?|echarme\s+una\s+mano|gestionar)\b",
    re.IGNORECASE,
)

# Product vocabulary rather than account data. Narrow enough not to capture "¿en qué categoría has
# clasificado el cine?", which is about the previous answer and belongs to the explainer below.
_CATEGORY_CATALOGUE: Final = re.compile(
    r"\b(lista|listado)\s+de\s+categorias\b"
    r"|\bdime\s+las\s+categorias(\s+disponibles)?\b"
    r"|\bque\s+categorias\s+(entiendes|manejas|usas|hay|tienes|soportas)\b"
    r"|\bque\s+opciones\s+de\s+categoria\s+(hay|tienes)\b"
    # "¿Qué categorías puedo consultar?" — asked in the demo, and it fell through to the planner,
    # which answered a question about *which categories exist* with "¿de qué categoría se trata?".
    # A loop, and an insulting one: the list is repository data and was one branch away.
    #
    # The verbs a person reaches for are about *them* doing something with the list rather than
    # the list existing, which is the shape the four patterns above all miss.
    r"|\bcategorias\s+(puedo|se\s+pueden|podria)\s+(consultar|usar|ver|filtrar|pedir)\b"
    r"|\bque\s+categorias\s+(puedo|se\s+pueden|podria)\b"
    r"|\bcategorias\s+(disponibles|existen|hay)\b"
    r"|\bcategorias(?:\s+de\s+gast[op])?\b.{0,110}"
    r"\b(hay|disponibles|seleccionar|utilizar|usar)\b"
    r"|\bcategorias\s+de\s+gast[op]\b.{0,90}\bapp\b.{0,90}\bfiltrar\b"
    r"|\b(?:cuales|quales)\s+son\s+las\s+categorias\b"
)

# The user says the last answer was wrong. Two shapes: naming the misunderstanding, or restating
# what they meant.
_CORRECTING: Final = re.compile(
    r"\b(no\s+me\s+has\s+entendido|no\s+es\s+eso|no\s+era\s+eso|eso\s+no\s+es\s+lo\s+que"
    r"|me\s+refer[íi]a|me\s+refiero|quer[íi]a\s+decir|te\s+has\s+equivocado"
    r"|no\s+te\s+he\s+preguntado)\b",
    re.IGNORECASE,
)

_ANSWER_NOUN: Final = re.compile(r"\brespues(?:ta|a)\b", re.IGNORECASE)
_ANSWER_REJECTION: Final = re.compile(
    r"\bno\b.{0,60}\b(?:correcta|adecuada|esperaba|resuelto|convence)\b"
    r"|\b(?:error|revisarla|intentarlo\s+de\s+nuevo)\b"
    r"|\b(?:perdido|repetirla|explicarla|explicarmelo)\b",
    re.IGNORECASE,
)

# A rejection with no replacement in it. These start a correction workflow rather than asking the
# planner to derive a new query from "no es eso", which contains no financial information at all.
_BARE_CORRECTION: Final = re.compile(
    r"(?:perdona[\s,]+)?(no\s+me\s+has\s+entendido|no\s+(es|era)\s+eso|"
    r"eso\s+no\s+es\s+lo\s+que(\s+te)?\s+pregunte|"
    r"te\s+has\s+equivocado|no\s+te\s+he\s+preguntado\s+eso|"
    r"me\s+(referia|refiero)|queria\s+decir)"
)

# About the previous answer: how it was reached, or what it was filed under. The `por qué` case is
# narrowed to what *the assistant* did — "¿por qué gasté tanto en junio?" is a question about money,
# and it is not one this pipeline can answer either, but the refusal it deserves is a different one.
_ABOUT_THE_ANSWER: Final = re.compile(
    r"\b(qu[ée]\s+consulta|c[óo]mo\s+(lo\s+)?has\s+(calculado|hecho|sacado)"
    r"|(?:de\s+)?d(?:[óo]nde|ond)\s+(?:"
    r"(?:has\s+)?(?:sacado|sacdo|sacao|sacas)"
    r"|(?:sale|viene)(?:\b.{0,35}\b(?:eso|n[úu]mero|cifra|cantidad|dato|respuesta)"
    r"|(?=\s*[?!.]*$)))"
    r"|qu[ée]\s+(?:registros\s+)?has\s+(consultado|mirado)"
    r"|en\s+(?:qu[ée]|qeu)\s+te\s+basas"
    r"|c[óo]mo\s+has\s+llegado\s+a"
    r"|(por\s+qu[ée]|c[óo]mo)\s+(me\s+)?has\s+(mostrado|ense[ñn]ado|clasificado|puesto|contado)"
    r"|en\s+qu[ée]\s+categor[íi]a\s+(?:lo\s+)?(?:has\s+)?"
    r"(?:clasificado|puesto|metido|contado)\b"
    r"|no\s+entiendo\s+(el|este|ese)?\s*desglose"
    r"|qu[ée]\s+has\s+entendido"
    # Asking whether two things are filed together. This is the shape the real question took —
    # "¿es que los restaurantes pertenecen a la misma categoría que el cine?" — and the first
    # version of this router missed it, so it reached the planner and came back asking which
    # period. A question about the catalogue does not have a period.
    r"|misma\s+categor[íi]a|pertenecen?\s+a|cuenta[n]?\s+como|es\s+lo\s+mismo"
    r"|est[áa]n?\s+en\s+la\s+misma)\b",
    re.IGNORECASE,
)

_FRUSTRATION: Final = re.compile(
    r"^(?:pero\s+)?(?:que\s+te\s+pasa|estas\s+bien|por\s+que\s+no\s+me\s+entiendes|"
    r"no\s+me\s+entiendes|esto\s+no\s+funciona|te\s+has\s+quedado\s+pillad[oa])$"
)


# Moving money between the holder's own accounts.
#
# **Both halves are required, and that is the whole safety of it.** A verb of moving *and* one of
# this holder's accounts by name. "Mueve el dinero" alone is not an instruction anybody could act
# on, and "en la hucha" alone is a question about a balance — reading either as a transfer would
# offer to move money at somebody who asked something else.
#
# `mandar` and `enviar` are deliberately absent: they are what a person says about paying somebody,
# and `payment_router` sees this sentence first anyway. What is here is the vocabulary of moving
# something from one place to another that is still yours.
_MOVING: Final = re.compile(
    r"\b(mueve|mover|muevo|mueva|pasa|pasar|paso|traspasa|traspasar|traspaso|trasvasar"
    r"|mete|meter|meto|ingresa|ingresar|guarda|guardar|ahorra|ahorrar"
    # Moving money to *oneself*. `enviar` and `mandar` are what a person says about paying somebody
    # else and are deliberately absent — but the reflexive is the opposite: "enviarme dinero a la
    # cuenta corriente" has exactly one possible destination and it is the holder's own account.
    # Without these the sentence fell through and was answered with that account's balance.
    r"|enviarme|mandarme|pasarme|traspasarme|transferirme|ingresarme"
    # The imperative with the pronoun stuck on, which is how it is actually typed: "pásame
    # 50 al fondo". Folded, so the accent is already gone by the time this is matched.
    r"|pasame|mandame|enviame|muevelo|pasalo|metelo|traspasame|transfiereme"
    # A transfer between one's own accounts, which the rail is named after. Safe here and not in
    # `payment_router` for the reason the whole function is safe: it also needs one of *this
    # holder's* accounts by name, and a payment to a person names a person. "Tranferir" is in the
    # list because it is what somebody typed, twice, and a typo is not a change of intent.
    r"|transferencia|transferir|transfiere|transfiero|tranferir|tranferirme)\b",
    re.IGNORECASE,
)


# What those same verbs mean when the sentence is asking rather than instructing. "¿Cuánto paso a
# la hucha cada mes?" is a question about a habit and it carries a moving verb and an account, so
# without this it read as an order to move money — and the answer to a question would have been a
# preview asking somebody to confirm a payment they never asked for. That is the worst shape a
# misreading can take here, which is why the exclusion is checked first rather than tuned into the
# verb list.
_ASKING: Final = re.compile(
    r"\b(cu[áa]nt[oa]s?|qu[ée]|cu[áa]l|cu[áa]ndo|c[óo]mo|d[óo]nde|por\s+qu[ée])\b",
    re.I,
)


# "Entre mis cuentas" names no account and says which kind of movement this is. With a moving verb
# in front of it the sentence is an instruction — "me gustaría transferir dinero entre cuentas" —
# and it was reaching the capability guard, which refused it with a sentence about *querying* the
# transfers. A refusal about reading, to somebody asking to write.
_BETWEEN_ACCOUNTS: Final = re.compile(r"\bentre\s+(mis|tus|sus|las)?\s*cuentas\b", re.IGNORECASE)

_OWN_ACCOUNT_RELATION: Final = re.compile(
    r"\bde\s+una\s+(?:de\s+)?mis\s+cuentas\s+a\s+otra\b"
    # Observed clipped ending: "mover dinero de una cuenta a otr". Safe as an internal transfer
    # signal only together with `_MOVING`; this rail can never leave the holder's own accounts.
    r"|\bde\s+una\s+cuenta\s+a\s+otr(?:a)?\b"
    r"|\bde\s+una\s+cuenta\s+mia\s+a\s+otra\s+cuenta\b"
    r"|\bde\s+una\s+cuenta\s+a\s+otra\s+cuenta\b.{0,35}"
    r"\b(?:es\s+mia|tambien\s+es\s+mia|tengo\s+yo)\b"
    r"|\bde\s+una\s+cuenta\s+a\s+otra\b.{0,30}\bambas\s+mias\b",
    re.IGNORECASE,
)

_CORRECTION_REPLACEMENT: Final = re.compile(
    r"\b(?:era|sino|me\s+refiero|me\s+referia|queria\s+decir)\b", re.IGNORECASE
)

_ACTION_INTENT: Final = re.compile(
    r"\b(?:quiero(?!\s+saber)|necesito(?!\s+saber)|me\s+gustaria|tengo\s+que"
    r"|estoy\s+intentando|voy\s+a\s+intentar)\b",
    re.IGNORECASE,
)


def _moving_money_inside(sentence: str) -> bool:
    """A verb of moving, not a question, and either a destination or a sum to move.

    The verb alone is never enough. "Mueve el dinero" is not an instruction anybody could act on,
    "en la hucha" alone is a question about a balance, and an interrogative turns the same words
    back into a question — reading any of them as a transfer would offer to move money at somebody
    who asked something else.

    **An amount says as much as an account.** "Mueve 500 € a algún sitio" points at no account this
    holder has, so the destination half found nothing and the sentence fell all the way through to
    the financial planner, which was asked to plan a query for an order to move money. The
    destination is what the transfer path *asks for* when it is missing — it lists the accounts and
    keeps the amount — so a stated sum is exactly the signal that there is an order here at all.

    A sum and no verb of moving stays what it was: an amount arriving with nothing open is
    deliberately nobody's, and two episodes exist to keep it that way.
    """
    from finguard_orchestrator.domain.services import account_words

    folded = spanish.folded(sentence)
    if _ASKING.search(folded) and _ACTION_INTENT.search(folded) is None:
        return False
    if not _MOVING.search(folded):
        return False
    return (
        bool(account_words.named_in(sentence))
        or account_words.names_a_custom_destination(sentence)
        or bool(_BETWEEN_ACCOUNTS.search(folded))
        or bool(_OWN_ACCOUNT_RELATION.search(folded))
        or payment_words.amount_in(sentence) is not None
    )


def route(sentence: str, *, has_previous: bool, at_rest: bool = True) -> Intent:
    """Which conversation this sentence belongs to.

    `has_previous` is whether there is an answer behind it — a plan the client echoed back. It
    gates the two intents that are *about* that answer, so the same words mean different things on
    the first turn and on the second, which is what "conversation" means.

    `at_rest` is whether anything is waiting for a reply, and it gates one word class: «vale» after
    an answer is manners, «vale» while a slot is open might be one. It defaults to True because a
    caller with no conversation in its hands has nothing open by definition.
    """
    folded = spanish.folded(sentence)
    if courtesy.is_courtesy(sentence, at_rest=at_rest):
        # First, because its vocabulary is anchored end to end and can therefore not steal a turn
        # from anything below: a sentence that is entirely «gracias» has nothing else in it. Last
        # would work equally well and would read as an afterthought — this is a whole turn, and 98
        # of the corpus's 879 are it.
        return Intent.COURTESY
    if _ABOUT_CARDS.search(folded) or _ABOUT_CARDS_BY_EACH.search(folded):
        # Before the balance, which used to exclude "qué tarjetas" without giving it anywhere to
        # go, and before the planner, which has no card dimension to plan with.
        return Intent.LIST_CARDS
    if _ABOUT_CONTACTS.search(folded) or _ABOUT_AGENDA.search(folded):
        # Before the payment check, and the comment on the pattern is the argument for why. A
        # sentence naming a contact list is asking who exists, even when it also says "bizum".
        return Intent.LIST_CONTACTS
    if payment_router.is_payment(sentence):
        # Before everything else. A sentence that both complains and asks for money is a payment:
        # the preview and the confirmation are still in front of it, and the other reading would
        # drop a real payment into a path that cannot make one.
        return Intent.PAYMENT
    if _ABOUT_ITSELF.search(folded) or (
        _ASSISTANT_NOUN.search(folded) and _ASSISTANT_PURPOSE.search(folded)
    ):
        return Intent.IDENTITY_OR_HELP
    if _CATEGORY_CATALOGUE.search(folded):
        return Intent.LIST_CATEGORIES
    if has_previous and (
        _CORRECTING.search(folded)
        or (_ANSWER_NOUN.search(folded) and _ANSWER_REJECTION.search(folded))
        or _FRUSTRATION.fullmatch(folded.strip(" ¿?¡!.,"))
    ):
        return Intent.CORRECTION
    if has_previous and _ABOUT_THE_ANSWER.search(folded):
        return Intent.EXPLAIN_PREVIOUS
    if _moving_money_inside(sentence):
        # After PAYMENT and before the balance, and both positions are the point. A sentence that
        # `payment_router` claims is a payment to a person, and this must never take one from it;
        # "mueve 200 € a la hucha" carries "cuánto" in neither direction and would otherwise fall
        # all the way through to the planner, which built a query about the last 200 days.
        return Intent.OWN_TRANSFER
    if (
        _ABOUT_ACCOUNTS.search(folded)
        or _ABOUT_THE_BALANCE.search(folded)
        and not _NOT_THE_BALANCE.search(folded)
    ):
        # Last of the deterministic ones, and not gated on `has_previous`: "¿cuánto tengo?" is a
        # whole question on the first turn. The exclusion is what keeps "¿cuántos movimientos
        # tengo este mes?" on the path that can answer it.
        return Intent.ACCOUNT_BALANCE
    return Intent.FINANCIAL_QUERY


def is_frustration(sentence: str) -> bool:
    """Whether this turn reports a broken conversation rather than correcting a plan field."""
    folded = re.sub(r"^[¿?¡!.,\s]+|[¿?¡!.,\s]+$", "", spanish.folded(sentence))
    return bool(_FRUSTRATION.fullmatch(folded))


def asks_for_something_else(sentence: str) -> bool:
    """Whether the sentence names a subject of its own instead of narrowing the last answer.

    Exposed for the balance ellipsis in the handler, which needs exactly the judgement this module
    already makes when it keeps «¿cuántos movimientos tengo?» off the balance path. One vocabulary
    for one question: a second list of nouns over there would drift from this one, which is
    precisely how the model's prompt drifted from these rules.
    """
    # Public callers should not have to know that every pattern in this module is evaluated over
    # folded Spanish. Keeping the normalisation here also makes clause analysis behave exactly
    # like ``route`` for accents, casing and harmless Unicode variants.
    return _NOT_THE_BALANCE.search(spanish.folded(sentence)) is not None


def is_bare_correction(sentence: str) -> bool:
    """Whether the sentence rejects the previous answer without supplying a replacement."""
    cleaned = spanish.folded(sentence).strip(" ¿?¡!.,")
    if _BARE_CORRECTION.fullmatch(cleaned) is not None:
        return True
    return bool(
        _ANSWER_NOUN.search(cleaned)
        and _ANSWER_REJECTION.search(cleaned)
        and _CORRECTION_REPLACEMENT.search(cleaned) is None
    )
