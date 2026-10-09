"""Reads one turn with a model, held to the closed operations schema.

One call per turn, and one more only when the answer cannot be read: the repair sends the answer
back with what was wrong with it. A second unreadable answer is not retried again. It becomes
`clarify`, because the model did answer, the person can still be asked, and a third call costs
another turn's latency to find out what two did not.

A provider that cannot answer at all is different: no question to the person fixes it, so it is
raised as `InterpretationFailedError` for the handler to report. There is no fallback across
models here; walking the catalogue is the provider layer's policy and lives there.

**On a hosted runtime with strict decoding there is often nothing to repair.** Groq's strict
`json_schema` refuses an answer that breaks the schema with an HTTP 400, and an answer cut off by
the token budget either with a 400 ("Failed to validate JSON") or with `finish_reason: length` and
no content. `OpenAiCompatibleProvider` raises `ProviderFailedError` for each, so each becomes
`InterpretationFailedError` with no repair call: there is no answer to send back. The repair is
for a runtime that returns what the model wrote, which Ollama does when an answer is valid against
the grammar but refused by the domain, or truncated at `num_predict`.
"""

import logging
from collections.abc import Mapping
from datetime import date
from typing import Any
from uuid import UUID

from finguard_orchestrator.domain.engine.operations import Clarify, Operation
from finguard_orchestrator.domain.engine.state import DialogueState, Missing
from finguard_orchestrator.domain.ports.llm_provider import LlmProviderPort
from finguard_orchestrator.domain.ports.turn_operations import (
    NO_ACCOUNT_NAMES,
    InterpretationFailedError,
)
from finguard_orchestrator.infrastructure.engine.operations_prompt import (
    MAX_SENTENCE,
    build_prompt,
    repair_note,
)
from finguard_orchestrator.infrastructure.engine.operations_schema import (
    UnreadableError,
    read,
    schema_for,
)

logger = logging.getLogger(__name__)


class LlmTurnOperations:
    """Implements TurnOperationsPort against one model."""

    def __init__(
        self,
        provider: LlmProviderPort,
        *,
        model: str,
        temperature: float,
        max_output_tokens: int,
        categories: Mapping[str, str],
    ) -> None:
        """`categories` maps each catalogue key to its short Spanish name: the keys close the
        schema, the names are the prompt's glossary."""
        self._provider = provider
        self._model = model
        self._temperature = temperature
        self._max_output_tokens = max_output_tokens
        self._categories = dict(categories)
        self._schema = schema_for(self._categories)

    async def interpret(
        self,
        sentence: str,
        state: DialogueState,
        *,
        today: date,
        account_names: Mapping[UUID, str] = NO_ACCOUNT_NAMES,
    ) -> tuple[Operation, ...]:
        if len(sentence) > MAX_SENTENCE:
            # Past what the prompt's budget holds (ruling R16): asked back, not sent truncated.
            return (Clarify(Missing.SUBJECT),)
        prompt = build_prompt(
            sentence,
            state,
            today=today,
            categories=self._categories,
            account_names=account_names,
        )
        answer = await self._complete(prompt)
        try:
            return read(answer, self._categories)
        except UnreadableError as refused:
            # The log gets types and paths; only the model, asked again, gets what it wrote.
            logger.warning("engine.operations_refused problem=%s", refused.logged)
            answer = await self._complete(prompt + repair_note(answer, refused.problem))
        try:
            return read(answer, self._categories)
        except UnreadableError as refused_again:
            logger.warning("engine.operations_unreadable problem=%s", refused_again.logged)
            return (Clarify(Missing.SUBJECT),)

    async def _complete(self, prompt: str) -> str:
        schema: dict[str, Any] = self._schema
        try:
            return await self._provider.complete(
                prompt,
                model=self._model,
                temperature=self._temperature,
                max_output_tokens=self._max_output_tokens,
                schema=schema,
            )
        except Exception as failed:
            # Whatever the adapter raised, the turn has no reading; the handler needs one error to
            # report it by, not one per runtime. Named by its type and nothing else, and not
            # chained: a hosted runtime's error can carry the generation it refused, which can be
            # the person's sentence, and a traceback logged upstream would print it.
            raise InterpretationFailedError(
                f"the interpreter's model failed: {type(failed).__name__}"
            ) from None
