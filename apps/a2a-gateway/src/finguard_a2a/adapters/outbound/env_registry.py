from urllib.parse import urlsplit

from finguard_a2a.domain.agents import RegisteredAgent


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
    return RegisteredAgent(id=parts.hostname.lower().replace(".", "-"), base_url=url.rstrip("/"))
