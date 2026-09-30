import hashlib
from typing import Final
from urllib.parse import urlsplit

from a2a.types import AgentCard
from a2a.utils.errors import InvalidParamsError
from a2a.utils.proto_utils import validate_proto_required_fields
from google.protobuf.json_format import MessageToJson

from finguard_a2a.domain.agents import AgentStatus, Reason, RegisteredAgent

_BINDINGS: Final = frozenset({"JSONRPC", "HTTP+JSON"})
_SUPPORTED: Final = ("api_key_security_scheme", "http_auth_security_scheme")


def digest(card: AgentCard | None) -> str:
    body = MessageToJson(card, sort_keys=True) if card is not None else ""
    return "sha256:" + hashlib.sha256(body.encode()).hexdigest()


def refused(agent: RegisteredAgent, card: AgentCard | None, reason: Reason) -> AgentStatus:
    return AgentStatus(agent, card, False, reason, digest(card), (), None)


def assess(agent: RegisteredAgent, card: AgentCard, credential: str | None) -> AgentStatus:
    """The gate: the first failure wins, and only a card that passes all of it is usable."""
    try:
        validate_proto_required_fields(card)
    except InvalidParamsError as error:
        # An empty skill list is the SDK's complaint too, but it has a reason of its own.
        if not _only_skills_missing(error):
            return refused(agent, card, "card_invalid")
    interfaces = [i for i in card.supported_interfaces if i.protocol_version.startswith("1.")]
    if not interfaces:
        return refused(agent, card, "version_unsupported")
    if not any(i.protocol_binding in _BINDINGS for i in interfaces):
        return refused(agent, card, "binding_unsupported")
    if not card.skills:
        return refused(agent, card, "no_skills")
    header = _credential_header(card, credential)
    if isinstance(header, str):
        return refused(agent, card, header)
    return AgentStatus(agent, card, True, None, digest(card), link_hosts(agent, card), header)


def _only_skills_missing(error: InvalidParamsError) -> bool:
    problems = (error.data or {}).get("errors", [])
    return bool(problems) and all(p.get("field") == "skills" for p in problems)


def _credential_header(card: AgentCard, credential: str | None) -> tuple[str, str] | Reason | None:
    if not card.security_requirements:
        return None
    names: list[str] = []
    for requirement in card.security_requirements:
        candidate = list(requirement.schemes)
        if candidate and all(
            n in card.security_schemes
            and card.security_schemes[n].WhichOneof("scheme") in _SUPPORTED
            for n in candidate
        ):
            names = candidate
            break
    else:
        return "auth_unsupported"
    if credential is None:
        return "credential_missing"
    # One credential fills one header: a requirement that combines schemes cannot be met.
    if len(names) != 1:
        return "auth_unsupported"
    scheme = card.security_schemes[names[0]]
    if scheme.WhichOneof("scheme") == "api_key_security_scheme":
        key = scheme.api_key_security_scheme
        return (key.name, credential) if key.location == "header" else "auth_unsupported"
    if scheme.http_auth_security_scheme.scheme.lower() == "bearer":
        return ("Authorization", f"Bearer {credential}")
    return "auth_unsupported"


def link_hosts(agent: RegisteredAgent, card: AgentCard) -> tuple[str, ...]:
    """Hosts a link in a reply may point at: where the agent is registered, and its provider."""
    hosts = [_host(agent.base_url)]
    if card.HasField("provider"):
        hosts.append(_host(card.provider.url))
    out: list[str] = []
    for host in hosts:
        if host and host not in out:
            out.append(host)
    return tuple(out)


def _host(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return ""
    return parts.netloc.lower().rpartition("@")[2]
