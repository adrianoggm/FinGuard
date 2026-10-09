"""Reads one turn with a model, held to the closed operations schema.

One call per turn, and one more only when the answer cannot be read: the repair sends the answer
back with what was wrong with it. A second unreadable answer is not retried again. It becomes
`clarify`, because the model did answer, the person can still be asked, and a third call costs
another turn's latency to find out what two did not.

A provider that cannot answer at all is different: no question to the person fixes it, so it is
raised as `InterpretationFailedError` for the handler to report. There is no fallback across
models here; walking the catalogue is the provider layer's policy and lives there.

**A hosted runtime with strict decoding refuses a wrong shape after generating it.** Groq's
strict `json_schema` answers it with an HTTP 400 `json_validate_failed`, usually with what the
model wrote. The provider raises that as `AnswerRefusedError`, and it is read here exactly like an
answer that cannot be read (ruling R31): the one repair, sending back what was written when the
provider returned it, and then a question back. Any other provider failure (an outage, a budget
exhausted with no content) is still `InterpretationFailedError`, with no repair call.
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID

from finguard_orchestrator.domain.engine.operations import Clarify, Operation
from finguard_orchestrator.domain.engine.state import DialogueState, Missing
from finguard_orchestrator.domain.errors import AnswerRefusedError
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

# Told to the model with the repair when the runtime refused its answer itself, which says only
# that the shape was wrong and not where.
_REFUSED_PROBLEM = "The answer did not match the required JSON schema; answer again matching it."


@dataclass(frozen=True)
class _Refused:
    """An answer the provider refused against the schema after generating it, with what was
    written when the provider returned it."""

    generation: str | None


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
            return self._read(answer)
        except UnreadableError as refused:
            # The log gets types and paths; only the model, asked again, gets what it wrote.
            logger.warning("engine.operations_refused problem=%s", refused.logged)
            written = answer.generation or "" if isinstance(answer, _Refused) else answer
            answer = await self._complete(prompt + repair_note(written, refused.problem))
        try:
            return self._read(answer)
        except UnreadableError as refused_again:
            logger.warning("engine.operations_unreadable problem=%s", refused_again.logged)
            return (Clarify(Missing.SUBJECT),)

    def _read(self, answer: "str | _Refused") -> tuple[Operation, ...]:
        if isinstance(answer, _Refused):
            raise UnreadableError(_REFUSED_PROBLEM, "refused_by_the_provider_against_the_schema")
        return read(answer, self._categories)

    async def _complete(self, prompt: str) -> "str | _Refused":
        schema: dict[str, Any] = self._schema
        try:
            return await self._provider.complete(
                prompt,
                model=self._model,
                temperature=self._temperature,
                max_output_tokens=self._max_output_tokens,
                schema=schema,
            )
        except AnswerRefusedError as refused:
            # The model answered, in a shape the runtime refused after generating it: an answer to
            # repair, not an outage (ruling R31).
            return _Refused(refused.generation)
        except Exception as failed:
            # Whatever the adapter raised, the turn has no reading; the handler needs one error to
            # report it by, not one per runtime. Named by its type and nothing else, and not
            # chained: a hosted runtime's error can carry the generation it refused, which can be
            # the person's sentence, and a traceback logged upstream would print it.
            raise InterpretationFailedError(
                f"the interpreter's model failed: {type(failed).__name__}"
            ) from None
