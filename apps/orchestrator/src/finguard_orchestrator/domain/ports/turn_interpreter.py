"""Which kind of turn this is — the one decision the whole conversation hangs on.

Today it is `intent_router.route`: ten intents, decided by regular expressions over the sentence.
That has been the right thing while the vocabulary was small and it is where the cost has moved.
Every conversation reported from the demo cost a new pattern — a reflexive verb, a way of saying
"the state of my savings", a participle for money arriving — and the next one will cost another.
With voice in front of it, imperfect transcriptions and more spontaneous phrasing, the exceptions
grow faster than anybody can add them.

**The boundary moves, not the guarantees.** The model gets to understand freely; the domain still
executes strictly. What an interpreter returns is one value from a closed enum, and everything
downstream — the plan, the compiler, the two-step payment, the scopes — is untouched. A model that
misreads a sentence sends the turn to the wrong *answer*; it cannot reach a different account, spend
a scope it was not granted, or produce SQL.

**One exception, and it is not negotiable.** `Intent.PAYMENT` is the turn that starts moving money,
and a model must never be the reason one starts. When an interpreter becomes the authority it may
widen everything else; a payment still needs the deterministic router to agree. That rule lives in
the decorator that promotes an interpreter rather than in the prose here, so it cannot be forgotten
by whoever does the promoting.

The port is a Protocol like every other, so the deterministic router and a model are the same shape
and either can be swapped in — which is what makes the shadow comparison possible at all.
"""

from typing import Protocol, runtime_checkable

from finguard_orchestrator.domain.models.conversation import Conversation
from finguard_orchestrator.domain.services.intent_router import Intent


@runtime_checkable
class TurnInterpreterPort(Protocol):
    """Reads one turn and says what kind it is.

    `conversation` is passed whole and not as a sentence about itself, because the same words are
    two different turns depending on what is open: "¿y en la hucha?" narrows a balance after a
    balance and narrows spending after a spending question, and "20 euros" is an amount only if
    something asked for one. An interpreter that saw only the sentence would be answering a
    question nobody can answer.

    Nothing here raises. An interpreter that cannot decide returns `Intent.FINANCIAL_QUERY`, which
    is the fallback the whole pipeline is built around: the planner reads it, the verifier checks
    it, and a wrong plan is refused. Every other intent is a shortcut past that machinery, so the
    honest answer when unsure is the long way round.
    """

    async def interpret(self, sentence: str, *, conversation: Conversation) -> Intent: ...
