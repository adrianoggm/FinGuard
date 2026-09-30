from dataclasses import dataclass
from typing import Literal, Protocol

from finguard_a2a.domain.agents import AgentStatus


@dataclass(frozen=True)
class RemoteReply:
    state: str
    task_id: str | None
    context_id: str | None
    parts: tuple[dict[str, object], ...]


class RemoteError(Exception):
    def __init__(
        self, code: Literal["reply_invalid", "reply_too_large", "agent_error", "timeout"]
    ) -> None:
        super().__init__(code)
        self.code = code


class RemoteAgents(Protocol):
    async def send(
        self,
        status: AgentStatus,
        *,
        skill_id: str,
        message_id: str,
        text: str,
        data: dict[str, object] | None,
        context_id: str | None,
    ) -> RemoteReply: ...
