"""Speaks the A2A gateway's internal contract (spec §4.3), by hand, like the banking clients.

The gateway is the only thing that speaks A2A; this is a small JSON contract in front of it, and the
orchestrator never sees a card, a JSON-RPC envelope or an SDK object. The shapes are stated twice --
there, and here -- and the mitigation is the same as banking's: both sides read strictly, so drift
is a refusal rather than a quiet misreading.

Every failure becomes an `ExpertAgentsError` with a closed code:

    connection, DNS, read error, client timeout     unreachable
    401                                             unauthorised (the shared token is wrong)
    an error body with one of the closed codes      that code, with the gateway's `reason`
    anything else, or a 2xx outside the contract    reply_invalid

Nothing is retried. A lost answer to a booking is an unknown outcome, never a reason to send it
again (§5.2), so the decision belongs to the caller that knows which kind of message it was.
"""

from typing import Annotated, Any, Final, Literal, cast, get_args

import httpx2 as httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic.alias_generators import to_camel

from finguard_orchestrator.domain.errors import InvalidValueError
from finguard_orchestrator.domain.models.expert_agents import (
    AGENT_ID,
    AgentErrorCode,
    AgentReply,
    AgentSkill,
    DataReplyPart,
    ExpertAgent,
    ExpertAgentsError,
    Outbound,
    ReplyState,
    TextReplyPart,
)
from finguard_orchestrator.domain.ports.expert_agents import ExpertAgentsPort

_CODES: Final = frozenset(get_args(AgentErrorCode))


class _Wire(BaseModel):
    """camelCase on the wire, read strictly: a wrong type is a refusal, never a coercion. Unknown
    keys are ignored so the gateway can add one (`inputModes`) without breaking this side."""

    model_config = ConfigDict(strict=True, alias_generator=to_camel, extra="ignore")


class _SkillWire(_Wire):
    id: str
    name: str
    description: str
    tags: list[str]
    examples: list[str]


class _AgentWire(_Wire):
    id: str
    name: str
    usable: bool
    reason: str | None
    card_digest: str
    link_hosts: list[str]
    skills: list[_SkillWire]


class _ListingWire(_Wire):
    agents: list[_AgentWire]


class _TextPartWire(_Wire):
    kind: Literal["text"]
    text: str


class _DataPartWire(_Wire):
    kind: Literal["data"]
    data: dict[str, Any]


class _ReplyWire(_Wire):
    state: ReplyState
    task_id: str | None
    context_id: str | None
    parts: list[Annotated[_TextPartWire | _DataPartWire, Field(discriminator="kind")]]
    latency_ms: int


class HttpExpertAgents(ExpertAgentsPort):
    """Implements ExpertAgentsPort over the gateway's HTTP contract."""

    def __init__(self, *, base_url: str, token: str, timeout_seconds: float) -> None:
        """No defaults: the URL and the token belong to a deployment, and the timeout is the
        orchestrator's per-agent budget, read from .env.defaults beside its reason."""
        self._base_url = base_url.rstrip("/")
        self._token = token
        self.timeout_seconds = timeout_seconds

    def _client(self) -> httpx.AsyncClient:
        """One client per call, as the banking clients do: nothing pooled across requests."""
        return httpx.AsyncClient(timeout=self.timeout_seconds)

    async def agents(self) -> tuple[ExpertAgent, ...]:
        response = await self._call("GET", f"{self._base_url}/v1/agents")
        try:
            listing = _ListingWire.model_validate_json(response.content)
            return tuple(_agent(agent) for agent in listing.agents)
        except (ValidationError, InvalidValueError) as exc:
            raise ExpertAgentsError("reply_invalid", "listing") from exc

    async def send(
        self,
        agent_id: str,
        *,
        skill_id: str,
        message_id: str,
        outbound: Outbound,
        context_id: str | None,
    ) -> AgentReply:
        if not AGENT_ID.fullmatch(agent_id):
            # It goes into the path: an id that is not one never reaches the wire.
            raise ExpertAgentsError("agent_unknown")
        response = await self._call(
            "POST",
            f"{self._base_url}/v1/agents/{agent_id}/messages",
            json={
                "skillId": skill_id,
                "messageId": message_id,
                "text": outbound.text,
                "data": dict(outbound.data),
                "contextId": context_id,
            },
        )
        try:
            return _reply(_ReplyWire.model_validate_json(response.content))
        except ValidationError as exc:
            raise ExpertAgentsError("reply_invalid", "reply") from exc

    async def _call(
        self, method: str, url: str, *, json: dict[str, object] | None = None
    ) -> httpx.Response:
        try:
            async with self._client() as client:
                response = await client.request(
                    method, url, json=json, headers={"Authorization": f"Bearer {self._token}"}
                )
        except httpx.HTTPError as exc:
            raise ExpertAgentsError("unreachable", type(exc).__name__) from exc
        if response.status_code >= 400:
            raise refusal_for(response.status_code, _body_or_empty(response))
        return response


def refusal_for(status: int, body: dict[str, Any]) -> ExpertAgentsError:
    """The closed error for a status the gateway refused with. Pure, like banking's."""
    if status == 401:
        return ExpertAgentsError("unauthorised")
    code, reason = body.get("code"), body.get("reason")
    if isinstance(code, str) and code in _CODES:
        return ExpertAgentsError(
            cast(AgentErrorCode, code), reason if isinstance(reason, str) else None
        )
    return ExpertAgentsError("reply_invalid", f"HTTP {status}")


def _body_or_empty(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _agent(wire: _AgentWire) -> ExpertAgent:
    return ExpertAgent(
        id=wire.id,
        name=wire.name,
        usable=wire.usable,
        reason=wire.reason,
        card_digest=wire.card_digest,
        link_hosts=tuple(wire.link_hosts),
        skills=tuple(
            AgentSkill(
                id=skill.id,
                name=skill.name,
                description=skill.description,
                tags=tuple(skill.tags),
                examples=tuple(skill.examples),
            )
            for skill in wire.skills
        ),
    )


def _reply(wire: _ReplyWire) -> AgentReply:
    return AgentReply(
        state=wire.state,
        task_id=wire.task_id,
        context_id=wire.context_id,
        parts=tuple(
            TextReplyPart(part.text)
            if isinstance(part, _TextPartWire)
            else DataReplyPart(part.data)
            for part in wire.parts
        ),
        latency_ms=wire.latency_ms,
    )
