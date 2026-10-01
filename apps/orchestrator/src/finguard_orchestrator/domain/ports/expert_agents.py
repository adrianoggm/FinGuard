from typing import Protocol, runtime_checkable

from finguard_orchestrator.domain.models.expert_agents import AgentReply, ExpertAgent, Outbound


@runtime_checkable
class ExpertAgentsPort(Protocol):
    """The external expert agents, as the orchestrator needs them: who is there, and ask one.

    Domain types only: no protocol object crosses this port (spec §3.2). Every failure is an
    `ExpertAgentsError` with a closed code. `send` is never retried here or behind it -- the
    caller decides, and a booking is never re-sent (§5.2).
    """

    async def agents(self) -> tuple[ExpertAgent, ...]: ...

    async def send(
        self,
        agent_id: str,
        *,
        skill_id: str,
        message_id: str,
        outbound: Outbound,
        context_id: str | None,
    ) -> AgentReply: ...  # raises ExpertAgentsError
