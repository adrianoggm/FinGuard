import asyncio
import json
from typing import Final

import httpx
from a2a.client import A2AClientError, A2AClientTimeoutError, ClientConfig, ClientFactory
from a2a.client.client import Client
from a2a.helpers import new_data_part, new_text_part
from a2a.types import (
    AgentCard,
    Message,
    Part,
    Role,
    SendMessageConfiguration,
    SendMessageRequest,
    StreamResponse,
    TaskState,
)
from a2a.utils import TransportProtocol
from a2a.utils.errors import A2AError
from google.protobuf.json_format import MessageToDict, ParseError
from google.protobuf.struct_pb2 import Struct, Value

from finguard_a2a.domain.agents import AgentStatus
from finguard_a2a.ports.remote_agents import RemoteError, RemoteReply

_STATES: Final = frozenset(
    {"COMPLETED", "INPUT_REQUIRED", "AUTH_REQUIRED", "FAILED", "REJECTED", "CANCELED"}
)
_OUTPUT_MODES: Final = ["text/plain", "application/json"]


class _Through(httpx.AsyncBaseTransport):
    """Lets each agent's client, which carries that agent's credential, reuse the shared client."""

    def __init__(self, shared: httpx.AsyncClient) -> None:
        self._shared = shared

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return await self._shared.send(request, stream=True)


class SdkRemoteAgents:
    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        timeout_seconds: float,
        max_text_bytes: int,
        max_data_bytes: int,
    ) -> None:
        self._shared = http
        self._timeout = timeout_seconds
        self._max_text = max_text_bytes
        self._max_data = max_data_bytes
        # One client per usable agent and card: its credential header lives in the client, and
        # the SDK's own close() would close that client, so it is kept for the process.
        self._clients: dict[str, tuple[str, httpx.AsyncClient, Client]] = {}

    async def aclose(self) -> None:
        for _, http, _ in self._clients.values():
            await http.aclose()
        self._clients.clear()

    async def send(
        self,
        status: AgentStatus,
        *,
        skill_id: str,
        message_id: str,
        text: str,
        data: dict[str, object] | None,
        context_id: str | None,
    ) -> RemoteReply:
        card = status.card
        if card is None:
            raise RemoteError("agent_error")
        parts = [new_text_part(text)]
        if data is not None and _accepts_json(card, skill_id):
            parts.append(new_data_part(data))
        request = SendMessageRequest(
            message=Message(
                role=Role.ROLE_USER,
                message_id=message_id,
                context_id=context_id or "",
                parts=parts,
                metadata=Struct(fields={"finguardSkill": Value(string_value=skill_id)}),
            ),
            configuration=SendMessageConfiguration(accepted_output_modes=_OUTPUT_MODES),
        )
        try:
            client = self._client_for(status, card)
            response = await asyncio.wait_for(self._one(client, request), self._timeout)
        except (A2AClientTimeoutError, TimeoutError, httpx.TimeoutException) as error:
            raise RemoteError("timeout") from error
        except (A2AError, A2AClientError, httpx.HTTPError) as error:
            raise RemoteError("agent_error") from error
        except (ValueError, ParseError) as error:
            raise RemoteError("reply_invalid") from error
        return self._reply(response)

    def _client_for(self, status: AgentStatus, card: AgentCard) -> Client:
        cached = self._clients.get(status.agent.id)
        if cached is not None and cached[0] == status.card_digest:
            return cached[2]
        headers = {}
        if status.credential_header is not None:
            headers[status.credential_header[0]] = status.credential_header[1]
        http = httpx.AsyncClient(
            transport=_Through(self._shared), headers=headers, timeout=self._timeout
        )
        client = ClientFactory(
            ClientConfig(
                streaming=False,
                httpx_client=http,
                supported_protocol_bindings=[
                    TransportProtocol.JSONRPC,
                    TransportProtocol.HTTP_JSON,
                ],
            )
        ).create(card)
        self._clients[status.agent.id] = (status.card_digest, http, client)
        if cached is not None:
            asyncio.get_running_loop().create_task(cached[1].aclose())
        return client

    @staticmethod
    async def _one(client: Client, request: SendMessageRequest) -> StreamResponse:
        async for response in client.send_message(request):
            return response  # streaming is off: there is exactly one
        raise ValueError("the agent sent no reply")

    def _reply(self, response: StreamResponse) -> RemoteReply:
        payload = response.WhichOneof("payload")
        if payload == "message":
            message = response.message
            parts = self._parts(list(message.parts))
            return RemoteReply("COMPLETED", None, message.context_id or None, parts)
        if payload != "task":
            raise RemoteError("reply_invalid")
        task = response.task
        state = TaskState.Name(task.status.state).removeprefix("TASK_STATE_")
        if state not in _STATES:
            raise RemoteError("reply_invalid")
        raw: list[Part] = [p for artifact in task.artifacts for p in artifact.parts]
        raw.extend(task.status.message.parts)
        return RemoteReply(state, task.id or None, task.context_id or None, self._parts(raw))

    def _parts(self, raw: list[Part]) -> tuple[dict[str, object], ...]:
        parts: list[dict[str, object]] = []
        text_bytes = data_bytes = 0
        for part in raw:
            kind = part.WhichOneof("content")
            if kind == "text":
                text_bytes += len(part.text.encode())
                parts.append({"kind": "text", "text": part.text})
            elif kind == "data":
                data = _plain(MessageToDict(part.data))
                data_bytes += len(json.dumps(data).encode())
                parts.append({"kind": "data", "data": data})
        if text_bytes > self._max_text or data_bytes > self._max_data:
            raise RemoteError("reply_too_large")
        return tuple(parts)


def _accepts_json(card: AgentCard, skill_id: str) -> bool:
    modes: list[str] = list(card.default_input_modes)
    for skill in card.skills:
        if skill.id == skill_id and skill.input_modes:
            modes = list(skill.input_modes)
    return "application/json" in modes


def _plain(value: object) -> object:
    """Protobuf numbers are doubles; a whole one is an integer to every reader of this wire."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value
