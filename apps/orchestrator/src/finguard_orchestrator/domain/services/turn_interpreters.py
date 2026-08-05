"""The interpreters that need no model, and the one that compares two.

`Deterministic` is `intent_router.route` behind the port — the behaviour that exists today, renamed
into a shape a model can also fill. It is not a step towards anything on its own; what it buys is
that the router and a model become interchangeable, which is the only way to measure one against
the other without a rewrite.

`Shadowed` is the migration. It asks both, records whether they agreed, and answers with the
deterministic one — so the model's opinion costs a log line and changes nothing. When the numbers
justify it, `Promoted` is what flips the authority, and it carries the one rule that does not flip
with it.
"""

import logging

from finguard_orchestrator.domain.models.conversation import Conversation
from finguard_orchestrator.domain.ports.turn_interpreter import TurnInterpreterPort
from finguard_orchestrator.domain.services import intent_router
from finguard_orchestrator.domain.services.intent_router import Intent

logger = logging.getLogger(__name__)


class Deterministic:
    """The routers, behind the port. Exactly today's behaviour and nothing else.

    `has_previous` is whether there is an answered plan behind this turn, and it gates the two
    intents that are *about* that answer. The router takes it as a boolean because that is all it
    reads; an interpreter that wants more of the conversation gets the whole thing.
    """

    async def interpret(self, sentence: str, *, conversation: Conversation) -> Intent:
        return intent_router.route(sentence, has_previous=conversation.last_plan is not None)


class Shadowed:
    """Asks both and answers with the first. The second only gets to be counted.

    **Nothing the shadow says reaches the user**, which is the property that makes this safe to run
    against real traffic: a model having a bad day costs a log line rather than a wrong answer.

    What is logged is two enum values and whether they matched. Not the sentence — this service
    reads a person's bank account and the rule is that nothing they typed reaches a log file, so
    the record has to be analysable without it. It is: the corpus is 879 turns whose right answer
    is already written down, and that is where a disagreement gets diagnosed. Production tells you
    *how often*; the corpus tells you *which sentences*.

    A shadow that fails is not an error. It is a second opinion nobody is acting on, so an exception
    from it is swallowed and counted — the alternative is a model outage taking down a conversation
    it was not participating in.
    """

    def __init__(self, authority: TurnInterpreterPort, shadow: TurnInterpreterPort) -> None:
        self._authority = authority
        self._shadow = shadow
        # Counted in memory as well as logged, so a test and a `/admin` route can read the rate
        # without parsing log lines. Per process and reset by a restart, which is what a shadow
        # measurement is worth anyway.
        self.asked = 0
        self.disagreed = 0

    async def interpret(self, sentence: str, *, conversation: Conversation) -> Intent:
        decided = await self._authority.interpret(sentence, conversation=conversation)
        self.asked += 1
        try:
            shadowed = await self._shadow.interpret(sentence, conversation=conversation)
        except Exception:
            # Deliberately broad. Whatever went wrong in the shadow — a dead daemon, a malformed
            # answer, a timeout — is a fact about the experiment and not about this turn.
            logger.warning("turn.shadow_failed decision=%s", decided.value, exc_info=True)
            return decided

        if shadowed is not decided:
            self.disagreed += 1
        logger.info(
            "turn.interpreted decision_legacy=%s decision_llm=%s disagree=%s",
            decided.value,
            shadowed.value,
            str(shadowed is not decided).lower(),
        )
        return decided


class Promoted:
    """The model decides, except about money.

    This is what a flipped migration looks like, and it exists now rather than later so the rule it
    carries is written while somebody is thinking about it rather than while somebody is shipping.

    **A payment needs both.** `Intent.PAYMENT` is the turn that starts moving money, and the whole
    argument for reading it deterministically is that the decision must not depend on the
    disposition of whichever model a deployment installed — two that were measured sat sixty points
    apart on exactly that. So a model may widen every other reading and may not, on its own, start a
    payment: it has to agree with the router, which is the same rule the two-step confirmation
    already applies one layer down.

    The asymmetry is on purpose and it only runs one way. A model saying "this is *not* a payment"
    about a sentence the router claims *is* one also loses — the router keeps that turn, because a
    real payment dropped into the read path is a payment that cannot be made.
    """

    def __init__(self, model: TurnInterpreterPort, router: TurnInterpreterPort) -> None:
        self._model = model
        self._router = router

    async def interpret(self, sentence: str, *, conversation: Conversation) -> Intent:
        deterministic = await self._router.interpret(sentence, conversation=conversation)
        if deterministic is Intent.PAYMENT:
            return deterministic
        try:
            read = await self._model.interpret(sentence, conversation=conversation)
        except Exception:
            # The router is the fallback and always will be: an interpreter that cannot answer
            # leaves a conversation working rather than failing.
            logger.warning("turn.model_failed falling_back=%s", deterministic.value, exc_info=True)
            return deterministic
        if read is Intent.PAYMENT:
            logger.info("turn.payment_refused_to_model router=%s", deterministic.value)
            return deterministic
        return read
