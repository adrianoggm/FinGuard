from dataclasses import dataclass
from typing import Final, Literal, get_args
from urllib.parse import urlsplit

from a2a.types import AgentCard, AgentInterface

Reason = Literal[
    "card_unreachable",
    "card_invalid",
    "version_unsupported",
    "binding_unsupported",
    "interface_untrusted",
    "no_skills",
    "auth_unsupported",
    "credential_missing",
]
REASONS: Final = get_args(Reason)

BINDINGS: Final = frozenset({"JSONRPC", "HTTP+JSON"})


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


def host_of(url: str) -> str:
    """`host:port` of an http(s) URL, lower case and without userinfo; "" for anything else."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return ""
    return parts.netloc.lower().rpartition("@")[2]


def callable_interfaces(agent: RegisteredAgent, card: AgentCard) -> list[AgentInterface]:
    """The card's 1.x JSONRPC and HTTP+JSON interfaces on the host the agent was registered at:
    the only addresses a message -- and the credential it carries -- is ever sent to. The card is
    the agent's own word; where it may send FinGuard's credential is not (ruling R30)."""
    home = host_of(agent.base_url)
    return [
        interface
        for interface in card.supported_interfaces
        if interface.protocol_version.startswith("1.")
        and interface.protocol_binding in BINDINGS
        and home
        and host_of(interface.url) == home
    ]
