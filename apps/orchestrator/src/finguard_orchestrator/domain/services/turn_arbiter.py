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
    ANSWER_PENDING a question is open and the turn fills a slot it was waiting on — «este año» to
                   «¿De qué periodo?», «en todas mis cuentas» to the same question, «este año» to
                   «¿En qué cuenta…?». Before every reader of the query in view, because each of
                   them used to claim the reply first: after an answered question, «este año» to a
                   new question's «¿De qué periodo?» completed the *previous* one.
    CORRECTION     the turn corrects the last answer («no era eso, quería decir julio»).
    NEW_QUESTION   everything else, which the pipeline and the planner read as they always did.

**Deterministic, and the interpreter's reading is evidence rather than a verdict.** RESET is a
closed lexicon and NEW_OPERATION the same routers the edge already trusts; `analysis` is consulted
only where its own vocabulary is the authority — who-can-I-pay is LIST_CONTACTS however many
payment words it carries, a turn it reads as an aside or an order is not a reply, and a correction
is the relation the analyzer already names.

`Arbitration` carries a `fills` set for a reply to the open question: every slot the reply names,
so the handler absorbs all of them instead of only the one it asked for — «en todas mis cuentas»
to «¿De qué periodo?» keeps the account and asks the period again, and «este año» to «¿En qué
cuenta…?» keeps the window and asks the account again (spec §3.1, §3.2). The slots are read with
the vocabularies the handler already completes them with, and only what needs no directory: a
kind of account and «todas mis cuentas» are words, and an account's own name is matched only
against the names the account question offered, which the conversation carries.
"""

import re
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Final

from finguard_orchestrator.domain.models.conversation import (
    AccountScopeClarification,
    BookingConfirmation,
    Conversation,
    FinancialClarification,
    PaymentConfirmation,
    Slot,
)
from finguard_orchestrator.domain.models.turn_analysis import TurnAnalysis, TurnRelation
from finguard_orchestrator.domain.services import (
    account_words,
    category_words,
    intent_router,
    payment_router,
    spanish,
    window_words,
)
from finguard_orchestrator.domain.services.capability_guard import MOVEMENT_NOUNS
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
    # The slots this turn fills in the question left open. Empty for every kind but a reply to it,
    # ANSWER_PENDING, where it is never empty: filling something is what makes a turn one.
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

    A question is never one (ruling R16): «¿nada?» asks whether there was nothing, «¿olvídalo?»
    whether to leave it, and wiping the conversation answers neither. A reset is said, not asked.

    Needs no conversation and no reading, which is what lets the handler answer it for a caller
    that brought no arbitration: RESET is first in the order, so the sentence alone decides it.
    """
    if "?" in sentence or "¿" in sentence:
        return False
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


# What makes a turn stand on its own, together with a window (see `states_a_question_of_its_own`).
#
# The noun alternation is shared with the superlative recogniser rather than spelled again. It used
# to hold only conjugated verbs and plural nouns, so "¿cuál fue mi mayor gasto de junio?" matched
# nothing here, stayed an ellipsis, and became a *window patch* on the previous aggregate: the month
# was applied and the superlative was dropped. "mis movimientos más altos de junio" worked, because
# `movimientos` happened to be in the list. One word was the whole difference between a ranking and
# somebody else's total.
_SELF_CONTAINED_FINANCIAL_QUESTION: Final = re.compile(
    r"\b(?:cuanto|cuantos|cual|cuales|que)\b.{0,70}"
    r"\b(?:gaste|gastado|pague|pagado|compre|comprado|ingrese|ingresado|cobre|cobrado"
    rf"|movimientos|operaciones|categoria|{MOVEMENT_NOUNS})\b"
    r"|\b(?:muestrame|ensename|quiero\s+ver)\b.{0,55}"
    r"\b(?:movimientos|operaciones|gastos|ingresos)\b"
)


def states_a_question_of_its_own(sentence: str, *, today: date) -> bool:
    """Whether this turn states a complete new question rather than an ellipsis.

    A concrete window plus a financial question is enough to stand on its own. Inheriting an old
    category there is how "cuánto gasté en la cuenta principal este mes" remained filtered by
    restaurants. Short turns without a window ("y cuánto gasté?") remain follow-ups.

    Here, beside the arbitration, because it is half of what makes a turn a reply: «¿cuánto gasté el
    mes pasado?» names a window, and asked while «¿De qué periodo?» waits it is a question of its
    own, not the period that one was missing. The handler's readers ask the same thing about
    follow-ups through `financial_turn._starts_new_financial_scope`, which reads this.
    """
    folded = spanish.folded(sentence)
    if re.search(r"\b(?:esa|ese|esas|esos)\b|\blo\s+mismo\b", folded):
        return False
    return (
        window_words.window_in(sentence, today=today) is not None
        and _SELF_CONTAINED_FINANCIAL_QUESTION.search(folded) is not None
    )


# What the analysis may read a turn as that makes it a turn of its own rather than a reply. The
# asides answer something else and leave the question open behind them («¿quién eres?» to
# «¿En qué cuenta…?» — the corpus walks away from that question and back on purpose); an order to
# pay or to move money between accounts names an account as its destination, and «pasa 100 a la
# hucha» is not the hucha chosen for a spending question.
#
# A balance is not here, and that is the defect this rule exists for: the router's own balance
# reading was taken above as NEW_OPERATION, so a balance the *analysis* reads beside a router that
# does not is a chip like «Todas mis cuentas» read as a question about money held — which is how it
# was answered with the balance.
_A_TURN_OF_ITS_OWN: Final = frozenset(
    {
        Intent.COURTESY,
        Intent.IDENTITY_OR_HELP,
        Intent.LIST_CATEGORIES,
        Intent.EXPLAIN_PREVIOUS,
        Intent.LIST_CONTACTS,
        Intent.LIST_CARDS,
        Intent.PAYMENT,
        Intent.OWN_TRANSFER,
    }
)

_WINDOW_SLOTS: Final = frozenset({Slot.WINDOW, Slot.YEAR})


def _what_the_question_holds(conversation: Conversation) -> frozenset[Slot]:
    """The slots the open question can take a value for, whichever of them it asked.

    The window question's draft holds its account as well (spec §3.2: the draft has carried
    `across_accounts` and `only_account_id(s)` all along) and the subject it is about. The account
    question holds a plan that is already complete, whose window a reply may still correct before
    it runs. A question asked with nothing half-built behind it (`last_asked` alone) holds only what
    it asked.

    The rest hold nothing here, on purpose. A correction question («¿Qué debería cambiar…?») is the
    correction readers' to answer, and a payment or a transfer has its own gates, which answer their
    replies before anything is arbitrated.
    """
    active = conversation.active
    if isinstance(active, FinancialClarification):
        if active.awaiting in _WINDOW_SLOTS:
            return frozenset({active.awaiting, Slot.ACCOUNT, Slot.CATEGORY})
        if active.awaiting is Slot.CATEGORY:
            return frozenset({Slot.CATEGORY})
        return frozenset()
    if isinstance(active, AccountScopeClarification):
        return frozenset({Slot.ACCOUNT, Slot.WINDOW})
    if conversation.awaiting is None and conversation.last_asked in {*_WINDOW_SLOTS, Slot.ACCOUNT}:
        return frozenset({conversation.last_asked})
    return frozenset()


def _names_an_account(sentence: str, conversation: Conversation) -> bool:
    """Whether the sentence chooses among the holder's accounts, in words that need no directory.

    «Todas mis cuentas» and a kind («la hucha», «la cuenta corriente») mean the same to every
    holder. Two more readings belong to the account question alone, because only it gives them a
    meaning: a bare «todas» / «en todas», which is every account as the answer to «¿En qué
    cuenta…?» and nothing at all as the answer to «¿De qué periodo?»; and an account's own name,
    matched against the names that question offered — the conversation carries them, so this needs
    no directory either.
    """
    if account_words.asks_for_all_accounts(sentence) or account_words.named_in(sentence):
        return True
    asked = conversation.awaiting or conversation.last_asked
    if asked is Slot.ACCOUNT and account_words.all_accounts_as_a_reply(sentence):
        return True
    active = conversation.active
    return isinstance(active, AccountScopeClarification) and bool(
        account_words.custom_chosen_among(
            sentence, tuple(candidate.display_name for candidate in active.candidates)
        )
    )


def _what_it_fills(
    sentence: str, conversation: Conversation, analysis: TurnAnalysis, *, today: date
) -> frozenset[Slot]:
    """The slots of the open question this sentence fills, or none when it is not a reply to it.

    Read with the vocabularies the handler completes the question with — `window_words.window_in`
    against the slot asked, `category_words.precisely_named_in`, `account_words` — so the
    arbitration cannot call a turn a reply the handler then has nothing to absorb from.

    Not a reply, whatever it names: a turn the analysis reads as an aside or an order
    (`_A_TURN_OF_ITS_OWN`), and a complete question of its own (`states_a_question_of_its_own`) —
    the fourth condition `_finish_pending` learned the hard way, when «¿cuánto gasté el mes
    pasado?» completed an internal-transfers draft that happened to be open.

    **A category alone answers only the question that asked for one.** «Este año pero en gasolina»
    fills the window and names a new subject, and both are the reply's; «en gasolina» to «¿De qué
    periodo?» fills nothing the question was waiting on, and is a change of subject the pipeline
    reads as it always did.
    """
    holds = _what_the_question_holds(conversation)
    if (
        not holds
        or analysis.intent in _A_TURN_OF_ITS_OWN
        or states_a_question_of_its_own(sentence, today=today)
    ):
        return frozenset()
    asked = conversation.awaiting or conversation.last_asked
    filled: set[Slot] = set()
    window_slot = Slot.YEAR if Slot.YEAR in holds else Slot.WINDOW
    if (
        window_slot in holds
        and window_words.window_in(sentence, asked_for=window_slot, today=today) is not None
    ):
        filled.add(window_slot)
    if Slot.ACCOUNT in holds and _names_an_account(sentence, conversation):
        filled.add(Slot.ACCOUNT)
    if Slot.CATEGORY in holds and category_words.precisely_named_in(sentence):
        filled.add(Slot.CATEGORY)
    if filled == {Slot.CATEGORY} and asked is not Slot.CATEGORY:
        return frozenset()
    return frozenset(filled)


def arbitrate(
    sentence: str, conversation: Conversation, analysis: TurnAnalysis, *, today: date
) -> Arbitration:
    """Which kind of turn `sentence` is, in the order the module docstring argues.

    `today` is what a reply's window is read against: «el mes pasado» fills a window only once
    there is a date for it to be the month before.
    """
    if is_a_reset(sentence):
        return Arbitration(TurnType.RESET)
    if isinstance(conversation.active, PaymentConfirmation | BookingConfirmation):
        return Arbitration(TurnType.CONFIRMATION)
    if _orders_an_operation(sentence, conversation, analysis):
        return Arbitration(TurnType.NEW_OPERATION)
    fills = _what_it_fills(sentence, conversation, analysis, today=today)
    if fills:
        return Arbitration(TurnType.ANSWER_PENDING, fills)
    if analysis.relation is TurnRelation.CORRECTION:
        return Arbitration(TurnType.CORRECTION)
    return Arbitration(TurnType.NEW_QUESTION)
