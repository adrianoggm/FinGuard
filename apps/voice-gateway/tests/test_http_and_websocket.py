import asyncio
import json
from collections.abc import AsyncIterator, Callable
from datetime import datetime
from typing import cast

import pytest
from fastapi import WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from finguard_voice_gateway.adapters.outbound.fake_assistant import FakeAssistantClient
from finguard_voice_gateway.app import (
    VoiceSession,
    _process_turn,
    app,
    handle_control,
    process_turn,
    receive_audio,
    services,
    sessions,
)
from finguard_voice_gateway.domain.state import SessionProtocolError, SessionState
from finguard_voice_gateway.ports.speech_to_text import SpeechToTextTimeoutError
from finguard_voice_gateway.protocol import parse_event


def _control(event_type: str, session_id: str, sequence: int, turn_id: str | None = None) -> str:
    return json.dumps(
        {
            "type": event_type,
            "sessionId": session_id,
            "turnId": turn_id,
            "sequence": sequence,
            "at": datetime.now().isoformat(),
        }
    )


def test_health_version_and_ticket_is_one_use() -> None:
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/version").json()["service"] == "voice-gateway"
        response = client.post("/v1/voice/sessions", headers={"Authorization": "Bearer dev-secret"})
        body = response.json()
        assert response.status_code == 200
        assert body["sessionId"] not in sessions
        with client.websocket_connect(
            body["websocketUrl"],
            subprotocols=["finguard.voice.v1", body["ticket"]],
        ) as socket:
            socket.send_text(_control("session.configure", body["sessionId"], 0))
            assert socket.receive_json()["type"] == "session.ready"
        try:
            with client.websocket_connect(
                body["websocketUrl"],
                subprotocols=["finguard.voice.v1", body["ticket"]],
            ):
                raise AssertionError("a consumed ticket must not connect")
        except WebSocketDisconnect as exc:
            assert exc.code == 4401
    sessions.clear()


def test_ticket_and_protocol_are_required() -> None:
    with TestClient(app) as client:
        body = client.post(
            "/v1/voice/sessions", headers={"Authorization": "Bearer dev-secret"}
        ).json()
        with pytest.raises(WebSocketDisconnect) as missing_protocol:
            with client.websocket_connect(body["websocketUrl"], subprotocols=[body["ticket"]]):
                pass
        assert missing_protocol.value.code == 4406
        with pytest.raises(WebSocketDisconnect) as missing_ticket:
            with client.websocket_connect(body["websocketUrl"], subprotocols=["finguard.voice.v1"]):
                pass
        assert missing_ticket.value.code == 4401
    sessions.clear()


def test_silent_socket_expires_without_waiting_for_a_long_idle_period(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_idle = services.settings.session_idle_seconds
    monkeypatch.setattr(services.settings, "session_idle_seconds", 0)
    try:
        with TestClient(app) as client:
            body = client.post(
                "/v1/voice/sessions", headers={"Authorization": "Bearer dev-secret"}
            ).json()
            with client.websocket_connect(
                body["websocketUrl"], subprotocols=["finguard.voice.v1", body["ticket"]]
            ) as socket:
                error = socket.receive_json()
                assert error["type"] == "error"
                assert error["code"] == "SESSION_EXPIRED"
                assert error["fatal"] is True
                with pytest.raises(WebSocketDisconnect) as closed:
                    socket.receive_json()
                assert closed.value.code == 4400
            assert body["sessionId"] not in sessions
    finally:
        monkeypatch.setattr(services.settings, "session_idle_seconds", original_idle)
        sessions.clear()


def test_fake_text_turn_and_spoken_payment_confirmation_refusal() -> None:
    with TestClient(app) as client:
        body = client.post(
            "/v1/voice/sessions", headers={"Authorization": "Bearer dev-secret"}
        ).json()
        with client.websocket_connect(
            body["websocketUrl"], subprotocols=["finguard.voice.v1", body["ticket"]]
        ) as socket:
            session_id = body["sessionId"]
            socket.send_text(_control("session.configure", session_id, 0))
            assert socket.receive_json()["type"] == "session.ready"
            socket.send_text(_control("input.start", session_id, 1, "turn-1"))
            socket.send_bytes(b"si")
            socket.send_text(_control("input.commit", session_id, 2, "turn-1"))
            received: list[dict[str, object]] = []
            while True:
                item = socket.receive()
                if item.get("text"):
                    payload = json.loads(item["text"])
                    received.append(payload)
                    if payload["type"] == "turn.completed":
                        break
            response = next(item for item in received if item["type"] == "assistant.text.final")
            response_text = response["text"]
            assert isinstance(response_text, str)
            assert "He entendido" in response_text
            assert response["paymentConfirmationRequired"] is False
    sessions.clear()


def test_payment_policy_blocks_next_spoken_turn_without_calling_assistant() -> None:
    assert isinstance(services.assistant, FakeAssistantClient)
    with TestClient(app) as client:
        before = services.assistant.call_count
        body = client.post(
            "/v1/voice/sessions", headers={"Authorization": "Bearer dev-secret"}
        ).json()
        with client.websocket_connect(
            body["websocketUrl"], subprotocols=["finguard.voice.v1", body["ticket"]]
        ) as socket:
            session_id = body["sessionId"]
            socket.send_text(_control("session.configure", session_id, 0))
            socket.receive_json()
            for sequence, turn_id, audio in (
                (1, "prepare", b"quiero preparar un pago "),
                (3, "confirm", b"si"),
            ):
                socket.send_text(_control("input.start", session_id, sequence, turn_id))
                socket.send_bytes(audio)
                socket.send_text(_control("input.commit", session_id, sequence + 1, turn_id))
                events: list[dict[str, object]] = []
                while True:
                    item = socket.receive()
                    if item.get("text"):
                        payload = json.loads(item["text"])
                        events.append(payload)
                        if payload["type"] == "turn.completed":
                            break
            assert services.assistant.call_count == before + 1
            assert services.assistant.received_credential is True
            final = [item for item in events if item["type"] == "assistant.text.final"][0]
            final_text = final["text"]
            assert isinstance(final_text, str)
            assert "No puedo confirmar" in final_text
            assert final["paymentConfirmationRequired"] is True
    sessions.clear()


class _Socket:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    async def send_json(self, value: dict[str, object]) -> None:
        self.events.append(value)

    async def close(self, code: int = 1000) -> None:
        return None

    async def send_bytes(self, data: bytes) -> None:
        return None


@pytest.mark.asyncio
@pytest.mark.parametrize("active_state", [SessionState.THINKING, SessionState.SPEAKING])
async def test_barge_in_reports_old_turn_before_new_turn(active_state: SessionState) -> None:
    session = VoiceSession("session", "credential", services)
    session.machine.configure()
    session.machine.start_input("old-turn")
    session.machine.commit()
    session.machine.thinking()
    if active_state == SessionState.SPEAKING:
        session.machine.speaking()
    socket = _Socket()
    await handle_control(
        socket,
        session,
        parse_event(json.loads(_control("input.start", "session", 0, "new-turn"))),
    )
    assert socket.events[0]["type"] == "response.cancelled"
    assert socket.events[0]["turnId"] == "old-turn"
    assert session.machine.state.value == SessionState.LISTENING.value
    assert session.machine.turn_id == "new-turn"


@pytest.mark.asyncio
async def test_barge_in_ack_is_one_shot_and_has_no_waiting_window() -> None:
    ticks = iter((0.0, 0.010, 0.020, 0.030, 0.040))
    session = VoiceSession("session", "credential", services, monotonic=lambda: next(ticks))
    session.machine.configure()
    session.machine.start_input("old-turn")
    session.machine.commit()
    session.machine.thinking()
    socket = _Socket()
    await handle_control(
        socket,
        session,
        parse_event(json.loads(_control("input.start", "session", 0, "new-turn"))),
    )
    await handle_control(
        socket,
        session,
        parse_event(json.loads(_control("response.cancel", "session", 1))),
    )
    assert session.last_cancel_ack_ms == pytest.approx(10.0)
    assert session.last_cancel_ack_ms is not None
    assert session.last_cancel_ack_ms < 250
    assert [event["type"] for event in socket.events].count("response.cancelled") == 1


@pytest.mark.asyncio
async def test_barge_in_does_not_wait_for_a_provider_that_suppresses_cancellation() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    async def stubborn_provider() -> None:
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release.wait()

    session = VoiceSession("session", "credential", services)
    session.machine.configure()
    session.machine.start_input("old-turn")
    session.machine.commit()
    session.machine.thinking()
    session.task = asyncio.create_task(stubborn_provider())
    await entered.wait()
    socket = _Socket()

    await asyncio.wait_for(
        handle_control(
            socket,
            session,
            parse_event(json.loads(_control("input.start", "session", 0, "new-turn"))),
        ),
        timeout=0.25,
    )

    assert session.machine.state == SessionState.LISTENING
    assert session.machine.turn_id == "new-turn"
    assert socket.events[0]["type"] == "response.cancelled"
    assert len(session.retired_tasks) == 1
    release.set()
    await asyncio.gather(*session.retired_tasks)
    await asyncio.sleep(0)
    assert session.retired_tasks == set()


@pytest.mark.asyncio
async def test_cancel_returns_none_when_state_machine_does_not_cancel() -> None:
    session = VoiceSession("session", "credential", services)
    session.machine.configure()
    session.machine.start_input("listening-turn")
    assert await session.cancel() is None
    assert session.machine.state.value == SessionState.LISTENING.value


@pytest.mark.asyncio
async def test_session_and_turn_mismatch_are_rejected_without_mutating_state() -> None:
    session = VoiceSession("session", "credential", services)
    session.machine.configure()
    socket = _Socket()
    with pytest.raises(SessionProtocolError) as session_error:
        await handle_control(
            socket,
            session,
            parse_event(json.loads(_control("input.start", "other", 1, "turn"))),
        )
    assert session_error.value.code == "SESSION_MISMATCH"
    assert session.machine.state == SessionState.READY
    session.machine.start_input("active")
    with pytest.raises(SessionProtocolError) as turn_error:
        await handle_control(
            socket,
            session,
            parse_event(json.loads(_control("input.commit", "session", 2, "other"))),
        )
    assert turn_error.value.code == "TURN_MISMATCH"
    assert session.machine.state.value == SessionState.LISTENING.value


@pytest.mark.asyncio
async def test_audio_frame_rules_are_checked_without_transport_races() -> None:
    session = VoiceSession("session", "credential", services)
    socket = _Socket()
    session.machine.configure()
    with pytest.raises(SessionProtocolError) as outside_listening:
        await receive_audio(cast(WebSocket, socket), session, b"ok")
    assert outside_listening.value.code == "INVALID_STATE"
    assert session.audio == bytearray()

    session.machine.start_input("turn")
    with pytest.raises(SessionProtocolError) as odd_frame:
        await receive_audio(cast(WebSocket, socket), session, b"x")
    assert odd_frame.value.code == "INVALID_AUDIO"
    assert session.audio == bytearray()
    await receive_audio(cast(WebSocket, socket), session, b"ok")
    session.machine.commit()
    with pytest.raises(SessionProtocolError) as after_commit:
        await receive_audio(cast(WebSocket, socket), session, b"ok")
    assert after_commit.value.code == "INVALID_STATE"
    assert session.audio == bytearray(b"ok")


def test_ping_and_repeated_configure_are_deterministic() -> None:
    with TestClient(app) as client:
        body = client.post("/v1/voice/sessions", headers={"Authorization": "Bearer x"}).json()
        with client.websocket_connect(
            body["websocketUrl"], subprotocols=["finguard.voice.v1", body["ticket"]]
        ) as socket:
            sid = body["sessionId"]
            socket.send_text(_control("session.configure", sid, 0))
            assert socket.receive_json()["type"] == "session.ready"
            socket.send_text(_control("session.configure", sid, 1))
            assert socket.receive_json()["code"] == "INVALID_STATE"
            socket.send_text(_control("ping", sid, 2))
            assert socket.receive_json()["type"] == "pong"
    sessions.clear()


@pytest.mark.asyncio
async def test_repeated_commit_is_invalid_state_before_processing_can_run() -> None:
    session = VoiceSession("session", "credential", services)
    session.machine.configure()
    session.machine.start_input("turn")
    socket = _Socket()
    await handle_control(
        socket, session, parse_event(json.loads(_control("input.commit", "session", 0, "turn")))
    )
    with pytest.raises(SessionProtocolError) as repeated:
        await handle_control(
            socket,
            session,
            parse_event(json.loads(_control("input.commit", "session", 1, "turn"))),
        )
    assert repeated.value.code == "INVALID_STATE"
    await session.cancel()


def test_close_removes_session_and_cancel_without_active_response_is_silent() -> None:
    with TestClient(app) as client:
        body = client.post("/v1/voice/sessions", headers={"Authorization": "Bearer x"}).json()
        with client.websocket_connect(
            body["websocketUrl"], subprotocols=["finguard.voice.v1", body["ticket"]]
        ) as socket:
            sid = body["sessionId"]
            socket.send_text(_control("session.configure", sid, 0))
            socket.receive_json()
            socket.send_text(_control("response.cancel", sid, 1))
            socket.send_text(_control("ping", sid, 2))
            assert socket.receive_json()["type"] == "pong"
            socket.send_text(_control("session.close", sid, 3))
        assert sid not in sessions


def test_disconnect_removes_session_without_explicit_close() -> None:
    with TestClient(app) as client:
        body = client.post("/v1/voice/sessions", headers={"Authorization": "Bearer x"}).json()
        with client.websocket_connect(
            body["websocketUrl"], subprotocols=["finguard.voice.v1", body["ticket"]]
        ) as socket:
            sid = body["sessionId"]
            socket.send_text(_control("session.configure", sid, 0))
            socket.receive_json()
            assert sid in sessions
        assert sid not in sessions


class _BlockingSTT:
    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def transcribe(self, audio: bytes, *, cancelled: Callable[[], bool]) -> str:
        self.entered.set()
        await self.release.wait()
        return "texto antiguo"


class _RecordingTransport(_Socket):
    def __init__(self) -> None:
        super().__init__()
        self.audio: list[bytes] = []

    async def send_bytes(self, data: bytes) -> None:
        self.audio.append(data)


@pytest.mark.asyncio
async def test_fencing_discards_late_turn_events_and_audio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    blocking_stt = _BlockingSTT()
    monkeypatch.setattr(services, "stt", blocking_stt)
    session = VoiceSession("session", "credential", services)
    session.machine.configure()
    session.machine.start_input("old")
    session.audio.extend(b"ok")
    session.machine.commit()
    transport = _RecordingTransport()
    task = asyncio.create_task(_process_turn(transport, session, "old"))
    await blocking_stt.entered.wait()
    events_at_cancel = len(transport.events)
    session.cancel_generation += 1
    assert session.machine.cancel() == "old"
    blocking_stt.release.set()
    await task
    assert len(transport.events) == events_at_cancel
    assert transport.audio == []


class _FailingSTT:
    async def transcribe(self, audio: bytes, *, cancelled: Callable[[], bool]) -> str:
        raise SpeechToTextTimeoutError("secret audio/transcript must not leak")


class _FailingTTS:
    def synthesize(self, text: str, *, cancelled: Callable[[], bool]) -> AsyncIterator[bytes]:
        async def stream() -> AsyncIterator[bytes]:
            raise RuntimeError("tts down")
            yield b"unreachable"

        return stream()


class _CancellationResistantTTS:
    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    def synthesize(self, text: str, *, cancelled: Callable[[], bool]) -> AsyncIterator[bytes]:
        async def stream() -> AsyncIterator[bytes]:
            self.entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await self.release.wait()
            yield b"stale audio"

        return stream()


@pytest.mark.asyncio
async def test_cancelled_speaking_turn_does_not_commit_conversation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resistant_tts = _CancellationResistantTTS()
    monkeypatch.setattr(services, "tts", resistant_tts)
    session = VoiceSession("session", "credential", services)
    original_conversation = {"last": "previous"}
    session.conversation = original_conversation
    session.machine.configure()
    session.machine.start_input("old-turn")
    session.audio.extend(b"old text")
    session.machine.commit()
    transport = _RecordingTransport()
    session.task = asyncio.create_task(process_turn(transport, session, "old-turn"))
    await resistant_tts.entered.wait()

    await handle_control(
        transport,
        session,
        parse_event(json.loads(_control("input.start", "session", 0, "new-turn"))),
    )
    resistant_tts.release.set()
    await asyncio.gather(*session.retired_tasks)

    assert session.conversation is original_conversation
    assert session.machine.turn_id == "new-turn"
    assert transport.audio == []


@pytest.mark.parametrize("provider", ["stt", "tts"])
def test_provider_errors_are_typed_and_close_socket(
    provider: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = getattr(services, provider)
    monkeypatch.setattr(services, provider, _FailingSTT() if provider == "stt" else _FailingTTS())
    try:
        with TestClient(app) as client:
            body = client.post(
                "/v1/voice/sessions", headers={"Authorization": "Bearer dev-secret"}
            ).json()
            with client.websocket_connect(
                body["websocketUrl"], subprotocols=["finguard.voice.v1", body["ticket"]]
            ) as socket:
                session_id = body["sessionId"]
                socket.send_text(_control("session.configure", session_id, 0))
                socket.receive_json()
                socket.send_text(_control("input.start", session_id, 1, "turn"))
                socket.send_bytes(b"ok")
                socket.send_text(_control("input.commit", session_id, 2, "turn"))
                error = None
                while error is None:
                    item = socket.receive_json()
                    if item["type"] == "error":
                        error = item
                assert error["code"] == ("STT_TIMEOUT" if provider == "stt" else "PROVIDER_ERROR")
                assert error["fatal"] is True
                if provider == "stt":
                    assert error["message"] == "speech provider failed"
                with pytest.raises(WebSocketDisconnect) as closed:
                    socket.receive_json()
                assert closed.value.code == 1011
    finally:
        monkeypatch.setattr(services, provider, original)
        sessions.clear()


def test_duplicate_sequence_is_fatal_and_closes_after_error() -> None:
    with TestClient(app) as client:
        body = client.post(
            "/v1/voice/sessions", headers={"Authorization": "Bearer dev-secret"}
        ).json()
        with client.websocket_connect(
            body["websocketUrl"], subprotocols=["finguard.voice.v1", body["ticket"]]
        ) as socket:
            session_id = body["sessionId"]
            socket.send_text(_control("session.configure", session_id, 0))
            socket.receive_json()
            socket.send_text(_control("ping", session_id, 0))
            assert socket.receive_json()["fatal"] is True
            with pytest.raises(WebSocketDisconnect) as closed:
                socket.receive_json()
            assert closed.value.code == 4400
    sessions.clear()


def test_oversized_frame_is_rejected() -> None:
    with TestClient(app) as client:
        body = client.post(
            "/v1/voice/sessions", headers={"Authorization": "Bearer dev-secret"}
        ).json()
        with client.websocket_connect(
            body["websocketUrl"], subprotocols=["finguard.voice.v1", body["ticket"]]
        ) as socket:
            session_id = body["sessionId"]
            socket.send_text(_control("session.configure", session_id, 0))
            socket.receive_json()
            socket.send_text(_control("input.start", session_id, 1, "turn-1"))
            socket.send_bytes(b"x" * (64 * 1024 + 1))
            assert socket.receive_json()["code"] == "FRAME_TOO_LARGE"
    sessions.clear()
