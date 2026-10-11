"""What kind of turn this is, decided once and before any reader may claim it.

About sixty steps in `AskFinancialQuestionHandler._steps` each used to decide on their own whether a
turn was theirs, so the answer depended on which of them happened to look first. Measured on
2026-10-07, that is where the brittleness was, not in any model: «olvidalo» after an answer was read
as the name of a shop, because the bare-shop reader looks before anything that knows the word; and
«olvídalo» answering «¿De qué periodo?» was read as a period nobody could parse and asked about
again, so the only way out of an open question was to answer it.

**The order is the design**, and the first match wins:

    RESET          the turn is only words that drop what was open — «olvídalo», «déjalo», «no
                   importa», «nada», «no era eso» — with nothing after them but courtesy and the
                   fillers people wrap them in («bah», «que no», «da igual»). First, because it is
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
    FOLLOW_UP      nothing is open, an answer is in view, and the turn is a patch on it that the
                   closed vocabularies ground (`continuation_patch.read` against that answer): «¿y
                   ella a mí?» after «¿cuánto le mandé a Marta?», «mejor por categoría» after a
                   total. A breakdown is never a correction, whatever the reading says: the figure
                   stood, and the turn asks to see it divided.
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
    Slot,
)
from finguard_orchestrator.domain.services import (
    continuation_patch,
    spanish,
    window_words,
)
from finguard_orchestrator.domain.services.capability_guard import MOVEMENT_NOUNS


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
    # The patch a FOLLOW_UP makes on the answer in view, as it was read here. The handler applies
    # this one rather than reading the sentence again, because the reading may rest on what only
    # the edge knew — a payment since the answer (`just_paid`) — and a second reading without it
    # would resolve «ella» to a person this one refused. None for every other kind.
    patch: continuation_patch.GroundedContinuation | None = None


# The reset lexicon, folded and split into words. Closed on purpose, like `confirmation._NO` and
# `merchant_words._NEVER_A_MERCHANT_WORDS`, which already hold several of these: a rule that guessed
# a reset from the shape of a sentence could wipe somebody's question for a word nobody meant that
# way, and the cost of a missed reset is one more turn.
#
# «No era eso» is here and «no es eso» is not. Said alone, the past tense drops the last answer;
# the present is the correction the corpus has always opened the «¿Qué debería cambiar…?» question
# for, and stays one. With anything after it — «no era eso, quería decir julio» — it is not this
# list's at all: the turn is no longer *only* reset words.
#
# «Olvídate», «déjalo estar», «cancela» and «cancélalo» are here since the final review (M-3): each
# was read as the name of a shop after an answer, and as a period nobody could parse while one was
# asked for. «Déjalo estar» before «déjalo», because the first match wins and «estar» alone is no
# word of this list. «Da igual» is not here, and must not be: to «¿En qué cuenta…?» it answers
# «any of them», which is a reply and drops nothing.
_RESET: Final = (
    ("no", "era", "eso"),
    ("no", "importa"),
    ("olvidalo",),
    ("olvidate",),
    ("dejalo", "estar"),
    ("dejalo",),
    ("cancelalo",),
    ("cancela",),
    ("nada",),
)


_WORD: Final = re.compile(r"[a-z0-9]+")


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
    if asks_what_there_is_in_total(sentence):
        # «¿Qué tengo en total … este mes?» (fix round 2): a whole question about what there is,
        # so the planner is handed no answer to continue — never the restaurants in view.
        return True
    if re.search(r"\b(?:esa|ese|esas|esos)\b|\blo\s+mismo\b", folded):
        return False
    return (
        window_words.window_in(sentence, today=today) is not None
        and _SELF_CONTAINED_FINANCIAL_QUESTION.search(folded) is not None
    )


# «¿Qué tengo en total…?», «q tengo en total…»: a question about what there is, whatever follows it
# (validation fix 3, fix round 2). Said alone it is the balance (the router's anchored row); with
# more said — «… en recibos este mes», «… de gastos este mes», «… este mes» — it is still a question
# of its own, and after an answer about restaurants it was read as «este mes» patched onto them.
_WHAT_THERE_IS_IN_TOTAL: Final = re.compile(
    r"^\W*(?:y\s+)?(?:que|q)\s+(?:dinero\s+)?tengo\s+en\s+total\b"
)


def asks_what_there_is_in_total(sentence: str) -> bool:
    """Whether the sentence opens «(qué|q) tengo en total» (fix round 2): a whole question, never a
    patch on the answer in view. Public because the follow-up reader declines it too."""
    return _WHAT_THERE_IS_IN_TOTAL.search(spanish.folded(sentence)) is not None
