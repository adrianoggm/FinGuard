import json
from datetime import datetime

import pytest

from finguard_voice_gateway.app import VoiceSession, handle_control, services
from finguard_voice_gateway.protocol import parse_event


class _Socket:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []
        self.audio: list[bytes] = []

    async def send_json(self, value: dict[str, object]) -> None:
        self.events.append(value)

    async def send_bytes(self, value: bytes) -> None:
        self.audio.append(value)

    async def close(self, code: int = 1000) -> None:
        return None


def _control(event_type: str, sequence: int, turn_id: str | None = None) -> str:
    return json.dumps(
        {
            "type": event_type,
            "sessionId": "session",
            "turnId": turn_id,
            "sequence": sequence,
            "at": datetime.now().isoformat(),
        }
    )


@pytest.mark.asyncio
async def test_turn_completed_contains_ordered_sanitized_timings_and_tts_first_byte() -> None:
    session = VoiceSession("session", "credential", services)
    socket = _Socket()
    await handle_control(socket, session, parse_event(json.loads(_control("session.configure", 0))))
    await handle_control(
        socket, session, parse_event(json.loads(_control("input.start", 1, "turn-1")))
    )
    await handle_control(
        socket, session, parse_event(json.loads(_control("input.commit", 2, "turn-1")))
    )
    assert session.task is not None
    await session.task

    completed = next(item for item in socket.events if item["type"] == "turn.completed")
    timings = completed["timings"]
    assert isinstance(timings, dict)
    assert timings["schemaVersion"] == 1
    assert timings["inputStartToInputCommitMs"] >= 0
    assert timings["inputCommitToTranscriptFinalMs"] >= 0
    assert timings["inputCommitToAssistantFirstStageMs"] is None
    assert timings["assistantFirstStageToAssistantTextFinalMs"] is None
    assert timings["assistantTextFinalToTtsFirstByteMs"] >= 0
    assert timings["ttsFirstByteToTurnCompletedMs"] >= 0
    assert timings["inputStartToTurnCompletedMs"] >= 0
    assert socket.audio
    assert "turn-1" not in session.turn_timings
    assert set(timings) == {
        "schemaVersion",
        "inputStartToInputCommitMs",
        "inputCommitToTranscriptFinalMs",
        "inputCommitToAssistantFirstStageMs",
        "assistantFirstStageToAssistantTextFinalMs",
        "assistantTextFinalToTtsFirstByteMs",
        "ttsFirstByteToTurnCompletedMs",
        "inputStartToTurnCompletedMs",
    }
    serialized = json.dumps(timings)
    assert "credential" not in serialized
    assert "turn-1" not in serialized
    assert "financial" not in serialized


@pytest.mark.asyncio
async def test_cancelled_turn_does_not_publish_final_timings() -> None:
    session = VoiceSession("session", "credential", services)
    socket = _Socket()
    await handle_control(socket, session, parse_event(json.loads(_control("session.configure", 0))))
    await handle_control(
        socket, session, parse_event(json.loads(_control("input.start", 1, "turn-1")))
    )
    await handle_control(
        socket, session, parse_event(json.loads(_control("input.commit", 2, "turn-1")))
    )
    await session.cancel()
    assert "turn-1" not in session.turn_timings
    assert not any(event["type"] == "turn.completed" for event in socket.events)
