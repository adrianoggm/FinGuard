import re
from typing import Final
from urllib.parse import urlsplit

from finguard_a2a.domain.agents import RegisteredAgent

# The orchestrator's `AGENT_ID`: it holds an agent id in a request path and refuses any other form,
# so an id outside it is an agent nobody could ask (ruling R30).
_AGENT_ID: Final = re.compile(r"[a-z0-9-]{1,64}")


class EnvRegistry:
    """An agent is one base URL; its id is the host, lower-cased, dots turned into dashes."""

    def __init__(self, urls: str) -> None:
        agents: list[RegisteredAgent] = []
        seen: set[str] = set()
        for raw in urls.split(","):
            agent = _parse(raw.strip())
            if agent.id in seen:
                raise ValueError(f"two agents share the id {agent.id!r}")
            seen.add(agent.id)
            agents.append(agent)
        self._agents = tuple(agents)

    def agents(self) -> tuple[RegisteredAgent, ...]:
        return self._agents


def _parse(url: str) -> RegisteredAgent:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(f"not an http(s) agent URL: {url!r}")
    agent_id = parts.hostname.lower().replace(".", "-")
    if not _AGENT_ID.fullmatch(agent_id):
        raise ValueError(f"the agent id {agent_id!r} of {url!r} is not [a-z0-9-]{{1,64}}")
    return RegisteredAgent(id=agent_id, base_url=url.rstrip("/"))
