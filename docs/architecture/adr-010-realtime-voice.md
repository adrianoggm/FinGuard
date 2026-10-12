# ADR-010 — Real-time voice is a channel adapter

**Status:** accepted, implemented and measured locally
**Date:** 2026-08-17
**Implemented:** 2026-08-18. Latency by stage in
[the voice latency report](../../apps/voice-gateway/evaluation/RESULTS.md). The gateway is a compose
service in the `voice` profile and its suite runs in CI beside the other two services.
**Numbering:** 009 is holdings and layered categorisation. 001–007 are in vision.md.

## Context

Phase 6 adds a multimodal conversation: microphone input, transcription, the existing financial
assistant, speech synthesis and interruption. The dangerous shortcut is to put financial routing
inside the voice service. That would create a second interpretation path beside the orchestrator,
with its own conversation state and payment behaviour, and the two channels would eventually
answer the same sentence differently.

Voice also changes the trust boundary. A browser WebSocket cannot attach the `Authorization`
header used by the existing HTTP client, raw audio is more sensitive than the derived transcript,
and an inaccurate transcription must never become an implicit confirmation that moves money.

## Decision

### 1. One new service, no new financial authority

`apps/voice-gateway` is an independent Python service with its own `pyproject.toml`, lockfile and
container. It owns audio and voice-session concerns only:

```text
browser audio
    ↓ WebSocket
voice-gateway: turn detection → STT → assistant client → TTS
    ↓ authenticated HTTP/SSE
orchestrator
    ↓
banking-api
```

The allowed dependency is `voice-gateway → orchestrator`. Direct access to `banking-api`, its
database, payment commands or financial domain types is forbidden. The gateway carries the
orchestrator's opaque conversation payload from one turn to the next and does not reconstruct it.

The service is hexagonal because its outside dependencies are exactly the ones likely to change:

```text
ports/
  speech_to_text.py
  text_to_speech.py
  assistant_client.py
  session_clock.py

adapters/
  inbound/websocket/
  outbound/fake_stt.py
  outbound/fake_tts.py
  outbound/orchestrator_http.py
```

Fake STT and TTS land first. They prove the protocol and cancellation semantics without making a
provider choice look like architecture.

### 2. Session bootstrap authenticates HTTP; WebSocket receives a one-use ticket

The browser first calls:

```http
POST /v1/voice/sessions
Authorization: Bearer <the same development credential the web client already uses>
```

The gateway keeps that credential only in memory for the bounded session and returns a session id,
a 30-second one-use ticket and the WebSocket URL. The browser presents the opaque base64url ticket
in `Sec-WebSocket-Protocol` beside `finguard.voice.v1`. The ticket is consumed on upgrade and is
never accepted twice. It is not put in a URL, where access logs and browser history would retain it.

Closing the socket, expiry or a fatal protocol error deletes the in-memory credential and session.
The gateway never writes credentials, raw audio, transcripts or financial answers to logs.

### 3. JSON controls the session; binary frames carry audio

Every JSON event has `type`, `sessionId`, `turnId`, `sequence` and `at`. `sequence` is monotonic per
direction and `turnId` makes late work from a cancelled turn recognisable and discardable.

Client control events:

| event | meaning |
| --- | --- |
| `session.configure` | negotiate protocol and audio formats |
| `input.start` | open a user turn; following binary frames belong to it. VOICE-001: optional `conversation`, the one on the client's screen, which replaces the session's own |
| `input.commit` | finish the utterance and start transcription |
| `response.cancel` | interrupt current assistant work or playback |
| `session.close` | close deliberately |
| `ping` | keepalive and clock sample |

Server events:

| event | meaning |
| --- | --- |
| `session.ready` | configuration accepted |
| `transcript.partial` | optional provisional text; never sent to the orchestrator |
| `transcript.final` | immutable text used for this turn |
| `assistant.stage` | existing orchestrator progress stage |
| `assistant.text.delta` / `assistant.text.final` | visible response text |
| `audio.started` / `audio.frame` / binary frames / `audio.completed` | one active synthesis stream. D-H-2 (fix wave): `audio.frame` is new, sent once immediately before every binary PCM frame, carrying only `turnId` -- the frame's own name on the wire, so a straggler from a barge-in-cancelled turn is dropped for certain rather than inferred from ordering (see below) |
| `turn.completed` | final conversation state and timings |
| `chartId` / `visualTurnId` | optional on `assistant.text.final`, `audio.started`, `audio.completed` and `turn.completed` since Wave 4.4: the chart this turn drew and the identity of the decision that drew it (the newest visual context's `turn_id`), so a client never plays one turn's audio beside another turn's chart; absent on an uncharted turn; version 1 unchanged (additive). D-H-1b (fix wave): `chartId` alone (never `visualTurnId`) is also stamped when the answer names a chart without drawing one -- `VISUAL_EXPLAINED` and the refusal-shaped `VISUAL_*` outcomes, read from the orchestrator's `about_chart_id` (D-H-1) -- so an explanation or a refusal identifies its chart too; `VISUAL_NEEDS_TARGET` still carries neither when no target could be resolved at all. |
| `response.cancelled` | interruption acknowledged and old turn fenced off |
| `error` | typed recoverable or fatal failure |
| `pong` | keepalive response and clock sample |

G-10 (fix wave): `error.fatal` is `false` for `ORCHESTRATOR_REJECTED` -- the orchestrator answering
this turn's own request with a 4xx (its own DTO rejecting, for instance, the `conversation` this
gateway just echoed back). That is one bad turn, not a broken session: the socket stays open and the
session returns to `READY`, ready for the next `input.start`, rather than closing with code `1011`
as every other assistant, speech or protocol failure still does.

Only one input stream and one output stream may be active. Binary client frames are rejected unless
the session is listening; binary server frames are meaningful only between `audio.started` and
`audio.completed`. Version 1 uses mono little-endian PCM16: 16 kHz input and 24 kHz output, with
20 ms target chunks. Local loopback does not justify adding a codec before the behaviour works.

D-H-2 (Wave 3 hardening, CLOSED by the fix wave): a binary PCM frame used to carry no turn
identity of its own — `turnId` named a JSON event, never a byte in the audio stream that followed
it. A barge-in's own race (a straggler frame from the just-cancelled turn's TTS task, enqueued
after the new turn's own `audio.started` clears the fence) was narrowed, not closed, by holding the
new turn's `audio.started` until this gateway's own `response.cancelled` for the *previous* turn
was observed (W-3) — a real, defensible narrowing of the race window, but a frame arriving after
that fence had already cleared (the new turn already playing) still had nothing naming it as
foreign. The metadata alternative (chosen over a wire-level binary header, which would have meant
decoding every PCM frame through an extra strip step on a path this gateway does not otherwise
touch): the gateway now sends one `audio.frame` event, carrying only `turnId`, immediately before
every binary chunk — WebSocket delivery is ordered, so the pairing with the very next binary
message is exact. The web client reads it and drops any binary frame whose named turn is not the
current one, before any of W-3's ordering heuristics run; those heuristics are left in place
(harmless, now redundant) rather than removed, since they still hold for the rarer case of no
`audio.frame` at all (an older gateway, or a fixture that has not adopted this event). No protocol
version bump: both sides of this wire changed together, in the same commit, and `audio.frame` is a
new, additive JSON event type rather than a change to any existing one's shape.

### 4. The state machine is explicit

```text
CONNECTING → READY → LISTENING → TRANSCRIBING → THINKING → SPEAKING → READY
                         ↘ error/retry ↗         ↘ completed ↗

THINKING  ─ response.cancel → READY
SPEAKING  ─ response.cancel → READY
READY     ─ input.start     → LISTENING
SPEAKING  ─ input.start     → cancel old turn, then LISTENING   (barge-in)
```

An event invalid for the current state produces `INVALID_STATE` without guessing what the client
meant. A second `input.commit`, audio after commit, synthesis from an old turn and duplicate
sequence numbers are protocol errors. Cancellation is idempotent.

The gateway owns this transport state. The orchestrator remains the sole owner of conversational
meaning; its returned `conversation` object is opaque gateway session data.

VOICE-001: the client holds the conversation, as it does for a typed question, and sends it with
every `input.start`. A turn typed between two spoken ones, a socket opened after a typed turn, or a
reply cancelled after the bank was read all change what is on screen, and the session's own copy
cannot know. When the field is present it replaces the session's copy; when it is absent the session
keeps its own, as before. The gateway still reads nothing in it.

### 5. Voice may prepare a payment, but version 1 may not confirm one

Speech recognition can turn noise or a near-homophone into “sí”. Therefore a voice turn may reach
the existing payment preparation path and the gateway may speak the preview, but a conversation in
`PAYMENT_CONFIRMATION` is not sent back as a spoken confirmation. The web client shows the preview
and requires its existing explicit confirmation control.

This is a channel policy, not a new payment rule. Supporting spoken confirmation later requires a
separate decision with transcript display, explicit read-back and evidence about false acceptance.

The orchestrator enforces it, not only the gateway: on a `VOICE` request a yes to a pending preview
counts as an ambiguous reply, so the preview stays open and nothing is confirmed, however many times
it is said. A spoken no still cancels. The gateway's one-shot refusal below remains the spoken
explanation; it is no longer the guarantee.

G-7 (fix wave): the spoken refusal this policy produces is not a fabricated orchestrator answer.
The assistant is never asked on this turn, so the gateway holds no real value for the eleven other
`QuestionResponse` keys a real answer carries, and §1 already forbids it from inventing financial
domain types to fill them with. The refusal's own `answer` carries exactly three keys -- `outcome`,
`text` and the unchanged `conversation` -- and a client must not expect the shape of a real
orchestrator answer from it.

### 6. Barge-in is cancellation, not another concurrent turn

Starting input while the assistant speaks immediately stops browser playback and sends
`response.cancel`. The gateway cancels TTS, stops forwarding stale assistant events and fences the
old `turnId` before accepting audio for the new one. If the orchestrator request cannot be cancelled
after banking has completed, its answer may finish internally but is discarded at the gateway.

No payment command is retried by voice infrastructure. The gateway forwards one stable
idempotency key per committed user turn and never invents a new key for a transport retry; issue
[#9](https://github.com/adrianoggm/FinGuard/issues/9) remains the authority for the preparation
endpoint's wider client-retry semantics.

### 7. Boundaries and data handling

- Maximum session: 30 minutes idle or 2 hours absolute.
- Maximum utterance: 30 seconds; maximum binary frame: 64 KiB.
- Maximum queued input audio: 5 seconds; exceeding it returns `BACKPRESSURE` and stops capture.
- Raw audio is held in memory only until transcription or cancellation.
- Partial transcripts never leave the gateway and never mutate conversation state.
- Logs contain ids, event types, byte counts, durations and error codes — no audio, transcript,
  answer, contact, account or raw credential.
- The compose service is published on `127.0.0.1` and belongs to the `voice` profile.

### 8. Latency is measured by stage before it is optimised

Each turn records monotonic timestamps for:

```text
input_start → input_commit → transcript_final → assistant_first_stage
            → assistant_text_final → tts_first_byte → playback_start → turn_completed
```

Reports include P50/P95 for transcription after commit, orchestrator first stage, first audible
response, complete turn and cancellation acknowledgement. The first implementation establishes the
baseline. The only initial hard latency invariant is cancellation acknowledgement and local
playback stop within 250 ms; other targets are signed after measuring real local STT and TTS.

## Delivery order

1. Contract fixtures and the session state machine.
2. Gateway skeleton with fake STT, fake TTS and a fake assistant.
3. HTTP/SSE assistant adapter and an end-to-end text turn.
4. Browser PCM capture, WebSocket transport and playback.
5. Local STT adapter, measured alone and through the gateway.
6. Local TTS adapter and streaming playback.
7. Barge-in, cancellation fencing and backpressure.
8. Text/audio/chart synchronisation and complete latency report.

Each step must leave a runnable vertical slice. Provider selection happens at steps 5 and 6 from
measured candidates, not in the service skeleton.

## Acceptance for phase 6

- Text typed and speech transcribed into the same sentence produce the same orchestrator outcome
  and conversation state.
- No voice component imports banking code or calls banking endpoints.
- Invalid event order, oversized audio, disconnects and provider timeouts have deterministic tests.
- Barge-in stops audible output and no event from the cancelled turn reaches the active turn.
- A spoken “sí” cannot confirm a payment in version 1.
- Fake-provider E2E is deterministic in CI; local-provider E2E is a separately reported benchmark.
- Latency report contains P50/P95 for every declared stage and names the hardware and model used.

## Consequences

There is one more service and one more protocol to operate. In return, audio dependencies do not
enter the orchestrator, financial interpretation remains single-sourced, local and hosted speech
providers remain replaceable, and interruption is a state transition that tests can prove rather
than timing behaviour spread across React callbacks.
