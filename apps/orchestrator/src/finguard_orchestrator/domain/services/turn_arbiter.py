"""What kind of turn this is, decided once and before any reader may claim it.

About sixty steps in `AskFinancialQuestionHandler._steps` each used to decide on their own whether a
turn was theirs, so the answer depended on which of them happened to look first. Measured on
2026-10-07, that is where the brittleness was, not in any model: «olvidalo» after an answer was read
as the name of a shop, because the bare-shop reader looks before anything that knows the word; and
«olvídalo» answering «¿De qué periodo?» was read as a period nobody could parse and asked about
again, so the only way out of an open question was to answer it.

**The order is the design**, and the first match wins:

    RESET          the turn is only words that drop what was open — «olvídalo», «déjalo», «no
                   importa», «nada», «no era eso» — with nothing after them. First, because it is
                   the one turn about the open question itself: read as its answer, it never ends.
    CONFIRMATION   a payment or booking preview is waiting for a yes. Its gates in the controller
                   answer it before this is asked, whatever the words; this is the arbitration
                   agreeing, so no later rule can read «sí» as something else.
    NEW_OPERATION  an order to move money, or a question about how much there is. Both outrank a
                   question left open: «¿De qué periodo?» → «ahora quiero mandarle 20 euros a
                   Marta» is the payment, and the period question is dropped, not kept.
    CORRECTION     the turn corrects the last answer («no era eso, quería decir julio»).
    NEW_QUESTION   everything else, which the pipeline and the planner read as they always did.

**Deterministic, and the interpreter's reading is evidence rather than a verdict.** RESET is a
closed lexicon and NEW_OPERATION the same routers the edge already trusts; `analysis` is consulted
only where its own vocabulary is the authority — who-can-I-pay is LIST_CONTACTS however many
payment words it carries, and a correction is the relation the analyzer already names.

`Arbitration` carries a `fills` set for the kinds that answer something: a reply to the open
question names the slots it fills, so the handler can absorb every one of them instead of only the
one it asked for. Nothing decided here fills a slot yet.
"""

import re
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Final

from finguard_orchestrator.domain.models.conversation import (
    BookingConfirmation,
    Conversation,
    PaymentConfirmation,
    Slot,
)
from finguard_orchestrator.domain.models.turn_analysis import TurnAnalysis, TurnRelation
from finguard_orchestrator.domain.services import intent_router, payment_router, spanish
from finguard_orchestrator.domain.services.intent_router import Intent


class TurnType(StrEnum):
    RESET = "RESET"
    CONFIRMATION = "CONFIRMATION"
    NEW_OPERATION = "NEW_OPERATION"
    ANSWER_PENDING = "ANSWER_PENDING"
    CORRECTION = "CORRECTION"
    FOLLOW_UP = "FOLLOW_UP"
    NEW_QUESTION = "NEW_QUESTION"


@dataclass(frozen=True)
class Arbitration:
    type: TurnType
    # The slots this turn fills in the question left open. Empty for every kind but a reply to it.
    fills: frozenset[Slot] = frozenset()


# The reset lexicon, folded and split into words. Closed on purpose, like `confirmation._NO` and
# `merchant_words._NEVER_A_MERCHANT_WORDS`, which already hold several of these: a rule that guessed
# a reset from the shape of a sentence could wipe somebody's question for a word nobody meant that
# way, and the cost of a missed reset is one more turn.
#
# «No era eso» is here and «no es eso» is not. Said alone, the past tense drops the last answer;
# the present is the correction the corpus has always opened the «¿Qué debería cambiar…?» question
# for, and stays one. With anything after it — «no era eso, quería decir julio» — it is not this
# list's at all: the turn is no longer *only* reset words.
_RESET: Final = (
    ("no", "era", "eso"),
    ("no", "importa"),
    ("olvidalo",),
    ("dejalo",),
    ("nada",),
)

# What may travel with a reset and asks for nothing: courtesy, and a leading «no» — «no, olvídalo»
# is how the reset is usually said. None of these is a reset by itself, which is why a turn has to
# say at least one word of `_RESET`: a bare «no» while a question is open is somebody answering it,
# and a bare «gracias» is manners.
_ALONGSIDE: Final = (
    ("por", "favor"),
    ("gracias",),
    ("vale",),
    ("no",),
)

_WORD: Final = re.compile(r"[a-z0-9]+")


def is_a_reset(sentence: str) -> bool:
    """Whether the whole turn is reset words, with at most courtesy and a «no» around them.

    Folded and read word by word, so accents, capitals and punctuation change nothing: «¡Olvídalo!»
    and «olvidalo» are the same turn. Any other word — a category, a window, «más» — makes it a
    request that happens to contain one: «no, déjalo en restaurantes», «nada más que gasolina».

    Needs no conversation and no reading, which is what lets the handler answer it for a caller
    that brought no arbitration: RESET is first in the order, so the sentence alone decides it.
    """
    words = _WORD.findall(spanish.folded(sentence))
    said_one, at = False, 0
    while at < len(words):
        reset = next((p for p in _RESET if tuple(words[at : at + len(p)]) == p), None)
        if reset is not None:
            said_one, at = True, at + len(reset)
            continue
        alongside = next((p for p in _ALONGSIDE if tuple(words[at : at + len(p)]) == p), None)
        if alongside is None:
            return False
        at += len(alongside)
    return said_one


def _orders_an_operation(sentence: str, conversation: Conversation, analysis: TurnAnalysis) -> bool:
    """An order to move money, or a question about how much there is.

    The payment half is `payment_router.is_payment`, the narrower authority the edge already uses on
    a FINANCIAL_QUERY reading; LIST_CONTACTS overrides it because «¿a quién puedo hacerle un
    bizum?» orders nothing however many payment words it carries.

    The balance half is the router's balance rows, read through `route` rather than copied, so the
    cascade's own precedence holds: «¿cuántos movimientos tengo?» is not a balance, and a sentence
    the router gives to cards or contacts first is not one either.
    """
    if analysis.intent is not Intent.LIST_CONTACTS and payment_router.is_payment(sentence):
        return True
    routed = intent_router.route(
        sentence,
        has_previous=conversation.last_plan is not None,
        at_rest=conversation.at_rest,
    )
    return routed is Intent.ACCOUNT_BALANCE


def arbitrate(
    sentence: str, conversation: Conversation, analysis: TurnAnalysis, *, today: date
) -> Arbitration:
    """Which kind of turn `sentence` is, in the order the module docstring argues.

    `today` is what a reply's window is read against; none of the kinds decided here needs a date.
    """
    if is_a_reset(sentence):
        return Arbitration(TurnType.RESET)
    if isinstance(conversation.active, PaymentConfirmation | BookingConfirmation):
        return Arbitration(TurnType.CONFIRMATION)
    if _orders_an_operation(sentence, conversation, analysis):
        return Arbitration(TurnType.NEW_OPERATION)
    if analysis.relation is TurnRelation.CORRECTION:
        return Arbitration(TurnType.CORRECTION)
    return Arbitration(TurnType.NEW_QUESTION)
