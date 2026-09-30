"""The two readings a model makes of third-party text (spec §6.1, §6.2).

Both propose; neither decides. A classification only narrows which skill is asked, and a
normalised reply is a draft the verifier accepts or rejects whole.
"""

from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from finguard_orchestrator.domain.models.expert_agents import (
    ActivityQuery,
    ActivitySlot,
    AgentReply,
    AvailabilityDraft,
    BookingDraft,
    Capability,
    ExpertAgent,
)


@runtime_checkable
class SkillClassifierPort(Protocol):
    async def classify(self, agent: ExpertAgent) -> Mapping[Capability, str]:
        """Capability -> skill id, for the capabilities exactly one of the card's skills serves.

        A capability absent from the mapping is one the agent does not offer: a classification
        that failed leaves the agent without it, never with a guessed one.
        """
        ...


@runtime_checkable
class ReplyNormaliserPort(Protocol):
    async def availability(
        self, query: ActivityQuery, reply: AgentReply
    ) -> AvailabilityDraft | None:
        """The reply's slots in the closed shape, or `None` when there is nothing to read."""
        ...

    async def booking(self, slot: ActivitySlot, reply: AgentReply) -> BookingDraft | None:
        """The reply's booking confirmation in the closed shape, or `None`."""
        ...
