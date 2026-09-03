"""Rule, shadow, hybrid and model-authoritative turn interpreters.

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
import re
from typing import Final

from finguard_orchestrator.domain.models.conversation import Conversation
from finguard_orchestrator.domain.models.planning_context import focus_of
from finguard_orchestrator.domain.models.result_reference import result_reference_of
from finguard_orchestrator.domain.ports.turn_interpreter import TurnInterpreterPort
from finguard_orchestrator.domain.services import intent_router, spanish
from finguard_orchestrator.domain.services.intent_router import Intent
from finguard_orchestrator.observability import Step, Verdict, record

logger = logging.getLogger(__name__)

# Which arrangement read the turn. Three names, fixed here rather than typed at the call sites,
# for the reason `Step` is an enum: Section 3 makes this a span attribute and a fourth spelling of
# "hybrid" is a series nothing sums.
_SHADOW: Final = "shadowed"
_PROMOTED: Final = "promoted"
_HYBRID: Final = "hybrid"


def _decided(router: Intent, answered: Intent, *, by: str) -> Intent:
    """Record which intent this turn was read as, and whether a model moved it off the router's.

    The one place this seam is recorded, and it is here rather than in `Deterministic` on purpose:
    the decision `Step.INTENT` names is *router versus model*, and a deployment with no model has
    no such decision to make. Recording an agreement between the router and itself would put a
    turn in the denominator of an override rate that nothing could ever have overridden.

    `changed` and the verdict come from one comparison, which is what keeps this a decision rather
    than a branch: `answered is not router` is exactly "a model widened this reading", and every
    other outcome above — a payment the model may not claim, a balance it may not propose, a
    reading it failed to produce at all — arrives here as the router's own intent and is recorded
    as the agreement it is.
    """
    record(
        Step.INTENT,
        Verdict.OVERRODE if answered is not router else Verdict.AGREED,
        changed=answered is not router,
        field="intent",
        was=router.value,
        now=answered.value,
        detail=by,
    )
    return answered


class Deterministic:
    """The routers, behind the port. Exactly today's behaviour and nothing else.

    The router reads two booleans off the conversation rather than taking it whole: whether there
    is an answered plan behind this turn, which gates the two intents that are *about* that answer,
    and whether anything is waiting for a reply, which decides whether «vale» is manners. An
    interpreter that wants more than that gets the whole conversation — this one does not.
    """

    async def interpret(self, sentence: str, *, conversation: Conversation) -> Intent:
        return intent_router.route(
            sentence,
            has_previous=conversation.last_plan is not None,
            at_rest=conversation.at_rest,
        )


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
        # Recorded before the shadow runs, not after, because the shadow cannot move this and that
        # is the whole property being defended: whatever it says, the turn is read as `decided`.
        # `changed` is therefore False on every turn a shadow ever sees, which is the correct
        # reading of "did this alter what the turn answered" and the reason the disagreement rate
        # stays where it already is — in `disagreed` and in `turn.interpreted`.
        _decided(decided, decided, by=_SHADOW)
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
        return _decided(
            deterministic,
            await self._read(sentence, conversation, deterministic),
            by=_PROMOTED,
        )

    async def _read(
        self, sentence: str, conversation: Conversation, deterministic: Intent
    ) -> Intent:
        """Which reading stands. Extracted so the seam above has one exit to record.

        Every path that keeps the router's reading — the payment rule, the model failing, the
        model proposing a payment — returns `deterministic`, so the comparison in `_decided` is
        the same one this method makes, expressed once instead of at four returns.
        """
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


# Broad stems, not complete phrases. Their only job is deciding whether the catch-all is worth a
# second reading; the model still chooses the intent and the guards below still decide whether that
# choice is safe to use. This keeps an ordinary financial query on the one-model-call path while
# letting phrasings such as "personas de mi agenda", "propósito del asistente" and misspelt balance
# questions reach the reader that can understand them.
_MAY_BE_A_SHORTCUT: Final = re.compile(
    r"\b(tarj|agenda|contact|asistent|finguard|categor|respuesta|cifra|cantidad|saldo|"
    r"dispon|cuent|dinero|tien|tng|huch|ahorr|fondo|agradec|graci|salud|adios|"
    r"explic|revis|error)",
    re.IGNORECASE,
)
_QUERY_SPECIFIC_SHORTCUT: Final = re.compile(
    r"\b(tarj|agenda|contact|asistent|finguard|categor)", re.IGNORECASE
)
_GROUNDED_RESULT_REFERENCE: Final = re.compile(
    r"\b(?:ese|esa|este|esta|aquel|aquella|esos|esas|estos|estas|aquellos|aquellas)\s+"
    r"(?:categoria|comercio|tienda|establecimiento|cuenta|movimiento|movimientos|"
    r"operacion|operaciones|cargo|cargos|pago|pagos|compra|compras|ingreso|ingresos)\b"
)


def _worth_a_second_read(sentence: str, conversation: Conversation) -> bool:
    """Whether a deterministic catch-all resembles one of the shortcut intents.

    A model call on every genuine spending question would add classification latency in front of
    the planning call that question already needs. The broad markers above name the families that
    bypass planning altogether, so only a catch-all that may belong to one of them pays for the
    second opinion. Corrections and explanations also need an answer behind them; without one there
    is no context for a model to recover.
    """
    folded = spanish.folded(sentence)
    reference = result_reference_of(conversation, focus_of(conversation))
    if (
        _GROUNDED_RESULT_REFERENCE.search(folded) is not None
        and reference is not None
        and (
            (reference.group_by is not None and reference.group_key is not None)
            or bool(reference.transaction_ids)
        )
    ):
        # The previous answer already supplies the missing noun. A model cannot improve on a typed
        # category/merchant/account row or an exact UUID set; it can only redirect the intent.
        return False
    if _MAY_BE_A_SHORTCUT.search(folded) is None:
        return False
    if intent_router.asks_for_something_else(sentence) and (
        _QUERY_SPECIFIC_SHORTCUT.search(folded) is None
    ):
        # "dinero/cuenta" also occur in ordinary spending questions. Those already need the query
        # planner, so a classifier in front of it would add latency and another chance to redirect
        # a sentence the deterministic path understood.
        return False
    if conversation.last_plan is None and any(
        marker in folded for marker in ("respuesta", "cifra", "cantidad", "explic", "revis")
    ):
        return False
    return True


def _contextual_fragment(sentence: str) -> bool:
    """Whether a short phrase is more likely to narrow the previous plan than replace it."""
    cleaned = spanish.folded(sentence).strip(" \t\u00bf?\u00a1!.,;:")
    return len(cleaned.split()) <= 5 or cleaned.startswith(("y ", "el total", "en total"))


class Hybrid:
    """Rules keep every decision they can make; a model widens only the catch-all.

    `Promoted` lets the model replace a known deterministic reading. Measurements showed that this
    regresses precisely the contextual turns the rules already handle well. This arrangement moves
    the other boundary instead: a rule that recognised a turn remains authoritative, while a turn
    it called `FINANCIAL_QUERY` may be recovered as a safe shortcut.

    A model can never create a payment or an own-account transfer. It may propose a balance only
    when the sentence does not explicitly ask for movements and is not a fragment narrowing the
    previous financial plan. Those are read-only operations, but a wrong balance is still a real
    figure answering another question and therefore needs its own guard.
    """

    def __init__(self, model: TurnInterpreterPort, router: TurnInterpreterPort) -> None:
        self._model = model
        self._router = router

    async def interpret(self, sentence: str, *, conversation: Conversation) -> Intent:
        deterministic = await self._router.interpret(sentence, conversation=conversation)
        return _decided(
            deterministic,
            await self._read(sentence, conversation, deterministic),
            by=_HYBRID,
        )

    async def _read(
        self, sentence: str, conversation: Conversation, deterministic: Intent
    ) -> Intent:
        """Which reading stands. Extracted so the seam above has one exit to record.

        Six of the seven returns below hand back the router's own reading — a turn the rules
        already claimed, a catch-all not worth a second opinion, a model that failed, and the three
        guards on what a model may propose. Only the last can differ, so `_decided` sees the whole
        decision in one comparison rather than needing a record at each way out.
        """
        if deterministic is not Intent.FINANCIAL_QUERY:
            return deterministic
        if not _worth_a_second_read(sentence, conversation):
            return deterministic

        try:
            read = await self._model.interpret(sentence, conversation=conversation)
        except Exception:
            logger.warning("turn.hybrid_model_failed", exc_info=True)
            return deterministic

        if read in {Intent.PAYMENT, Intent.OWN_TRANSFER}:
            logger.info("turn.action_refused_to_model proposed=%s", read.value)
            return deterministic
        if read in {Intent.CORRECTION, Intent.EXPLAIN_PREVIOUS} and (
            conversation.last_plan is None
        ):
            return deterministic
        if read is Intent.ACCOUNT_BALANCE:
            if intent_router.asks_for_something_else(sentence):
                return deterministic
            if (
                conversation.last_plan is not None
                and not conversation.answered_the_balance
                and _contextual_fragment(sentence)
            ):
                return deterministic
        return read
