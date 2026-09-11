import asyncio
import json
import re
import time
import uuid
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, Protocol

from fastapi import FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
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
from finguard_voice_gateway.timings import TurnTimings

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

# The browser's first dependency, not a detail: a rejected preflight never reaches a route, so
# without this the session bootstrap is unreachable from a page while answering `curl` perfectly.
# The orchestrator's own CORS comment describes this exact failure; this service repeated it because
# every client that exercised it — the benchmark, the protocol tests — was not a browser.
#
# Methods and headers are spelled out rather than "*": the answer to a preflight is a statement
# about what this service accepts, and a wildcard makes that statement unfalsifiable.
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(services.settings.web_origins),
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


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


_NUMBERED_LINE = re.compile(r"^\d+\.\s*")


def spoken_text(reply_spoken: str | None, reply_text: str) -> str:
    """What TTS should read for this turn (G-5): `reply_spoken` when the assistant set one,
    else `reply_text` with a compound answer's own «1. … 2. …» numbering stripped.

    `_compound_read_reply` joins each part's text with `f"{index}. {part.text}"` and builds
    those parts with no `spoken_for_voice` (a real orchestrator gap, confirmed by reading
    `llm_controller.py` -- not something this gateway invents a fix for on the orchestrator's
    side). Read aloud verbatim, a two-part answer says "uno punto ... dos punto ..." before
    either sentence -- numbering a screen reads by eye and a voice has no use for. Every line
    that starts with a number and a dot has it stripped, and the remaining lines are joined with
    a plain space rather than a newline, so TTS reads one sentence after another instead of
    silence where the screen had a line break -- a space, not ". ", because each part's own
    `text` already ends in its own sentence punctuation. A single, non-numbered answer's
    `reply_text` is unaffected: no line here starts that way, so `_NUMBERED_LINE` never matches
    and nothing changes.
    """
    if reply_spoken is not None:
        return reply_spoken
    stripped_lines = (_NUMBERED_LINE.sub("", line) for line in reply_text.split("\n"))
    return " ".join(line for line in stripped_lines if line)


def chart_identity(answer: Mapping[str, Any], conversation: object) -> dict[str, str]:
    """`chartId`/`visualTurnId` for the four turn events -- only the keys the reply carries.

    The gateway reads two strings off a payload it otherwise passes through unchanged; it still
    interprets nothing (ADR-010). `chartId` is `answer["visualization"]["chart_id"]` when that is a
    str AND `answer["visualization"]["kind"] == "chart"` (G-11) -- a KPI answer's visualization has
    no `chart_id` and a KPI is not a chart a later turn can name, so it is omitted, and a future
    kind this gateway has never seen must not be read as a chart merely because it carries a
    same-named field; a restyle answers with `visualization` empty and the chart only in
    `answer["visualization_derivation"]["chart_id"]` (`_visual_only_response`), so that is read as
    the fallback under the same guards. `visualTurnId` is the newest
    `conversation["visual_contexts"][-1]["turn_id"]`, but only when that same newest context's
    `chart_id` equals the `chartId` just found: contexts persist across turns
    (`Conversation.with_visual_context`), so an uncharted turn -- or one whose chart came from a
    stale context -- must not be stamped with a previous turn's decision identity. An uncharted
    turn returns `{}` so its events look exactly as they did before Wave 4.4 -- omitted, never
    `null`.

    G-5: a compound (`parts`) answer's own top-level `visualization`/`visualization_derivation`
    are always `None` (`_compound_read_reply`) -- whichever part drew a chart carries it inside
    `answer["parts"][i]["visualization"]` instead. Rather than reaching into `parts`, a `chartId`
    still not found above is read from the conversation's own newest visual context when `parts`
    is a non-empty list: that context IS the compound's last charted part, because
    `_compound_read_reply` appends each part's new context in clause order before returning. This
    reading is weaker than the two above -- it has no chart id of its own to compare the newest
    context against, so a compound answer that draws no chart at all, while its conversation still
    carries a stale context from an earlier turn, would wrongly borrow that stale identity. Known
    limitation, undetectable from this function's own inputs; not exercised by the fake, whose
    only `parts` case always charts its last part.
    """
    identity: dict[str, str] = {}
    visualization = answer.get("visualization")
    # G-11: gated on `kind == "chart"`, not merely on a `chart_id` string being present. A KPI's
    # visualization has no `chart_id` (already excluded by the `isinstance` check below), but a
    # future kind this gateway has never seen -- anything other than "chart" or "kpi" -- must not
    # be read as if it were one just because it happens to carry a same-named field: the web
    # client throws on any `kind` it does not recognise, and stamping a `chartId` the client will
    # never render is worse than stamping none.
    if isinstance(visualization, Mapping) and visualization.get("kind") == "chart":
        chart_id = visualization.get("chart_id")
        if isinstance(chart_id, str):
            identity["chartId"] = chart_id
    if "chartId" not in identity:
        derivation = answer.get("visualization_derivation")
        if isinstance(derivation, Mapping):
            derived_chart_id = derivation.get("chart_id")
            if isinstance(derived_chart_id, str):
                identity["chartId"] = derived_chart_id
    if "chartId" not in identity:
        parts = answer.get("parts")
        if isinstance(parts, list) and parts and isinstance(conversation, Mapping):
            visual_contexts = conversation.get("visual_contexts")
            if isinstance(visual_contexts, list) and visual_contexts:
                newest_context = visual_contexts[-1]
                if isinstance(newest_context, Mapping):
                    newest_chart_id = newest_context.get("chart_id")
                    if isinstance(newest_chart_id, str):
                        identity["chartId"] = newest_chart_id
    if "chartId" in identity and isinstance(conversation, Mapping):
        visual_contexts = conversation.get("visual_contexts")
        if isinstance(visual_contexts, list) and visual_contexts:
            last_context = visual_contexts[-1]
            if isinstance(last_context, Mapping):
                context_chart_id = last_context.get("chart_id")
                turn_id = last_context.get("turn_id")
                if (
                    isinstance(turn_id, str)
                    and isinstance(context_chart_id, str)
                    and context_chart_id == identity["chartId"]
                ):
                    identity["visualTurnId"] = turn_id
    return identity


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
        self.turn_timings: dict[str, TurnTimings] = {}

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
        if old_turn_id is not None:
            self.turn_timings.pop(old_turn_id, None)
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
        session.turn_timings[control.turnId] = TurnTimings(session.monotonic)
        session.turn_timings[control.turnId].mark("input_start")
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
        timings = session.turn_timings.get(control.turnId)
        if timings is not None:
            timings.mark("input_commit")
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
        if exc.code == "ORCHESTRATOR_REJECTED":
            # G-10: a 4xx is the orchestrator refusing this turn's own request -- e.g. the
            # `conversation` this gateway echoed back failed the orchestrator's DTO validation
            # (`orchestrator_assistant.py`'s `_status_message`, any status below 500 other than
            # 401/403). That is one bad turn, not a broken session: `machine.cancel()` returns
            # to READY (clearing the dead turn id) so the next `input.start` behaves like any
            # other new turn, and the socket -- and everything already said on it -- survives.
            session.machine.cancel()
            await send_error(websocket, session, exc.code, "assistant provider rejected the turn")
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
    timings = session.turn_timings.get(turn_id)
    if timings is None:
        # Keep direct internal callers and protocol-compatible adapters working;
        # normal WebSocket turns create these marks in handle_control.
        timings = TurnTimings(session.monotonic)
        timings.mark("input_start")
        timings.mark("input_commit")
        session.turn_timings[turn_id] = timings
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
    timings.mark("transcript_final")
    session.machine.thinking()
    # G-1: captured before the branch below reads it, so the commit at the bottom of this
    # function can tell "this turn WAS the one-shot refusal" from "this turn actually asked the
    # assistant" -- the two cases clear the session-local latch differently.
    refusing_payment = session.payment_confirmation_required
    if refusing_payment:
        reply_text = "No puedo confirmar pagos por voz; usa el control explícito en pantalla."
        reply_spoken = None
        reply_conversation = session.conversation
        payment_required = True
        # G-7: a minimal, honest answer -- `outcome`, `text` and the unchanged `conversation`,
        # never the other nine `QuestionResponse` keys the gateway used to invent (`intent_id`,
        # `plan`, `currency`, `total_minor`, `movement_count`, `groups`, `movements`,
        # `suggestions`, and the two literal fields above). The assistant is never asked this
        # turn, so the gateway has no real values for any of them, and ADR-010 (§1, "no new
        # financial authority") says it carries no financial domain types to fabricate one with.
        reply_answer: dict[str, object] = {
            "outcome": "AWAITING_CONFIRMATION",
            "text": reply_text,
            "conversation": reply_conversation,
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
                timings.mark("assistant_first_stage")

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
        reply_spoken = reply.spoken
        reply_conversation = reply.conversation
        payment_required = reply.payment_confirmation_required
        reply_answer = reply.answer
    # Computed once the reply is known, both branches: the chart this turn drew and the
    # identity of the decision that drew it, riding the four turn events below.
    identity = chart_identity(reply_answer, reply_conversation)
    # G-4: committed here, before synthesis, not after `audio.completed` survives. A barge-in
    # is cancellation during TTS -- the normal voice gesture -- and until this point every
    # cancellation check above already returned before touching session state, so reaching here
    # means this turn's reply is real and belongs to the conversation regardless of what happens
    # to its audio next. The old placement (right before `turn.completed`) discarded the chart
    # a charted answer had just drawn on exactly that gesture: the holder saw it, interrupted,
    # and the next "ponlo en horizontal" found no chart to restyle.
    session.conversation = reply_conversation
    # G-1: a refusal is spoken once and this session-local latch clears immediately after --
    # the assistant was never asked this turn, so nothing it said could have changed the
    # conversation's real payment state, and holding the latch open forever (the old behaviour)
    # meant the assistant could never be reached again on this session, not even to cancel.
    # ADR-010 §5 blocks a spoken *confirmation* turn from moving money, not every later turn.
    session.payment_confirmation_required = False if refusing_payment else payment_required
    session.machine.speaking()
    # `text=reply_text` here always -- the screen shows the full written answer regardless of
    # channel (Task 3, Wave 4.4); only the audio below speaks `reply_spoken` when there is one.
    await websocket.send_json(
        event(
            "assistant.text.final",
            session.session_id,
            turn_id=turn_id,
            sequence=session.next_sequence(),
            text=reply_text,
            answer=reply_answer,
            paymentConfirmationRequired=payment_required,
            **identity,
        )
    )
    timings.mark("assistant_text_final")
    await websocket.send_json(
        event(
            "audio.started",
            session.session_id,
            turn_id=turn_id,
            sequence=session.next_sequence(),
            sampleRate=session.services.settings.output_sample_rate,
            **identity,
        )
    )
    async for chunk in session.services.tts.synthesize(
        spoken_text(reply_spoken, reply_text),
        cancelled=lambda: session.cancelled(generation, turn_id),
    ):
        if session.cancelled(generation, turn_id):
            return
        await websocket.send_bytes(chunk)
        timings.mark("tts_first_byte")
    if session.cancelled(generation, turn_id):
        return
    await websocket.send_json(
        event(
            "audio.completed",
            session.session_id,
            turn_id=turn_id,
            sequence=session.next_sequence(),
            **identity,
        )
    )
    if session.cancelled(generation, turn_id):
        return
    session.machine.completed()
    timings.mark("turn_completed")
    try:
        await websocket.send_json(
            event(
                "turn.completed",
                session.session_id,
                turn_id=turn_id,
                sequence=session.next_sequence(),
                conversation=reply_conversation,
                timings={"schemaVersion": 1, **timings.durations()},
                **identity,
            )
        )
    finally:
        session.turn_timings.pop(turn_id, None)


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
