from dataclasses import dataclass
from typing import Final, Literal, get_args

from a2a.types import AgentCard

Reason = Literal[
    "card_unreachable",
    "card_invalid",
    "version_unsupported",
    "binding_unsupported",
    "no_skills",
    "auth_unsupported",
    "credential_missing",
]
REASONS: Final = get_args(Reason)


@dataclass(frozen=True)
class RegisteredAgent:
    id: str
    base_url: str


@dataclass(frozen=True)
class AgentStatus:
    agent: RegisteredAgent
    card: AgentCard | None  # the SDK's type stays inside the gateway
    usable: bool
    reason: Reason | None
    card_digest: str
    link_hosts: tuple[str, ...]
    credential_header: tuple[str, str] | None  # (header name, value)
