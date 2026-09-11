"""Measure the voice gateway protocol from a client perspective."""

import argparse
import asyncio
import json
import math
import os
import statistics
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from urllib.parse import urljoin

import httpx
import websockets
from websockets.typing import Subprotocol

TIMINGS_SCHEMA_VERSION = 1
REQUIRED_TIMING_NAMES = frozenset(
    {
        "inputStartToInputCommitMs",
        "inputCommitToTranscriptFinalMs",
        "assistantTextFinalToTtsFirstByteMs",
        "ttsFirstByteToTurnCompletedMs",
        "inputStartToTurnCompletedMs",
    }
)
OPTIONAL_STAGE_TIMING_NAMES = frozenset(
    {
        "inputCommitToAssistantFirstStageMs",
        "assistantFirstStageToAssistantTextFinalMs",
    }
)
KNOWN_TIMING_NAMES = REQUIRED_TIMING_NAMES | OPTIONAL_STAGE_TIMING_NAMES
ERROR_OUTCOMES = frozenset({"BANKING_UNAVAILABLE", "PROVIDER_ERROR"})
# The protocol caps a binary frame at 64 KiB, so audio has to be chunked like a browser chunks it.
# Sending the file whole worked only because the first measured utterance was one second long: at
# 16 kHz PCM16 anything past two seconds was rejected as FRAME_TOO_LARGE, which reads as a broken
# service rather than as a runner that ignored the limit it publishes. 100 ms is a capture block.
AUDIO_FRAME_BYTES = 3_200


async def send_audio(socket: Any, audio: bytes) -> None:
    for offset in range(0, len(audio), AUDIO_FRAME_BYTES):
        await socket.send(audio[offset : offset + AUDIO_FRAME_BYTES])


@dataclass
class Sample:
    values: dict[str, float]


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        raise ValueError("cannot calculate a percentile without samples")
    ordered = sorted(values)
    index = min(len(ordered) - 1, round((len(ordered) - 1) * fraction))
    return round(ordered[index], 3)


def summarize(samples: list[Sample]) -> dict[str, dict[str, float | int]]:
    names = sorted({name for sample in samples for name in sample.values})
    return {
        name: {
            "count": len(
                values := [sample.values[name] for sample in samples if name in sample.values]
            ),
            "p50Ms": percentile(values, 0.50),
            "p95Ms": percentile(values, 0.95),
            "meanMs": round(statistics.fmean(values), 3),
        }
        for name in names
    }


def validate_timings(
    timings: object, *, require_stage_timings: bool = False
) -> dict[str, float | None]:
    if not isinstance(timings, dict) or timings.get("schemaVersion") != TIMINGS_SCHEMA_VERSION:
        raise RuntimeError("unsupported or missing timings schemaVersion")
    if set(timings) - {"schemaVersion"} != KNOWN_TIMING_NAMES:
        raise RuntimeError("turn.completed contained an unknown or missing timing metric")
    result: dict[str, float | None] = {}
    for name in KNOWN_TIMING_NAMES:
        value = timings[name]
        if value is None and name in OPTIONAL_STAGE_TIMING_NAMES and not require_stage_timings:
            result[name] = None
        elif isinstance(value, bool) or not isinstance(value, (int, float)):
            raise RuntimeError(f"timing metric {name} must be numeric")
        else:
            numeric = float(value)
            if not math.isfinite(numeric) or numeric < 0:
                raise RuntimeError(f"timing metric {name} must be finite and non-negative")
            result[name] = numeric
    for name in REQUIRED_TIMING_NAMES:
        if result[name] is None:
            raise RuntimeError(f"required timing metric {name} is null")
    return result


def validate_sample_counts(
    metrics: dict[str, dict[str, float | int]],
    runs: int,
    *,
    cancel_runs: int,
    require_stage_timings: bool,
    follow_up: bool = False,
) -> None:
    expected = REQUIRED_TIMING_NAMES | {"clientPlaybackStartMs"}
    if require_stage_timings:
        expected |= OPTIONAL_STAGE_TIMING_NAMES
    if follow_up:
        # `--follow-up-text` renames every one of a recorded turn's own metrics under the
        # `followUp:` prefix (Wave 4.4) rather than inventing a second, smaller vocabulary — the
        # follow-up turn is measured exactly like the turn it follows.
        expected |= {f"followUp:{name}" for name in expected}
    if cancel_runs:
        expected |= {"cancelAcknowledgementMs"}
    # The stage metrics appear whenever the orchestrator reports its stages, which is not the same
    # question as whether the caller demanded them. `validate_timings` already treats them as
    # optional per turn; treating them as unknown here made a run where every turn was PLANNED
    # report "failed".
    optional = OPTIONAL_STAGE_TIMING_NAMES | {
        f"followUp:{name}" for name in OPTIONAL_STAGE_TIMING_NAMES
    }
    expected |= set(metrics) & optional
    if set(metrics) != expected:
        raise RuntimeError("benchmark did not produce the exact required metric set")
    for name in expected:
        expected_count = cancel_runs if name == "cancelAcknowledgementMs" else runs
        if metrics[name]["count"] != expected_count:
            raise RuntimeError(f"metric {name} did not contain {expected_count} samples")


def control(event_type: str, session_id: str, sequence: int, turn_id: str | None = None) -> str:
    return json.dumps(
        {
            "type": event_type,
            "sessionId": session_id,
            "turnId": turn_id,
            "sequence": sequence,
            "at": datetime.now(UTC).isoformat(),
        },
        separators=(",", ":"),
    )


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("benchmark turn deadline exceeded")
    return remaining


async def receive_until(
    socket: Any,
    deadline: float,
    predicate: Callable[[str | bytes], bool],
    on_message: Callable[[str | bytes], None] | None = None,
) -> str | bytes:
    while True:
        message = await asyncio.wait_for(socket.recv(), timeout=_remaining(deadline))
        if on_message is not None:
            on_message(message)
        if predicate(message):
            return cast(str | bytes, message)


def _payload(message: str | bytes) -> dict[str, Any]:
    if not isinstance(message, str):
        raise RuntimeError("expected a JSON event")
    value = json.loads(message)
    if not isinstance(value, dict):
        raise RuntimeError("gateway event must be an object")
    return value


def _check_outcome(payload: dict[str, Any]) -> None:
    answer = payload.get("answer")
    outcome = answer.get("outcome") if isinstance(answer, dict) else None
    if not isinstance(outcome, str):
        raise RuntimeError("assistant outcome was not observed")
    if outcome.endswith("_ERROR") or outcome in ERROR_OUTCOMES:
        raise RuntimeError(f"assistant outcome: {outcome}")


def _safe_error(exc: Exception) -> str:
    """Expose only runner-authored protocol diagnostics, never provider payloads."""
    return str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__


async def run_turn(
    socket: Any,
    session_id: str,
    sequence: int,
    wait_seconds: float,
    audio: bytes,
    *,
    require_stage_timings: bool = False,
) -> tuple[Sample, int]:
    turn_id = f"benchmark-{sequence}"
    deadline = time.monotonic() + wait_seconds
    await socket.send(control("input.start", session_id, sequence, turn_id))
    await send_audio(socket, audio)
    started = time.monotonic()
    commit_sequence = sequence + 1
    await socket.send(control("input.commit", session_id, commit_sequence, turn_id))
    first_audio: float | None = None
    completed: dict[str, Any] | None = None
    assistant_final = False
    audio_started = False
    while completed is None:
        message = await asyncio.wait_for(socket.recv(), timeout=_remaining(deadline))
        if isinstance(message, bytes):
            if not audio_started:
                raise RuntimeError("audio frame arrived before audio.started")
            if first_audio is None:
                first_audio = (time.monotonic() - started) * 1000
            continue
        payload = _payload(message)
        event_type = payload.get("type")
        event_turn_id = payload.get("turnId")
        if event_type == "error":
            raise RuntimeError(f"gateway error: {payload.get('code', 'UNKNOWN')}")
        if (
            event_type in {"assistant.text.final", "audio.started", "turn.completed"}
            and event_turn_id != turn_id
        ):
            raise RuntimeError(f"{event_type} did not match the active turnId")
        if event_type == "audio.started":
            audio_started = True
        elif event_type == "assistant.text.final":
            _check_outcome(payload)
            assistant_final = True
        elif event_type == "turn.completed":
            completed = payload
    if not audio_started or first_audio is None:
        raise RuntimeError("turn did not produce an audio.started event and audio frame")
    if not assistant_final:
        raise RuntimeError("assistant.text.final was not observed")
    timings = validate_timings(completed["timings"], require_stage_timings=require_stage_timings)
    values = {name: value for name, value in timings.items() if value is not None}
    values["clientPlaybackStartMs"] = round(first_audio, 3)
    return Sample(values), commit_sequence + 1


def _prefixed(sample: Sample, prefix: str) -> Sample:
    return Sample({f"{prefix}{name}": value for name, value in sample.values.items()})


async def run_recorded_turn(
    socket: Any,
    session_id: str,
    sequence: int,
    wait_seconds: float,
    audio: bytes,
    *,
    follow_up_audio: bytes | None,
    require_stage_timings: bool = False,
) -> tuple[list[Sample], int]:
    """One recorded turn and, when `follow_up_audio` is set, a second turn straight after it on
    the same session -- the presentation-only follow-up (Wave 4.4, design §8) measured beside the
    turn it follows rather than as a run of its own. The follow-up `Sample` is renamed under the
    `followUp:` prefix so `summarize` reports it as its own set of rows."""
    sample, sequence = await run_turn(
        socket,
        session_id,
        sequence,
        wait_seconds,
        audio,
        require_stage_timings=require_stage_timings,
    )
    samples = [sample]
    if follow_up_audio is not None:
        follow_up_sample, sequence = await run_turn(
            socket,
            session_id,
            sequence,
            wait_seconds,
            follow_up_audio,
            require_stage_timings=require_stage_timings,
        )
        samples.append(_prefixed(follow_up_sample, "followUp:"))
    return samples, sequence


async def run_cancel_probe(
    socket: Any, session_id: str, sequence: int, wait_seconds: float, audio: bytes
) -> tuple[float, int]:
    turn_id = f"cancel-probe-{sequence}"
    deadline = time.monotonic() + wait_seconds
    await socket.send(control("input.start", session_id, sequence, turn_id))
    await send_audio(socket, audio)
    await socket.send(control("input.commit", session_id, sequence + 1, turn_id))
    while True:
        message = await asyncio.wait_for(socket.recv(), timeout=_remaining(deadline))
        if isinstance(message, bytes):
            raise RuntimeError("audio frame arrived before audio.started")
        payload = _payload(message)
        event_type = payload.get("type")
        if (
            event_type in {"assistant.text.final", "audio.started", "turn.completed"}
            and payload.get("turnId") != turn_id
        ):
            raise RuntimeError(f"{event_type} did not match the active turnId")
        if event_type == "error":
            raise RuntimeError(f"gateway error: {payload.get('code', 'UNKNOWN')}")
        if event_type == "turn.completed" and payload.get("turnId") == turn_id:
            raise RuntimeError("cancel probe completed before audio started")
        if event_type == "audio.started":
            if payload.get("turnId") != turn_id:
                raise RuntimeError("audio.started did not match the active turnId")
            break
    # The first binary frame is the observable start of actual playback data.
    message = await asyncio.wait_for(socket.recv(), timeout=_remaining(deadline))
    if not isinstance(message, bytes):
        raise RuntimeError("audio.started was not followed by an audio frame")
    sent = time.monotonic()
    await socket.send(control("response.cancel", session_id, sequence + 2, turn_id))
    while True:
        message = await asyncio.wait_for(socket.recv(), timeout=_remaining(deadline))
        if isinstance(message, bytes):
            # Frames already queued before the gateway processes the cancel may still arrive.
            # The acknowledgement and application-level pong below form the actual fence.
            continue
        payload = _payload(message)
        event_type = payload.get("type")
        if event_type == "error":
            raise RuntimeError(f"gateway error: {payload.get('code', 'UNKNOWN')}")
        if event_type == "turn.completed" and payload.get("turnId") == turn_id:
            raise RuntimeError("cancel probe completed before cancellation was observed")
        if event_type == "response.cancelled":
            if payload.get("turnId") != turn_id:
                raise RuntimeError("response.cancelled did not match the active turnId")
            break
    await socket.send(control("ping", session_id, sequence + 3))
    while True:
        trailing = await asyncio.wait_for(socket.recv(), timeout=_remaining(deadline))
        if isinstance(trailing, bytes):
            raise RuntimeError("audio frame arrived after the cancellation ping/pong fence")
        payload = _payload(trailing)
        if payload.get("type") == "error":
            raise RuntimeError(f"gateway error: {payload.get('code', 'UNKNOWN')}")
        if payload.get("type") == "pong":
            break
    return round((time.monotonic() - sent) * 1000, 3), sequence + 4


async def _pcm_audio(pcm_file: str | None, text: str | None) -> bytes | None:
    """PCM16 for one turn: a real recording's bytes verbatim when `pcm_file` is given, else
    `text` UTF-8-encoded and padded to an even length -- a stand-in that only transcribes under
    the fake STT, not a real provider. Returns `None` when neither is given (no turn)."""
    if pcm_file:
        audio = await asyncio.to_thread(Path(pcm_file).read_bytes)
    elif text:
        audio = text.encode("utf-8")
        if len(audio) % 2:
            audio += b"\x00"
    else:
        return None
    if not audio or len(audio) % 2:
        raise RuntimeError("audio must be non-empty PCM16 with complete samples")
    return audio


async def benchmark(args: argparse.Namespace) -> dict[str, Any]:
    token = args.token or os.environ.get("VOICE_GATEWAY_TOKEN")
    if not token:
        raise RuntimeError("missing token: pass --token or VOICE_GATEWAY_TOKEN")
    audio = await _pcm_audio(args.pcm_file, args.text)
    if audio is None:
        raise RuntimeError("input audio must be non-empty PCM16 with complete samples")
    # `--follow-up-pcm-file` takes a real recording for the follow-up turn, the way `--pcm-file`
    # does for the first; `--follow-up-text` remains the fake-STT stand-in when no recording is
    # given. A real-provider follow-up row (M-7) needs the file, not the text.
    follow_up_audio = await _pcm_audio(args.follow_up_pcm_file, args.follow_up_text)
    base_url = args.url.rstrip("/")
    async with httpx.AsyncClient(base_url=base_url, timeout=args.timeout) as client:
        response = await client.post(
            "/v1/voice/sessions", headers={"Authorization": f"Bearer {token}"}
        )
        response.raise_for_status()
        session = response.json()
        ws_path = str(session["websocketUrl"])
        ws_url = urljoin(base_url + "/", ws_path.lstrip("/"))
        ws_url = ws_url.replace("http://", "ws://", 1).replace("https://", "wss://", 1)
        async with websockets.connect(
            ws_url,
            subprotocols=[Subprotocol("finguard.voice.v1"), Subprotocol(str(session["ticket"]))],
            open_timeout=args.timeout,
        ) as socket:
            session_id = str(session["sessionId"])
            sequence = 0
            await socket.send(control("session.configure", session_id, sequence))
            await receive_until(
                socket,
                time.monotonic() + args.timeout,
                lambda message: _payload(message).get("type") == "session.ready",
            )
            sequence += 1
            for _ in range(args.warmup):
                _, sequence = await run_turn(
                    socket,
                    session_id,
                    sequence,
                    args.timeout,
                    audio,
                    require_stage_timings=args.require_stage_timings,
                )
            samples: list[Sample] = []
            cancel_samples: list[Sample] = []
            failures: list[dict[str, Any]] = []
            completed_turns = 0
            for index in range(args.runs):
                try:
                    turn_samples, sequence = await run_recorded_turn(
                        socket,
                        session_id,
                        sequence,
                        args.timeout,
                        audio,
                        follow_up_audio=follow_up_audio,
                        require_stage_timings=args.require_stage_timings,
                    )
                    samples.extend(turn_samples)
                    completed_turns += 1
                    if args.cancel_runs and index < args.cancel_runs:
                        cancel_ms, sequence = await run_cancel_probe(
                            socket, session_id, sequence, args.timeout, audio
                        )
                        cancel_samples.append(Sample({"cancelAcknowledgementMs": cancel_ms}))
                except TimeoutError as exc:
                    failures.append(
                        {"run": index + 1, "kind": "timeout", "error": type(exc).__name__}
                    )
                except Exception as exc:
                    failures.append(
                        {"run": index + 1, "kind": "failure", "error": _safe_error(exc)}
                    )
                if failures:
                    break
            metrics = summarize(samples + cancel_samples)
            if not failures and completed_turns == args.runs:
                try:
                    validate_sample_counts(
                        metrics,
                        args.runs,
                        cancel_runs=args.cancel_runs,
                        require_stage_timings=args.require_stage_timings,
                        follow_up=follow_up_audio is not None,
                    )
                except RuntimeError as exc:
                    failures.append({"kind": "validation", "error": type(exc).__name__})
            return {
                "status": "ok" if not failures and completed_turns == args.runs else "failed",
                "providerConfiguration": "read from gateway environment; no credentials emitted",
                "warmupRuns": args.warmup,
                "followUpText": args.follow_up_text,
                "followUpPcmFile": args.follow_up_pcm_file,
                "requestedRuns": args.runs,
                "completedRuns": completed_turns,
                "metrics": metrics,
                "failures": failures,
            }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--token", help="Bearer token; omitted from output")
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--cancel-runs", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--text", default="hola")
    parser.add_argument(
        "--follow-up-text",
        default=None,
        help="when set, every recorded turn is followed by a second turn with this sentence; "
        "its Sample durations are reported under a followUp: prefix",
    )
    parser.add_argument("--pcm-file", help="raw mono PCM16 little-endian at 16 kHz")
    parser.add_argument(
        "--follow-up-pcm-file",
        default=None,
        help="raw mono PCM16 little-endian at 16 kHz for the follow-up turn; a real recording, "
        "the way --pcm-file is for the first turn. Overrides --follow-up-text, whose UTF-8 "
        "bytes only transcribe under the fake STT and cannot produce a real-provider row.",
    )
    parser.add_argument("--require-stage-timings", action="store_true")
    parser.add_argument("--format", choices=("json", "table"), default="json")
    args = parser.parse_args()
    if (
        args.runs < 1
        or args.warmup < 0
        or args.cancel_runs < 0
        or args.cancel_runs > args.runs
        or args.timeout <= 0
    ):
        parser.error("runs/timeout must be positive; warmup/cancel-runs must be in range")
    return args


def main() -> int:
    args = parse_args()
    try:
        result = asyncio.run(benchmark(args))
    except Exception as exc:
        result = {
            "status": "failed",
            "failures": [{"kind": "startup", "error": type(exc).__name__}],
        }
    if args.format == "json":
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"status={result['status']} completed={result.get('completedRuns', 0)}/{args.runs}")
        for name, values in result.get("metrics", {}).items():
            print(
                f"{name}: p50={values['p50Ms']:.3f} ms "
                f"p95={values['p95Ms']:.3f} ms n={values['count']}"
            )
        for failure in result.get("failures", []):
            print(f"failure run={failure.get('run', '-')}: {failure['kind']} ({failure['error']})")
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
