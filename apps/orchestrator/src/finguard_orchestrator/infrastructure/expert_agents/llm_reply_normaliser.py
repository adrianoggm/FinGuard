"""Implements ReplyNormaliserPort: a model reads an agent's reply into a closed schema (§6.2).

Built like `LlmVisualPlanner`: one call under a closed schema, one repair with the same
`STRUCTURE_REPAIR` suffix, a strict parse. An invalid structure twice is `None` -- that agent did
not respond validly -- and a `ProviderFailedError` propagates for the caller to treat the same way.

A reply in `AUTH_REQUIRED`, `FAILED`, `REJECTED` or `CANCELED` never reaches the model: the state
itself is the information, and the one thing read from an `AUTH_REQUIRED` reply, its linking
address, is read deterministically (`agent_reply_verifier.link_in`). `availability` reads
`COMPLETED` and `INPUT_REQUIRED` (whose alternatives are slots); `booking` reads `COMPLETED` only,
because nothing else confirms a booking.

What comes back is a draft. Nothing reads it but the verifier.
"""

import logging
from collections.abc import Callable
from typing import Any, Final

from finguard_orchestrator.domain.models.expert_agents import (
    ActivityQuery,
    ActivitySlot,
    AgentReply,
    AvailabilityDraft,
    BookingDraft,
    ReplyState,
)
from finguard_orchestrator.domain.ports.agent_reading import ReplyNormaliserPort
from finguard_orchestrator.domain.ports.llm_provider import LlmProviderPort
from finguard_orchestrator.infrastructure.expert_agents.agent_answer import (
    parse_availability,
    parse_booking,
)
from finguard_orchestrator.infrastructure.expert_agents.agent_prompt import (
    availability_prompt,
    availability_schema,
    booking_prompt,
    booking_schema,
)
from finguard_orchestrator.infrastructure.llm.structure_repair import STRUCTURE_REPAIR

logger = logging.getLogger(__name__)

_READ: Final[frozenset[ReplyState]] = frozenset({"COMPLETED", "INPUT_REQUIRED"})


class LlmReplyNormaliser(ReplyNormaliserPort):
    def __init__(
        self, provider: LlmProviderPort, *, model: str, temperature: float, max_output_tokens: int
    ) -> None:
        self._provider = provider
        self._model = model
        self._temperature = temperature
        self._max_output_tokens = max_output_tokens
        self._availability_schema = availability_schema()
        self._booking_schema = booking_schema()

    async def availability(
        self, query: ActivityQuery, reply: AgentReply
    ) -> AvailabilityDraft | None:
        if reply.state not in _READ:
            return None
        prompt = availability_prompt(query, reply)
        return await self._read(
            prompt, self._availability_schema, parse_availability, "availability"
        )

    async def booking(self, slot: ActivitySlot, reply: AgentReply) -> BookingDraft | None:
        if reply.state != "COMPLETED":
            # Only a completed reply can confirm a booking; an INPUT_REQUIRED one (a full class)
            # is read for its alternatives through `availability`, never as a confirmation.
            return None
        return await self._read(
            booking_prompt(slot, reply), self._booking_schema, parse_booking, "booking"
        )

    async def _read[T](
        self,
        prompt: str,
        schema: dict[str, Any],
        parse: Callable[[str], T | None],
        reading: str,
    ) -> T | None:
        draft = parse(await self._complete(prompt, schema))
        if draft is None:
            # Never echo the provider's text into the repair prompt or the log (llm_query_planner).
            logger.warning("reply_normaliser.invalid_structure", extra={"reading": reading})
            draft = parse(await self._complete(prompt + STRUCTURE_REPAIR, schema))
            if draft is None:
                logger.warning("reply_normaliser.unread", extra={"reading": reading})
        return draft

    async def _complete(self, prompt: str, schema: dict[str, Any]) -> str:
        return await self._provider.complete(
            prompt,
            model=self._model,
            temperature=self._temperature,
            max_output_tokens=self._max_output_tokens,
            schema=schema,
        )
