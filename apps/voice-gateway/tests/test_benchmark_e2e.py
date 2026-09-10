import asyncio
import json
import subprocess
import sys
from collections import deque
from pathlib import Path
from typing import Any

import pytest

# The benchmark is an executable script rather than an installed package module.
sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from benchmark_e2e import (
    OPTIONAL_STAGE_TIMING_NAMES,
    REQUIRED_TIMING_NAMES,
    Sample,
    run_cancel_probe,
    run_recorded_turn,
    run_turn,
    send_audio,
    summarize,
    validate_sample_counts,
    validate_timings,
)


def event(event_type: str, turn_id: str, **extra: Any) -> str:
    return json.dumps({"type": event_type, "turnId": turn_id, **extra})


def timings(*, stages: bool = False, **overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {"schemaVersion": 1}
    value.update({name: 1.0 for name in REQUIRED_TIMING_NAMES})
    value.update({name: 2.0 if stages else None for name in OPTIONAL_STAGE_TIMING_NAMES})
    value.update(overrides)
    return value


class Socket:
    def __init__(self, messages: list[str | bytes], *, recv_delay: float = 0.0) -> None:
        self.messages = deque(messages)
        self.sent: list[str | bytes] = []
        self.recv_delay = recv_delay

    async def send(self, message: str | bytes) -> None:
        self.sent.append(message)

    async def recv(self) -> str | bytes:
        if self.recv_delay:
            await asyncio.sleep(self.recv_delay)
        if not self.messages:
            await asyncio.Future()
        return self.messages.popleft()


def valid_turn_messages(turn_id: str = "benchmark-1") -> list[str | bytes]:
    return [
        event("assistant.text.final", turn_id, answer={"outcome": "ANSWERED"}),
        event("audio.started", turn_id),
        b"audio",
        event("turn.completed", turn_id, timings=timings()),
    ]


def test_percentiles_and_counts_are_deterministic() -> None:
    metrics = summarize(
        [Sample({"latency": 1.0}), Sample({"latency": 2.0}), Sample({"latency": 10.0})]
    )
    assert metrics["latency"] == {"count": 3, "p50Ms": 2.0, "p95Ms": 10.0, "meanMs": 4.333}
    validate_sample_counts(
        {
            name: {"count": 2, "p50Ms": 1.0, "p95Ms": 1.0, "meanMs": 1.0}
            for name in REQUIRED_TIMING_NAMES | {"clientPlaybackStartMs"}
        },
        2,
        cancel_runs=0,
        require_stage_timings=False,
    )


@pytest.mark.asyncio
async def test_audio_is_chunked_within_the_protocol_frame_limit() -> None:
    # Every frame has to fit the 64 KiB the protocol publishes. Sending a whole utterance in one
    # frame made the gateway answer FRAME_TOO_LARGE for anything longer than two seconds.
    sent: list[bytes] = []

    class Socket:
        async def send(self, payload: bytes) -> None:
            sent.append(payload)

    audio = bytes(200_000)
    await send_audio(Socket(), audio)
    assert b"".join(sent) == audio
    assert sent and all(len(frame) <= 65_536 for frame in sent)
    assert len(sent) > 1


def test_sample_counts_tolerate_stage_metrics_nobody_asked_to_require() -> None:
    # A stack whose orchestrator reports its stages produces these two metrics whether or not the
    # caller passed --require-stage-timings. Rejecting them made a healthy run report "failed":
    # every turn was PLANNED, banking answered 200, and the harness disagreed with itself because
    # per-turn validation calls them optional and this check called them unknown.
    metrics = {
        name: {"count": 2, "p50Ms": 1.0, "p95Ms": 1.0, "meanMs": 1.0}
        for name in REQUIRED_TIMING_NAMES | {"clientPlaybackStartMs"} | OPTIONAL_STAGE_TIMING_NAMES
    }
    validate_sample_counts(metrics, 2, cancel_runs=0, require_stage_timings=False)

    # Present is not the same as unmeasured: a stage metric that appears must have every sample.
    short = dict(metrics)
    name = sorted(OPTIONAL_STAGE_TIMING_NAMES)[0]
    short[name] = {"count": 1, "p50Ms": 1.0, "p95Ms": 1.0, "meanMs": 1.0}
    with pytest.raises(RuntimeError):
        validate_sample_counts(short, 2, cancel_runs=0, require_stage_timings=False)

    # An unknown metric is still an error.
    with pytest.raises(RuntimeError):
        validate_sample_counts(
            {**metrics, "inventedMs": {"count": 2, "p50Ms": 1.0, "p95Ms": 1.0, "meanMs": 1.0}},
            2,
            cancel_runs=0,
            require_stage_timings=False,
        )


def test_timings_require_schema_exact_names_numeric_values_and_optional_stages() -> None:
    parsed = validate_timings(timings())
    assert parsed["inputCommitToAssistantFirstStageMs"] is None
    with pytest.raises(RuntimeError, match="schemaVersion"):
        validate_timings({**timings(), "schemaVersion": 2})
    with pytest.raises(RuntimeError, match="unknown or missing"):
        validate_timings({**timings(), "unexpected": 1.0})
    with pytest.raises(RuntimeError, match="must be numeric"):
        validate_timings({**timings(), "inputStartToInputCommitMs": "slow"})
    with pytest.raises(RuntimeError, match="finite and non-negative"):
        validate_timings({**timings(), "inputStartToInputCommitMs": -1.0})
    with pytest.raises(RuntimeError, match="must be numeric"):
        validate_timings(timings(), require_stage_timings=True)


def test_script_entrypoint_exposes_the_cli() -> None:
    script = Path(__file__).parents[1] / "scripts" / "benchmark_e2e.py"
    completed = subprocess.run(
        [sys.executable, str(script), "--help"], capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0
    assert "--require-stage-timings" in completed.stdout


@pytest.mark.asyncio
async def test_turn_correlates_events_requires_audio_and_accepts_optional_stages() -> None:
    sample, sequence = await run_turn(Socket(valid_turn_messages()), "session", 1, 1.0, b"\0\0")
    assert sequence == 3
    assert "clientPlaybackStartMs" in sample.values
    assert not OPTIONAL_STAGE_TIMING_NAMES.intersection(sample.values)

    mismatched = Socket(valid_turn_messages("other"))
    with pytest.raises(RuntimeError, match="turnId"):
        await run_turn(mismatched, "session", 1, 1.0, b"\0\0")

    no_audio = Socket(
        [
            event("assistant.text.final", "benchmark-1", answer={"outcome": "ANSWERED"}),
            event("turn.completed", "benchmark-1", timings=timings()),
        ]
    )
    with pytest.raises(RuntimeError, match="audio.started"):
        await run_turn(no_audio, "session", 1, 1.0, b"\0\0")


@pytest.mark.asyncio
async def test_turn_uses_total_deadline_even_when_recv_keeps_returning() -> None:
    socket = Socket([event("assistant.stage", "benchmark-1")] * 20, recv_delay=0.02)
    with pytest.raises(TimeoutError):
        await run_turn(socket, "session", 1, 0.05, b"\0\0")


@pytest.mark.asyncio
async def test_turn_rejects_error_outcomes() -> None:
    socket = Socket(
        [event("assistant.text.final", "benchmark-1", answer={"outcome": "BANKING_UNAVAILABLE"})]
    )
    with pytest.raises(RuntimeError, match="BANKING_UNAVAILABLE"):
        await run_turn(socket, "session", 1, 1.0, b"\0\0")


@pytest.mark.asyncio
async def test_cancel_waits_for_audio_acknowledges_same_turn_and_fences_trailing_audio() -> None:
    socket = Socket(
        [
            event("audio.started", "cancel-probe-1"),
            b"audio",
            b"already-queued",
            event("response.cancelled", "cancel-probe-1"),
            event("pong", ""),
        ]
    )
    latency, sequence = await run_cancel_probe(socket, "session", 1, 1.0, b"\0\0")
    assert latency >= 0
    assert sequence == 5
    assert json.loads(str(socket.sent[-1]))["type"] == "ping"

    wrong_ack = Socket(
        [event("audio.started", "cancel-probe-1"), b"audio", event("response.cancelled", "other")]
    )
    with pytest.raises(RuntimeError, match="turnId"):
        await run_cancel_probe(wrong_ack, "session", 1, 1.0, b"\0\0")

    late_audio = Socket(
        [
            event("audio.started", "cancel-probe-1"),
            b"audio",
            event("response.cancelled", "cancel-probe-1"),
            b"late",
        ]
    )
    with pytest.raises(RuntimeError, match="after the cancellation ping/pong fence"):
        await run_cancel_probe(late_audio, "session", 1, 1.0, b"\0\0")


# --- Wave 4.4 (design §8): the follow-up turn measured beside the turn it follows -------------


@pytest.mark.asyncio
async def test_a_follow_up_text_produces_a_second_sample_under_its_own_prefix() -> None:
    """`--follow-up-text` runs a second turn straight after the recorded one, on the same
    session: the sequence keeps climbing across both turns, and the second `Sample`'s names all
    carry the `followUp:` prefix -- the same names, so `summarize` reports the same milestones
    twice, once per turn."""
    socket = Socket(valid_turn_messages("benchmark-1") + valid_turn_messages("benchmark-3"))
    samples, sequence = await run_recorded_turn(
        socket, "session", 1, 1.0, b"\0\0", follow_up_audio=b"\0\0"
    )

    assert sequence == 5
    assert len(samples) == 2
    recorded, follow_up = samples
    assert recorded.values and not any(name.startswith("followUp:") for name in recorded.values)
    assert set(follow_up.values) == {f"followUp:{name}" for name in recorded.values}


@pytest.mark.asyncio
async def test_without_a_follow_up_text_a_recorded_turn_is_unchanged() -> None:
    """No option, no second turn: the sequence and the single `Sample` are exactly what
    `run_turn` alone would have produced."""
    socket = Socket(valid_turn_messages("benchmark-1"))
    samples, sequence = await run_recorded_turn(
        socket, "session", 1, 1.0, b"\0\0", follow_up_audio=None
    )

    assert sequence == 3
    assert len(samples) == 1
    assert not any(name.startswith("followUp:") for name in samples[0].values)


def test_summarize_keeps_a_recorded_and_a_follow_up_row_apart() -> None:
    metrics = summarize(
        [
            Sample(
                {
                    "inputStartToTurnCompletedMs": 100.0,
                    "followUp:inputStartToTurnCompletedMs": 200.0,
                }
            )
        ]
    )
    assert metrics["inputStartToTurnCompletedMs"]["p50Ms"] == 100.0
    assert metrics["followUp:inputStartToTurnCompletedMs"]["p50Ms"] == 200.0


def test_validate_sample_counts_accepts_the_follow_up_metric_set() -> None:
    base = REQUIRED_TIMING_NAMES | {"clientPlaybackStartMs"}
    metrics = {
        name: {"count": 2, "p50Ms": 1.0, "p95Ms": 1.0, "meanMs": 1.0}
        for name in base | {f"followUp:{name}" for name in base}
    }
    validate_sample_counts(metrics, 2, cancel_runs=0, require_stage_timings=False, follow_up=True)
    # Without the flag the same (now unrecognised) `followUp:` names are unknown metrics.
    with pytest.raises(RuntimeError):
        validate_sample_counts(metrics, 2, cancel_runs=0, require_stage_timings=False)


def test_follow_up_text_is_exposed_on_the_cli() -> None:
    script = Path(__file__).parents[1] / "scripts" / "benchmark_e2e.py"
    completed = subprocess.run(
        [sys.executable, str(script), "--help"], capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0
    assert "--follow-up-text" in completed.stdout
