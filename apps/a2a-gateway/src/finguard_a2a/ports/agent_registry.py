from typing import Protocol

from finguard_a2a.domain.agents import RegisteredAgent


class AgentRegistry(Protocol):
    def agents(self) -> tuple[RegisteredAgent, ...]: ...
