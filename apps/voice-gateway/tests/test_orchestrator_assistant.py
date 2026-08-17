import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest

from finguard_voice_gateway.adapters.outbound.fake_stt import FakeSpeechToText
from finguard_voice_gateway.adapters.outbound.fake_tts import FakeTextToSpeech
from finguard_voice_gateway.adapters.outbound.orchestrator_assistant import (
    OrchestratorAssistantClient,
)
from finguard_voice_gateway.app import VoiceSession, _process_turn, process_turn
from finguard_voice_gateway.bootstrap import Services, build_services, ensure_services_open
from finguard_voice_gateway.config import Settings
from finguard_voice_gateway.domain.state import SessionState
from finguard_voice_gateway.ports.assistant_client import AssistantClientError


class ChunkStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            yield chunk


class CaptureTransport(httpx.AsyncBaseTransport):
    def __init__(self, response: httpx.Response | None = None, chunks: list[bytes] | None = None):
        self.request: httpx.Request | None = None
        self.response = response
        self.chunks = chunks or []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.request = request
        if self.response is not None:
            return self.response
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=ChunkStream(self.chunks),
            request=request,
        )


def sse(*events: dict[str, Any]) -> list[bytes]:
    raw = b"".join(
        f": keepalive\n\ndata: {json.dumps(event)}\n\n".encode() for event in events
    )
    return [raw[index : index + 3] for index in range(0, len(raw), 3)]


@pytest.mark.asyncio
async def test_adapter_sends_real_payload_auth_and_stable_key() -> None:
    transport = CaptureTransport(
        chunks=sse(
            {"type": "stage", "name": "reading", "detail": None},
            {
                "type": "answer",
                "text": "saldo: 20 €",
                "conversation": {"workflow": None, "last": "saldo"},
                "outcome": "ANSWERED",
            },
        )
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://orchestrator") as http:
        stages: list[tuple[str, str | None]] = []
        reply = await OrchestratorAssistantClient(http).ask(
            "¿cuánto tengo?",
            {"workflow": None},
            credential="secret",
            idempotency_key="voice:s:t",
            on_stage=lambda name, detail: _record(stages, name, detail),
        )
    assert transport.request is not None
    assert transport.request.headers["authorization"] == "Bearer secret"
    assert transport.request.headers["idempotency-key"] == "voice:s:t"
    assert json.loads(transport.request.content) == {
        "question": "¿cuánto tengo?",
        "conversation": {"workflow": None},
    }
    assert stages == [("reading", None)]
    assert reply.text == "saldo: 20 €"
    assert reply.conversation == {"workflow": None, "last": "saldo"}
    assert not reply.payment_confirmation_required


async def _record(
    target: list[tuple[str, str | None]], name: str, detail: str | None
) -> None:
    target.append((name, detail))


@pytest.mark.asyncio
async def test_adapter_maps_payment_confirmation_from_real_conversation() -> None:
    transport = CaptureTransport(
        chunks=sse(
            {
                "type": "answer",
                "text": "Revisa el pago",
                "outcome": "AWAITING_CONFIRMATION",
                "conversation": {"workflow": "PAYMENT_CONFIRMATION"},
            }
        )
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://orchestrator") as http:
        reply = await OrchestratorAssistantClient(http).ask(
            "prepara un pago",
            None,
            credential="secret",
            idempotency_key="key",
        )
    assert json.loads(transport.request.content) == {"question": "prepara un pago"}  # type: ignore[union-attr]
    assert reply.payment_confirmation_required


@pytest.mark.asyncio
async def test_adapter_accepts_multiline_data_split_across_chunks() -> None:
    raw = (
        b'data: {\ndata: "type": "answer",\ndata: "text": "ok",\n'
        b'data: "conversation": null\ndata: }\n\n'
    )
    transport = CaptureTransport(chunks=[raw[:5], raw[5:13], raw[13:]])
    async with httpx.AsyncClient(transport=transport, base_url="http://orchestrator") as http:
        reply = await OrchestratorAssistantClient(http).ask(
            "hola", None, credential="secret", idempotency_key="key"
        )
    assert reply.text == "ok"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("chunks", "expected_code"),
    [
        ([b"data: {\"type\": \"mystery\"}\n\n"], "ORCHESTRATOR_PROTOCOL"),
        (
            [
                b'data: {"type": "answer", "text": "a"}\n\n'
                b'data: {"type": "answer", "text": "b"}\n\n'
            ],
            "ORCHESTRATOR_PROTOCOL",
        ),
        ([b"data: {\"type\": \"stage\", \"name\": 3}\n\n"], "ORCHESTRATOR_PROTOCOL"),
        ([b"data: {\"type\": \"stage\", \"name\": \"reading\"}\n\n"], "ORCHESTRATOR_PROTOCOL"),
    ],
)
async def test_adapter_rejects_protocol_variants_with_exact_code(
    chunks: list[bytes], expected_code: str
) -> None:
    transport = CaptureTransport(chunks=chunks)
    async with httpx.AsyncClient(transport=transport, base_url="http://orchestrator") as http:
        with pytest.raises(AssistantClientError) as error:
            await OrchestratorAssistantClient(http).ask(
                "hola", None, credential="secret", idempotency_key="key"
            )
    assert error.value.code == expected_code


@pytest.mark.asyncio
async def test_adapter_rejects_non_sse_content_type() -> None:
    transport = CaptureTransport(
        httpx.Response(200, headers={"content-type": "application/json"})
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://orchestrator") as http:
        with pytest.raises(AssistantClientError) as error:
            await OrchestratorAssistantClient(http).ask(
                "hola", None, credential="secret", idempotency_key="key"
            )
    assert error.value.code == "ORCHESTRATOR_PROTOCOL"


@pytest.mark.asyncio
async def test_adapter_rejects_an_sse_event_over_the_memory_limit() -> None:
    transport = CaptureTransport(chunks=[b"data: " + b"x" * 64])
    async with httpx.AsyncClient(transport=transport, base_url="http://orchestrator") as http:
        with pytest.raises(AssistantClientError) as error:
            await OrchestratorAssistantClient(http, max_event_bytes=16).ask(
                "hola", None, credential="secret", idempotency_key="key"
            )
    assert error.value.code == "ORCHESTRATOR_PROTOCOL"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 500, 503, 422])
async def test_adapter_sanitizes_remote_http_errors(status: int) -> None:
    transport = CaptureTransport(httpx.Response(status))
    async with httpx.AsyncClient(transport=transport, base_url="http://orchestrator") as http:
        with pytest.raises(AssistantClientError) as error:
            await OrchestratorAssistantClient(http).ask(
                "hola", None, credential="secret", idempotency_key="key"
            )
    assert error.value.code == (
        "ORCHESTRATOR_AUTH"
        if status in {401, 403}
        else "ORCHESTRATOR_UNAVAILABLE"
        if status >= 500
        else "ORCHESTRATOR_REJECTED"
    )
    assert "private" not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "chunks",
    [
        [b"data: {not-json}\n\n"],
        [b"data: {\"type\": \"stage\", \"name\": \"reading\"}\n\n"],
        [b"data: {\"type\": \"answer\", \"text\": 3}\n\n"],
    ],
)
async def test_adapter_rejects_malformed_or_incomplete_sse(chunks: list[bytes]) -> None:
    transport = CaptureTransport(chunks=chunks)
    async with httpx.AsyncClient(transport=transport, base_url="http://orchestrator") as http:
        with pytest.raises(AssistantClientError):
            await OrchestratorAssistantClient(http).ask(
                "hola", None, credential="secret", idempotency_key="key"
            )


@pytest.mark.asyncio
async def test_adapter_converts_timeout_and_cancellation_without_retry() -> None:
    async def timeout(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=_)

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(timeout), base_url="http://x"
    ) as http:
        with pytest.raises(AssistantClientError, match="timed out"):
            await OrchestratorAssistantClient(http).ask(
                "hola", None, credential="secret", idempotency_key="key"
            )

    gate = asyncio.Event()

    class BlockingStream(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            await gate.wait()
            yield b""

    async def blocking(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=BlockingStream()
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(blocking), base_url="http://x"
    ) as http:
        task = asyncio.create_task(
            OrchestratorAssistantClient(http).ask(
                "hola", None, credential="secret", idempotency_key="key"
            )
        )
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


class ErrorAssistant:
    async def ask(self, *args: Any, **kwargs: Any) -> Any:
        raise AssistantClientError("sensitive remote body", "ORCHESTRATOR_AUTH")


@pytest.mark.asyncio
async def test_process_turn_sends_sanitized_assistant_error_to_websocket() -> None:
    services = Services(Settings(), FakeSpeechToText(), FakeTextToSpeech(), ErrorAssistant())
    session = VoiceSession("session", "credential-do-not-leak", services)
    session.machine.state = SessionState.CONNECTING
    session.machine.configure()
    session.machine.start_input("turn")
    session.machine.receive_audio(2, max_utterance_bytes=100)
    session.audio.extend(b"hi")
    session.machine.commit()
    socket = Transport()
    await process_turn(socket, session, "turn")
    error = next(item for item in socket.json if item["type"] == "error")
    assert error["code"] == "ORCHESTRATOR_AUTH"
    assert "credential-do-not-leak" not in str(error)
    assert "sensitive remote body" not in str(error)


@pytest.mark.asyncio
async def test_http_client_cannot_be_silently_reused_after_close() -> None:
    services = build_services(Settings(assistant_mode="orchestrator_http"))
    assert services.http_client is not None
    ensure_services_open(services)
    await services.http_client.aclose()
    with pytest.raises(RuntimeError, match="already closed"):
        ensure_services_open(services)


class Transport:
    def __init__(self) -> None:
        self.json: list[dict[str, Any]] = []
        self.audio: list[bytes] = []

    async def send_json(self, value: dict[str, Any]) -> None:
        self.json.append(value)

    async def send_bytes(self, value: bytes) -> None:
        self.audio.append(value)

    async def close(self, code: int = 1000) -> None:
        del code


@pytest.mark.asyncio
async def test_vertical_gateway_session_to_http_adapter() -> None:
    transport = CaptureTransport(
        chunks=sse(
            {"type": "stage", "name": "reading", "detail": None},
            {
                "type": "answer",
                "text": "Respuesta real",
                "conversation": {"workflow": None},
                "outcome": "ANSWERED",
            },
        )
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://orchestrator") as http:
        services = Services(
            Settings(),
            FakeSpeechToText(),
            FakeTextToSpeech(),
            OrchestratorAssistantClient(http),
        )
        session = VoiceSession("session", "credential", services)
        session.machine.state = SessionState.CONNECTING
        session.machine.configure()
        session.machine.start_input("turn")
        session.machine.receive_audio(2, max_utterance_bytes=100)
        session.audio.extend(b"hi")
        session.machine.commit()
        socket = Transport()
        await _process_turn(socket, session, "turn")
    assert any(item["type"] == "turn.completed" for item in socket.json)
    assert any(
        item["type"] == "assistant.stage" and item["stage"] == "reading"
        for item in socket.json
    )
    assert transport.request is not None
    assert transport.request.headers["authorization"] == "Bearer credential"
