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


# What those same verbs mean when the sentence is asking rather than instructing. Checked first
# rather than tuned into the verb list, because the worst shape a misreading can take here is a
# preview asking somebody to confirm a payment they never asked for.
_ASKING: Final = re.compile(rf"\b({alternation('asking')})\b", re.I)
