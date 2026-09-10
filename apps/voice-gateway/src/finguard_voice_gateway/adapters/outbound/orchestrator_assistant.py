"""Authenticated, streaming client for the orchestrator question endpoint."""

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from finguard_voice_gateway.ports.assistant_client import (
    AssistantClientError,
    AssistantReply,
    AssistantStageCallback,
)

MAX_SSE_EVENT_BYTES = 256 * 1024


async def _sse_events(
    response: httpx.Response, max_event_bytes: int = MAX_SSE_EVENT_BYTES
) -> AsyncIterator[dict[str, Any]]:
    buffer = b""
    data_lines: list[str] = []
    async for chunk in response.aiter_bytes():
        buffer += chunk
        if len(buffer) + sum(len(line.encode()) for line in data_lines) > max_event_bytes:
            raise AssistantClientError("SSE event too large", "ORCHESTRATOR_PROTOCOL")
        while b"\n" in buffer:
            raw, buffer = buffer.split(b"\n", 1)
            try:
                line = raw.rstrip(b"\r").decode("utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                raise AssistantClientError("malformed SSE stream", "ORCHESTRATOR_PROTOCOL") from exc
            if not line:
                if data_lines:
                    yield _decode_event("\n".join(data_lines))
                    data_lines.clear()
                continue
            if line.startswith(":"):
                continue
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
            elif line.startswith(("event:", "id:", "retry:")):
                continue
            else:
                raise AssistantClientError("malformed SSE stream", "ORCHESTRATOR_PROTOCOL")
    if buffer or data_lines:
        if buffer:
            try:
                line = buffer.rstrip(b"\r").decode("utf-8", errors="strict")
            except UnicodeDecodeError as exc:
                raise AssistantClientError(
                    "incomplete SSE stream", "ORCHESTRATOR_PROTOCOL"
                ) from exc
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
            elif line and not line.startswith(":"):
                raise AssistantClientError("incomplete SSE stream", "ORCHESTRATOR_PROTOCOL")
        if data_lines:
            yield _decode_event("\n".join(data_lines))


def _decode_event(payload: str) -> dict[str, Any]:
    try:
        value = json.loads(payload)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise AssistantClientError("malformed SSE event", "ORCHESTRATOR_PROTOCOL") from exc
    if not isinstance(value, dict):
        raise AssistantClientError("malformed SSE event", "ORCHESTRATOR_PROTOCOL")
    return value


class OrchestratorAssistantClient:
    def __init__(
        self,
        client: httpx.AsyncClient,
        stream_path: str = "/v1/questions/stream",
        max_event_bytes: int = MAX_SSE_EVENT_BYTES,
    ) -> None:
        if max_event_bytes <= 0:
            raise ValueError("max_event_bytes must be positive")
        self._client = client
        self._stream_path = stream_path
        self._max_event_bytes = max_event_bytes

    async def ask(
        self,
        text: str,
        conversation: object,
        *,
        credential: str,
        idempotency_key: str,
        on_stage: AssistantStageCallback | None = None,
    ) -> AssistantReply:
        payload: dict[str, object] = {"question": text, "channel": "VOICE"}
        if conversation is not None:
            payload["conversation"] = conversation
        try:
            async with self._client.stream(
                "POST",
                self._stream_path,
                headers={
                    "Authorization": f"Bearer {credential}",
                    "Idempotency-Key": idempotency_key,
                },
                json=payload,
            ) as response:
                if response.status_code >= 400:
                    message, code = _status_message(response.status_code)
                    raise AssistantClientError(message, code)
                content_type = response.headers.get("content-type", "")
                if "text/event-stream" not in content_type:
                    raise AssistantClientError(
                        "invalid orchestrator response", "ORCHESTRATOR_PROTOCOL"
                    )
                final: dict[str, Any] | None = None
                async for item in _sse_events(response, self._max_event_bytes):
                    item_type = item.get("type")
                    if item_type == "stage":
                        name = item.get("name")
                        if not isinstance(name, str) or not name:
                            raise AssistantClientError(
                                "malformed orchestrator stage", "ORCHESTRATOR_PROTOCOL"
                            )
                        if on_stage is not None:
                            detail = item.get("detail")
                            await on_stage(name, detail if isinstance(detail, str) else None)
                    elif item_type == "answer":
                        if final is not None:
                            raise AssistantClientError(
                                "duplicate orchestrator answer", "ORCHESTRATOR_PROTOCOL"
                            )
                        final = item
                    else:
                        raise AssistantClientError(
                            "unknown orchestrator event", "ORCHESTRATOR_PROTOCOL"
                        )
                if final is None:
                    raise AssistantClientError(
                        "incomplete orchestrator stream", "ORCHESTRATOR_PROTOCOL"
                    )
                return _reply_from(final)
        except httpx.TimeoutException as exc:
            raise AssistantClientError(
                "orchestrator request timed out", "ORCHESTRATOR_TIMEOUT"
            ) from exc
        except httpx.HTTPError as exc:
            raise AssistantClientError("orchestrator request failed") from exc


def _status_message(status_code: int) -> tuple[str, str]:
    if status_code == 401 or status_code == 403:
        return "orchestrator authentication failed", "ORCHESTRATOR_AUTH"
    if status_code >= 500:
        return "orchestrator unavailable", "ORCHESTRATOR_UNAVAILABLE"
    return "orchestrator rejected the request", "ORCHESTRATOR_REJECTED"


def _reply_from(payload: dict[str, Any]) -> AssistantReply:
    text = payload.get("text")
    if not isinstance(text, str):
        raise AssistantClientError("malformed orchestrator answer", "ORCHESTRATOR_PROTOCOL")
    conversation = payload.get("conversation")
    if conversation is not None and not isinstance(conversation, dict):
        raise AssistantClientError("malformed orchestrator conversation", "ORCHESTRATOR_PROTOCOL")
    payment_required = (
        isinstance(conversation, dict) and conversation.get("workflow") == "PAYMENT_CONFIRMATION"
    ) or payload.get("outcome") == "AWAITING_CONFIRMATION"
    answer = {key: value for key, value in payload.items() if key != "type"}
    # No error for a missing or non-string value: an older orchestrator answers with no `spoken`
    # key at all, and that is not malformed, only silent about voice.
    spoken = payload.get("spoken")
    spoken = spoken if isinstance(spoken, str) else None
    return AssistantReply(text, conversation, answer, payment_required, spoken=spoken)
