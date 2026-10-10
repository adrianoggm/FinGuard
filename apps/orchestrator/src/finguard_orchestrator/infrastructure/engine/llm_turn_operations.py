"""Reads one turn with a model, held to the closed operations schema.

One normal call per turn, plus at most one repair: for unreadable output, or for a payment proposal
that conflicts with an explicit past-transaction question. A repeated mismatch becomes `clarify`;
it never reaches payment preparation.

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
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID

from finguard_orchestrator.domain.engine.operations import (
    Clarify,
    Delegate,
    Operation,
    PrepareBizum,
    PrepareOwnTransfer,
    PrepareTransfer,
)
from finguard_orchestrator.domain.engine.state import DialogueState, Missing
from finguard_orchestrator.domain.errors import AnswerRefusedError
from finguard_orchestrator.domain.ports.llm_provider import LlmProviderPort
from finguard_orchestrator.domain.ports.turn_operations import (
    NO_ACCOUNT_NAMES,
    InterpretationFailedError,
)
from finguard_orchestrator.domain.services import spanish
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
_PAST_PAYMENT = re.compile(
    r"\b(?:envie|enviaste|envio|enviaron|enviado|mande|mandaste|mando|mandaron|mandado|"
    r"pague|pagaste|pago|pagaron|pagado|hice|hiciste|hizo|hicieron|transferi|transferiste|"
    r"transfirio|transfirieron|transferido)\b"
)
_CURRENT_PAYMENT_REQUEST = re.compile(
    r"\b(?:envia(?:me|le|les)?|manda(?:me|le|les)?|haz(?:me|le)?|realiza|prepara|paga|"
    r"transfiere)\b|"
    r"\b(?:quiero|quisiera|me gustaria|voy a|vas a|puedes|podrias|quieres que|quiero que)\s+"
    r"(?:enviar|mandar|hacer|transferir|preparar|pagar|envie|mande|haga|transfiera)\b"
)
_PAYMENT_OPERATIONS = (PrepareBizum, PrepareTransfer, PrepareOwnTransfer)
_PAST_PAYMENT_MISREAD = (
    "This proposes a new payment, but the person's sentence asks about a past transaction. "
    "Interpret it as a read-only financial query using ask or patch; do not prepare a payment."
)


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
        repair_used = False
        try:
            operations = self._read(answer)
        except UnreadableError as refused:
            # The log gets types and paths; only the model, asked again, gets what it wrote.
            logger.warning("engine.operations_refused problem=%s", refused.logged)
            written = answer.generation or "" if isinstance(answer, _Refused) else answer
            answer = await self._complete(prompt + repair_note(written, refused.problem))
            repair_used = True
            try:
                operations = self._read(answer)
            except UnreadableError as refused_again:
                logger.warning("engine.operations_unreadable problem=%s", refused_again.logged)
                return (Clarify(Missing.SUBJECT),)
        if not _past_payment_was_misread(sentence, operations):
            return operations

        # A plausible payment operation is still unsafe when the sentence asks about a completed
        # transaction. Give the planner one structured correction chance; if it repeats the
        # mismatch, stop before any payment or contact-resolution node sees the operation.
        logger.warning("engine.operations_consistency_mismatch problem=past_activity_as_payment")
        if repair_used:
            return (Clarify(Missing.SUBJECT),)
        written = answer.generation or "" if isinstance(answer, _Refused) else answer
        repaired = await self._complete(prompt + repair_note(written, _PAST_PAYMENT_MISREAD))
        try:
            operations = self._read(repaired)
        except UnreadableError as refused:
            logger.warning(
                "engine.operations_consistency_repair_unreadable problem=%s", refused.logged
            )
            return (Clarify(Missing.SUBJECT),)
        if _past_payment_was_misread(sentence, operations):
            logger.warning(
                "engine.operations_consistency_rejected problem=past_activity_as_payment"
            )
            return (Clarify(Missing.SUBJECT),)
        return operations

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


def _past_payment_was_misread(sentence: str, operations: tuple[Operation, ...]) -> bool:
    """Detect a payment proposal for a past-tense transaction without authorizing or routing
    anything. The check can only trigger one bounded repair or a safe clarification."""
    proposes_payment = any(
        isinstance(operation, _PAYMENT_OPERATIONS)
        or (isinstance(operation, Delegate) and operation.to in {"PAYMENT", "TRANSFER"})
        for operation in operations
    )
    if not proposes_payment:
        return False
    words = spanish.folded(sentence)
    return bool(_PAST_PAYMENT.search(words)) and not bool(_CURRENT_PAYMENT_REQUEST.search(words))
