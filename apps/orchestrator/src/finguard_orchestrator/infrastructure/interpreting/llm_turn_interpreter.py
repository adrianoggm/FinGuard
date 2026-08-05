"""Reads one turn with a model, constrained to the nine intents.

The schema is generated from the enum rather than written out, for the reason the plan schema is:
a hand-copied list of nine names is a list that goes stale the first time somebody adds a tenth.

**It never raises.** Every way this can fail — a dead daemon, a malformed answer, a name that is not
an intent — becomes `FINANCIAL_QUERY`, which is the long way round and always safe. An exception
here would move a decision about what to tell the user into a layer with nothing to tell them, and
this is a component being evaluated: it has to be possible to run it in shadow against real traffic
without it being able to break anything.

That is also why there is no fallback across models. Walking the catalogue is application policy and
already lives in the handler; a second copy here would be two places deciding what a failure means.
"""

import json
import logging
from typing import Any, Final

from finguard_orchestrator.domain.models.conversation import Conversation
from finguard_orchestrator.domain.ports.llm_provider import LlmProviderPort
from finguard_orchestrator.domain.services.intent_router import Intent
from finguard_orchestrator.infrastructure.interpreting.turn_prompt import build_prompt

logger = logging.getLogger(__name__)

# One required field with a closed set of values. A constrained decoder cannot reach anything else,
# which turns "did it answer with a real intent" from something to validate into something the
# runtime makes unreachable — and leaves this class checking only what a decoder cannot promise.
SCHEMA: Final[dict[str, Any]] = {
    "type": "object",
    "properties": {"intent": {"type": "string", "enum": [intent.value for intent in Intent]}},
    "required": ["intent"],
    "additionalProperties": False,
}

# Deliberately small. This asks for one word, and a model given room to explain itself takes it —
# which costs latency on every single turn of every conversation.
MAX_OUTPUT_TOKENS: Final = 32


class LlmTurnInterpreter:
    """Implements TurnInterpreterPort against one model."""

    def __init__(self, provider: LlmProviderPort, *, model: str) -> None:
        """The model by name, like the planner takes it, and nothing else.

        No temperature and no token budget: both are decided here rather than configured, because
        both are properties of *this* job. Classification wants zero temperature — the same turn
        twice has to be the same intent, or a conversation stops being reproducible and so does
        every measurement of one — and it wants a tiny budget, because a model given room to
        explain itself takes it, on every turn of every conversation.
        """
        self._provider = provider
        self._model = model

    async def interpret(self, sentence: str, *, conversation: Conversation) -> Intent:
        try:
            answered = await self._provider.complete(
                build_prompt(sentence, conversation),
                model=self._model,
                temperature=0.0,
                max_output_tokens=MAX_OUTPUT_TOKENS,
                schema=SCHEMA,
            )
        except Exception:
            logger.warning("turn.interpreter_unavailable", exc_info=True)
            return Intent.FINANCIAL_QUERY

        return _read(answered)


def _read(answered: str) -> Intent:
    """The intent in the answer, or the safe one.

    Nothing is normalised and nothing is guessed at. A decoder that honoured the schema cannot get
    here with anything else; a decoder that did not is a runtime this pipeline cannot rely on, and
    the honest reading of its output is that there is none.
    """
    try:
        named = json.loads(answered).get("intent")
        return Intent(named)
    except (ValueError, TypeError, AttributeError):
        logger.warning("turn.unreadable_intent")
        return Intent.FINANCIAL_QUERY
