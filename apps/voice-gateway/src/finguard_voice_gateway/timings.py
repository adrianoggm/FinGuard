from collections.abc import Callable
from dataclasses import dataclass, field

MonotonicClock = Callable[[], float]


@dataclass
class TurnTimings:
    """Monotonic turn milestones; only derived durations leave the gateway."""

    clock: MonotonicClock
    _marks: dict[str, float] = field(default_factory=dict)

    def mark(self, name: str) -> None:
        self._marks.setdefault(name, self.clock())

    def durations(self) -> dict[str, float | None]:
        def elapsed(start: str, end: str) -> float | None:
            started = self._marks.get(start)
            finished = self._marks.get(end)
            if started is None or finished is None:
                return None
            return round(max(0.0, (finished - started) * 1000), 3)

        return {
            "inputStartToInputCommitMs": elapsed("input_start", "input_commit"),
            "inputCommitToTranscriptFinalMs": elapsed("input_commit", "transcript_final"),
            "inputCommitToAssistantFirstStageMs": elapsed("input_commit", "assistant_first_stage"),
            "assistantFirstStageToAssistantTextFinalMs": elapsed(
                "assistant_first_stage", "assistant_text_final"
            ),
            "assistantTextFinalToTtsFirstByteMs": elapsed("assistant_text_final", "tts_first_byte"),
            "ttsFirstByteToTurnCompletedMs": elapsed("tts_first_byte", "turn_completed"),
            "inputStartToTurnCompletedMs": elapsed("input_start", "turn_completed"),
        }
