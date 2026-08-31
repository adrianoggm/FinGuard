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

**The Spanish is in `intent_lexicon`, and only the Spanish.** What each concept means, the frame it
is read inside, the flags, and the order above all stayed here, because all four are routing
decisions and the reviewer for them is whoever owns this cascade. The 168 phrasings are a coverage
decision and the reviewer for those is a Spanish speaker, which is the whole reason they moved.
Every one of the 23 patterns below rebuilds byte-for-byte from that table, asserted by
`tests/test_intent_lexicon.py`, so the move is checkable rather than trusted.
"""

import re
from enum import StrEnum
from typing import Final

from finguard_orchestrator.domain.services import courtesy, payment_router, payment_words, spanish

# Imported bare rather than as `intent_lexicon.alternation`, which is how `explicit_facts` reads
# its own table. Twenty-three call sites here against nine there, and every one of them sits inside
# an f-string frame that has to stay on one readable line.
from finguard_orchestrator.domain.services.intent_lexicon import alternation


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


# **"Cuánto" is spelled by a rule rather than by a list, and the rule stays here.** The verb beside
# it already carried three misspellings by hand — `tiengo`, `tieno`, `tngo` — and the question word
# carried none, so "cunato tengo" was a balance question answered by the financial planner. Both
# halves have to survive a slip for the sentence to, and enumerating the second list in the lexicon
# would repeat what the first one teaches and go stale the moment it changed. So the four rows that
# need it carry a `<how_much>` slot and this fills it.
#
# Transpositions and not one whole edit: one substitution from "cuanto" is "cuando", and reading
# "¿cuándo cobro la nómina?" as a balance would be a real figure about a different question.
_HOW_MUCH: Final = "|".join(spanish.transposed("cuanto"))

# How much money there is, which is a state and not a sum over a window. Every row carries its own
# boundaries, so this concept has no frame beyond the flag.
_ABOUT_THE_BALANCE: Final = re.compile(
    alternation("about_the_balance", how_much=_HOW_MUCH), re.IGNORECASE
)

# Asking which accounts exist is served by the same deterministic summary as a balance.
_ABOUT_ACCOUNTS: Final = re.compile(alternation("about_accounts"), re.IGNORECASE)

# What "tengo" means when it is not about the balance. Consulted before the balance, so a question
# that counts movements or lists things keeps going to the path that can answer it.
_NOT_THE_BALANCE: Final = re.compile(alternation("not_the_balance"), re.IGNORECASE)


# The cards in the wallet, and what has been charged to each. The same shape as LIST_CONTACTS and
# fixed the same way: a question with an answer in this service does not belong to a model that has
# no way to express it.
_ABOUT_CARDS: Final = re.compile(alternation("about_cards"), re.IGNORECASE)

_ABOUT_CARDS_BY_EACH: Final = re.compile(alternation("about_cards_by_each"), re.IGNORECASE)


# Who can be paid, which is a question and not an order.
#
# **Checked before PAYMENT**, reversing the rule that payments come first. That rule exists because
# being wrong about a payment costs money, and listing contacts cannot move any — so here the risk
# runs the other way, and the expensive mistake is refusing to answer a question. What keeps it safe
# is the narrowness of the vocabulary: it needs the word "contactos", or an explicit "a quién
# puedo". No order to pay contains either, which is asserted rather than assumed.
_ABOUT_CONTACTS: Final = re.compile(alternation("about_contacts"), re.IGNORECASE)

_ABOUT_AGENDA: Final = re.compile(alternation("about_agenda"), re.IGNORECASE)


# About the assistant rather than about the account. Wrapped in a group with a leading `\b`, which
# is load-bearing for the one row that anchors itself: see `about_itself` in the lexicon.
_ABOUT_ITSELF: Final = re.compile(rf"\b({alternation('about_itself')})", re.IGNORECASE)

# The noun and the purpose are separate concepts because `route` requires both: "este asistente"
# alone names the subject without asking anything about it.
_ASSISTANT_NOUN: Final = re.compile(alternation("assistant_noun"), re.IGNORECASE)
_ASSISTANT_PURPOSE: Final = re.compile(alternation("assistant_purpose"), re.IGNORECASE)

# Product vocabulary rather than account data. The one concept read case-sensitively, which costs
# nothing because the sentence is folded before it arrives — and is left as it was rather than
# tidied, because a flag change is a change to make on purpose and measure.
_CATEGORY_CATALOGUE: Final = re.compile(alternation("category_catalogue"))

# The user says the last answer was wrong. Two shapes: naming the misunderstanding, or restating
# what they meant.
_CORRECTING: Final = re.compile(rf"\b({alternation('correcting')})\b", re.IGNORECASE)

_ANSWER_NOUN: Final = re.compile(alternation("answer_noun"), re.IGNORECASE)
_ANSWER_REJECTION: Final = re.compile(alternation("answer_rejection"), re.IGNORECASE)

# A rejection with no replacement in it. These start a correction workflow rather than asking the
# planner to derive a new query from "no es eso", which contains no financial information at all.
#
# The optional "perdona" is the frame's and not a row's: it opens a turn rather than being one of
# the ways of rejecting an answer, and `is_bare_correction` reads this under `fullmatch`.
_BARE_CORRECTION: Final = re.compile(rf"(?:perdona[\s,]+)?({alternation('bare_correction')})")

# About the previous answer: how it was reached, or what it was filed under.
_ABOUT_THE_ANSWER: Final = re.compile(rf"\b({alternation('about_the_answer')})\b", re.IGNORECASE)

# Anchored end to end, and read under `fullmatch` besides. The optional "pero" is the frame's: it
# opens the turn rather than being one of the ways of reporting a broken conversation.
_FRUSTRATION: Final = re.compile(rf"^(?:pero\s+)?(?:{alternation('frustration')})$")


# Moving money between the holder's own accounts.
#
# **Both halves are required, and that is the whole safety of it.** A verb of moving *and* one of
# this holder's accounts by name. "Mueve el dinero" alone is not an instruction anybody could act
# on, and "en la hucha" alone is a question about a balance — reading either as a transfer would
# offer to move money at somebody who asked something else. That rule is `_moving_money_inside`'s
# below; this is only the verbs.
_MOVING: Final = re.compile(rf"\b({alternation('moving')})\b", re.IGNORECASE)


# What those same verbs mean when the sentence is asking rather than instructing. Checked first
# rather than tuned into the verb list, because the worst shape a misreading can take here is a
# preview asking somebody to confirm a payment they never asked for.
_ASKING: Final = re.compile(rf"\b({alternation('asking')})\b", re.I)


_BETWEEN_ACCOUNTS: Final = re.compile(alternation("between_accounts"), re.IGNORECASE)

_OWN_ACCOUNT_RELATION: Final = re.compile(alternation("own_account_relation"), re.IGNORECASE)

_CORRECTION_REPLACEMENT: Final = re.compile(
    rf"\b(?:{alternation('correction_replacement')})\b", re.IGNORECASE
)

_ACTION_INTENT: Final = re.compile(rf"\b(?:{alternation('action_intent')})\b", re.IGNORECASE)


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
    asking = _ASKING.search(folded)
    own_relation = _OWN_ACCOUNT_RELATION.search(folded)
    # `que` can be the relative pronoun after the relation that proves both accounts belong to the
    # holder: "de una cuenta a otra cuenta que también es mía". Treating it as an interrogative
    # sent an imperative to the model. A real question puts its interrogative before that relation.
    asking_follows_ownership = bool(
        asking is not None and own_relation is not None and own_relation.start() <= asking.start()
    )
    if (
        asking is not None
        and not asking_follows_ownership
        and _ACTION_INTENT.search(folded) is None
    ):
        return False
    if not _MOVING.search(folded):
        return False
    return (
        bool(account_words.named_in(sentence))
        or account_words.names_a_custom_destination(sentence)
        or bool(_BETWEEN_ACCOUNTS.search(folded))
        or own_relation is not None
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
