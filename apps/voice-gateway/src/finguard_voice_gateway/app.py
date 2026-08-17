import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, Protocol

from fastapi import FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from finguard_voice_gateway import __version__
from finguard_voice_gateway.bootstrap import Services, build_services, ensure_services_open
from finguard_voice_gateway.domain.state import (
    SessionProtocolError,
    SessionState,
    SessionStateMachine,
)
from finguard_voice_gateway.ports.assistant_client import AssistantClientError
from finguard_voice_gateway.ports.speech_to_text import SpeechToTextError, SpeechToTextLifecycle
from finguard_voice_gateway.ports.text_to_speech import TextToSpeechError, TextToSpeechLifecycle
from finguard_voice_gateway.protocol import (
    CancelEvent,
    CloseEvent,
    CommitEvent,
    ConfigureEvent,
    ControlEvent,
    PingEvent,
    StartEvent,
    parse_event,
)
from finguard_voice_gateway.tickets import TicketStore

services = build_services()
tickets = TicketStore(services.settings.ticket_ttl_seconds)
sessions: dict[str, "VoiceSession"] = {}


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    ensure_services_open(services)
    if services.settings.stt_mode == "local" and services.settings.stt_warmup:
        if isinstance(services.stt, SpeechToTextLifecycle):
            await services.stt.warmup()
    if services.settings.tts_mode == "local" and services.settings.tts_warmup:
        if isinstance(services.tts, TextToSpeechLifecycle):
            await services.tts.warmup()
    cleanup = asyncio.create_task(tickets.cleanup_loop(1.0))
    try:
        yield
    finally:
        cleanup.cancel()
        try:
            await cleanup
        except asyncio.CancelledError:
            pass
        if services.http_client is not None:
            await services.http_client.aclose()
        if isinstance(services.stt, SpeechToTextLifecycle):
            await services.stt.close()
        if isinstance(services.tts, TextToSpeechLifecycle):
            await services.tts.close()


app = FastAPI(title="FinGuard Voice Gateway", version=__version__, lifespan=lifespan)


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def event(
    event_type: str, session_id: str, *, turn_id: str | None = None, sequence: int = 0, **data: Any
) -> dict[str, Any]:
    return {
        "type": event_type,
        "sessionId": session_id,
        "turnId": turn_id,
        "sequence": sequence,
        "at": now_iso(),
        **data,
    }


class VoiceSession:
    def __init__(
        self,
        session_id: str,
        credential: str,
        service_set: Services,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.session_id = session_id
        self.credential = credential
        self.services = service_set
        self.monotonic = monotonic
        self.machine = SessionStateMachine()
        self.conversation: object = None
        self.audio = bytearray()
        self.payment_confirmation_required = False
        self.task: asyncio.Task[None] | None = None
        self.retired_tasks: set[asyncio.Task[None]] = set()
        self.cancel_generation = 0
        self.created = self.monotonic()
        self.last_activity = self.created
        self.server_sequence = 0
        self.last_cancel_ack_ms: float | None = None

    def next_sequence(self) -> int:
        value = self.server_sequence
        self.server_sequence += 1
        return value

    def cancelled(self, generation: int, turn_id: str) -> bool:
        return generation != self.cancel_generation or self.machine.turn_id != turn_id

    async def cancel(self) -> str | None:
        # Fence first: a non-abortable provider may return while its task is being
        # cancelled, but it must observe the new generation/state immediately.
        started = self.monotonic()
        self.cancel_generation += 1
        old_turn_id = self.machine.cancel()
        if self.task is not None and not self.task.done():
            task = self.task
            task.cancel()
            # Cancellation is a fence, not a promise that an external provider
            # has stopped. Keep a strong reference and consume its result without
            # delaying the acknowledgement or the next input.start.
            self.retired_tasks.add(task)
            task.add_done_callback(self._reap_retired_task)
        self.task = None
        self.last_cancel_ack_ms = (self.monotonic() - started) * 1000
        return old_turn_id

    def _reap_retired_task(self, task: asyncio.Task[None]) -> None:
        self.retired_tasks.discard(task)
        if not task.cancelled():
            task.exception()

    def expired(self) -> bool:
        return self.remaining_seconds() <= 0

    def remaining_seconds(self) -> float:
        settings = self.services.settings
        now = self.monotonic()
        return min(
            settings.session_absolute_seconds - (now - self.created),
            settings.session_idle_seconds - (now - self.last_activity),
        )

    def touch(self) -> None:
        self.last_activity = self.monotonic()


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/version")
async def version() -> dict[str, str]:
    return {"service": "voice-gateway", "version": __version__}


@app.post("/v1/voice/sessions")
async def create_session(authorization: str | None = Header(default=None)) -> JSONResponse:
    if not authorization or not authorization.startswith("Bearer ") or not authorization[7:]:
        raise HTTPException(status_code=401, detail="Bearer credential required")
    session_id = str(uuid.uuid4())
    ticket = tickets.issue(session_id, authorization[7:])
    return JSONResponse(
        {
            "sessionId": session_id,
            "ticket": ticket.token,
            "expiresAt": ticket.expires_at.isoformat(),
            "websocketUrl": f"/v1/voice/sessions/{session_id}",
        }
    )


@app.websocket("/v1/voice/sessions/{session_id}")
async def voice_socket(websocket: WebSocket, session_id: str) -> None:
    protocols = [
        part.strip() for part in websocket.headers.get("sec-websocket-protocol", "").split(",")
    ]
    if "finguard.voice.v1" not in protocols:
        await websocket.close(code=4406)
        return
    token_candidates = [part for part in protocols if part != "finguard.voice.v1" and part]
    if len(token_candidates) != 1:
        await websocket.close(code=4401)
        return
    token = token_candidates[0]
    ticket = tickets.consume(token, session_id)
    if ticket is None:
        await websocket.close(code=4401)
        return
    session = VoiceSession(session_id, ticket.credential, services)
    sessions[session_id] = session
    await websocket.accept(subprotocol="finguard.voice.v1")
    session.machine.state = SessionState.CONNECTING
    try:
        while True:
            try:
                message = await asyncio.wait_for(
                    websocket.receive(), timeout=session.remaining_seconds()
                )
            except TimeoutError:
                await send_error(
                    websocket, session, "SESSION_EXPIRED", "session expired", fatal=True
                )
                await websocket.close(code=4400)
                break
            if message.get("type") == "websocket.disconnect":
                break
            if message.get("bytes") is not None:
                try:
                    await receive_audio(websocket, session, message["bytes"])
                    session.touch()
                except SessionProtocolError as exc:
                    await send_error(websocket, session, exc.code, exc.message, fatal=exc.fatal)
                    if exc.fatal:
                        await websocket.close(code=4400)
                        break
            elif message.get("text") is not None:
                try:
                    control = parse_event(json.loads(message["text"]))
                    await handle_control(websocket, session, control)
                    session.touch()
                except (ValueError, TypeError):
                    await send_error(
                        websocket, session, "INVALID_EVENT", "invalid JSON control event"
                    )
                except SessionProtocolError as exc:
                    await send_error(websocket, session, exc.code, exc.message, fatal=exc.fatal)
                    if exc.fatal:
                        await websocket.close(code=4400)
                        break
            else:
                await send_error(websocket, session, "INVALID_EVENT", "unsupported WebSocket frame")
    except WebSocketDisconnect:
        pass
    finally:
        await session.cancel()
        session.machine.close()
        sessions.pop(session_id, None)


async def receive_audio(websocket: WebSocket, session: VoiceSession, frame: bytes) -> None:
    settings = session.services.settings
    if len(frame) > settings.max_frame_bytes:
        raise SessionProtocolError(
            "FRAME_TOO_LARGE", f"audio frame exceeds {settings.max_frame_bytes} bytes"
        )
    if len(frame) % 2:
        raise SessionProtocolError("INVALID_AUDIO", "PCM16 frames must contain whole samples")
    max_bytes = settings.max_utterance_seconds * settings.audio_bytes_per_second
    # The first slice buffers a non-streaming utterance. Streaming STT will enforce the
    # five-second queue limit when it introduces a bounded producer/consumer queue.
    session.machine.receive_audio(len(frame), max_utterance_bytes=max_bytes)
    session.audio.extend(frame)


class JsonSender(Protocol):
    async def send_json(self, data: dict[str, Any]) -> None: ...

    async def close(self, code: int = 1000) -> None: ...


class VoiceTransport(JsonSender, Protocol):
    async def send_bytes(self, data: bytes) -> None: ...


async def handle_control(
    websocket: VoiceTransport, session: VoiceSession, control: ControlEvent
) -> None:
    if control.sessionId != session.session_id:
        raise SessionProtocolError("SESSION_MISMATCH", "event sessionId does not match socket")
    session.machine.accept_sequence(control.sequence)
    if isinstance(control, ConfigureEvent):
        session.machine.configure()
        await websocket.send_json(
            event(
                "session.ready",
                session.session_id,
                sequence=session.next_sequence(),
                inputSampleRate=session.services.settings.input_sample_rate,
                outputSampleRate=session.services.settings.output_sample_rate,
            )
        )
    elif isinstance(control, StartEvent):
        old_turn_id = (
            await session.cancel()
            if session.machine.state in (SessionState.THINKING, SessionState.SPEAKING)
            else None
        )
        session.machine.start_input(control.turnId)
        if old_turn_id:
            await websocket.send_json(
                event(
                    "response.cancelled",
                    session.session_id,
                    turn_id=old_turn_id,
                    sequence=session.next_sequence(),
                )
            )
    elif isinstance(control, CommitEvent):
        if session.machine.turn_id != control.turnId:
            raise SessionProtocolError("TURN_MISMATCH", "event turnId does not match active turn")
        session.machine.commit()
        session.task = asyncio.create_task(process_turn(websocket, session, control.turnId))
    elif isinstance(control, CancelEvent):
        old_turn_id = await session.cancel()
        if old_turn_id:
            await websocket.send_json(
                event(
                    "response.cancelled",
                    session.session_id,
                    turn_id=old_turn_id,
                    sequence=session.next_sequence(),
                )
            )
    elif isinstance(control, CloseEvent):
        session.machine.close()
        await websocket.close(code=1000)
    elif isinstance(control, PingEvent):
        await websocket.send_json(
            event("pong", session.session_id, sequence=session.next_sequence())
        )


async def process_turn(websocket: VoiceTransport, session: VoiceSession, turn_id: str) -> None:
    try:
        await _process_turn(websocket, session, turn_id)
    except asyncio.CancelledError:
        raise
    except AssistantClientError as exc:
        if session.machine.turn_id != turn_id:
            return
        session.machine.close()
        await send_error(websocket, session, exc.code, "assistant provider failed", fatal=True)
        await websocket.close(code=1011)
    except SpeechToTextError as exc:
        if session.machine.turn_id != turn_id:
            return
        session.machine.close()
        await send_error(websocket, session, exc.code, "speech provider failed", fatal=True)
        await websocket.close(code=1011)
    except TextToSpeechError as exc:
        if session.machine.turn_id != turn_id:
            return
        session.machine.close()
        await send_error(websocket, session, exc.code, "speech provider failed", fatal=True)
        await websocket.close(code=1011)
    except Exception as exc:
        if session.machine.turn_id != turn_id:
            return
        session.machine.close()
        await send_error(
            websocket,
            session,
            "PROVIDER_ERROR",
            f"voice provider failed: {type(exc).__name__}",
            fatal=True,
        )
        await websocket.close(code=1011)
    finally:
        if session.task is asyncio.current_task():
            session.task = None


async def _process_turn(websocket: VoiceTransport, session: VoiceSession, turn_id: str) -> None:
    generation = session.cancel_generation
    audio = bytes(session.audio)
    session.audio.clear()
    await websocket.send_json(
        event(
            "assistant.stage",
            session.session_id,
            turn_id=turn_id,
            sequence=session.next_sequence(),
            stage="transcribing",
        )
    )
    text = await session.services.stt.transcribe(
        audio, cancelled=lambda: session.cancelled(generation, turn_id)
    )
    if session.cancelled(generation, turn_id):
        return
    await websocket.send_json(
        event(
            "transcript.final",
            session.session_id,
            turn_id=turn_id,
            sequence=session.next_sequence(),
            text=text,
        )
    )
    session.machine.thinking()
    if session.payment_confirmation_required:
        reply_text = "No puedo confirmar pagos por voz; usa el control explícito en pantalla."
        reply_conversation = session.conversation
        payment_required = True
        reply_answer: dict[str, object] = {
            "outcome": "AWAITING_CONFIRMATION", "text": reply_text,
            "question": None, "intent_id": None, "plan": None,
            "conversation": reply_conversation, "currency": None,
            "total_minor": None, "movement_count": 0, "groups": [],
            "movements": [], "suggestions": [],
        }
    else:
        async def relay_stage(stage: str, detail: str | None) -> None:
            if not session.cancelled(generation, turn_id):
                await websocket.send_json(
                    event(
                        "assistant.stage",
                        session.session_id,
                        turn_id=turn_id,
                        sequence=session.next_sequence(),
                        stage=stage,
                        detail=detail,
                    )
                )

        reply = await session.services.assistant.ask(
            text,
            session.conversation,
            credential=session.credential,
            idempotency_key=f"voice:{session.session_id}:{turn_id}",
            on_stage=relay_stage,
        )
        if session.cancelled(generation, turn_id):
            return
        reply_text = reply.text
        reply_conversation = reply.conversation
        payment_required = reply.payment_confirmation_required
        reply_answer = reply.answer
    session.machine.speaking()
    await websocket.send_json(
        event(
            "assistant.text.final",
            session.session_id,
            turn_id=turn_id,
            sequence=session.next_sequence(),
            text=reply_text,
            answer=reply_answer,
            paymentConfirmationRequired=payment_required,
        )
    )
    await websocket.send_json(
        event(
            "audio.started",
            session.session_id,
            turn_id=turn_id,
            sequence=session.next_sequence(),
            sampleRate=session.services.settings.output_sample_rate,
        )
    )
    async for chunk in session.services.tts.synthesize(
        reply_text, cancelled=lambda: session.cancelled(generation, turn_id)
    ):
        if session.cancelled(generation, turn_id):
            return
        await websocket.send_bytes(chunk)
    if session.cancelled(generation, turn_id):
        return
    await websocket.send_json(
        event(
            "audio.completed", session.session_id, turn_id=turn_id, sequence=session.next_sequence()
        )
    )
    if session.cancelled(generation, turn_id):
        return
    # Conversation is committed only after the turn survives synthesis. A
    # cancelled answer may have completed remotely, but it is discarded here.
    session.conversation = reply_conversation
    session.payment_confirmation_required = payment_required
    session.machine.completed()
    await websocket.send_json(
        event(
            "turn.completed",
            session.session_id,
            turn_id=turn_id,
            sequence=session.next_sequence(),
            conversation=reply_conversation,
        )
    )


async def send_error(
    websocket: JsonSender,
    session: VoiceSession,
    code: str,
    message: str,
    *,
    fatal: bool = False,
) -> None:
    await websocket.send_json(
        event(
            "error",
            session.session_id,
            sequence=session.next_sequence(),
            code=code,
            message=message,
            fatal=fatal,
        )
    )
