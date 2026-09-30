import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx
from a2a.client import A2ACardResolver
from a2a.client.errors import AgentCardResolutionError
from google.protobuf.json_format import ParseError

from finguard_a2a.application.conformance import assess, refused
from finguard_a2a.domain.agents import AgentStatus, RegisteredAgent
from finguard_a2a.ports.agent_registry import AgentRegistry


@dataclass
class _Entry:
    status: AgentStatus
    expires_at: float


class Directory:
    """What the gateway knows about each agent: its card, judged by the gate, kept for a TTL."""

    def __init__(
        self,
        registry: AgentRegistry,
        http: httpx.AsyncClient,
        credentials: Callable[[str], str | None],
        ttl_seconds: float,
        clock: Callable[[], float] = time.monotonic,
        on_discovered: Callable[[AgentStatus], None] | None = None,
    ) -> None:
        self._registry = registry
        self._http = http
        self._credentials = credentials
        self._ttl = ttl_seconds
        self._clock = clock
        self._on_discovered = on_discovered
        self._entries: dict[str, _Entry] = {}

    async def statuses(self) -> tuple[AgentStatus, ...]:
        return tuple([await self._status_of(a) for a in self._registry.agents()])

    async def status(self, agent_id: str) -> AgentStatus | None:
        for agent in self._registry.agents():
            if agent.id == agent_id:
                return await self._status_of(agent)
        return None

    async def _status_of(self, agent: RegisteredAgent) -> AgentStatus:
        entry = self._entries.get(agent.id)
        if entry is not None and self._clock() < entry.expires_at:
            return entry.status
        status = await self._discover(agent)
        if self._on_discovered is not None:
            self._on_discovered(status)
        self._entries[agent.id] = _Entry(status, self._clock() + self._ttl)
        return status

    async def _discover(self, agent: RegisteredAgent) -> AgentStatus:
        try:
            card = await A2ACardResolver(self._http, agent.base_url).get_agent_card()
        except (AgentCardResolutionError, httpx.HTTPError):
            return refused(agent, None, "card_unreachable")
        except (TypeError, AttributeError, ValueError, ParseError):
            return refused(agent, None, "card_invalid")
        return assess(agent, card, self._credentials(agent.id))
