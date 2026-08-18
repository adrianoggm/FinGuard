import math
from collections.abc import AsyncIterator, Callable

from finguard_voice_gateway.config import Settings

TONE_HZ = 220.0
AMPLITUDE = 6_000
SECONDS_PER_CHARACTER = 0.05


class FakeTextToSpeech:
    """A tone as long as the sentence, rather than the sentence's own bytes read as audio.

    Emitting the answer's UTF-8 bytes made a whole reply under a millisecond of sound: the browser
    played a click and barge-in had nothing left to interrupt by the time a person could react.
    A tone in the output format, lasting roughly as long as speaking the text would, makes audio
    playback and cancellation testable on any machine — while staying deterministic and needing no
    model. It does not pretend to be speech; that is what the Piper adapter is for.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()

    def synthesize(self, text: str, *, cancelled: Callable[[], bool]) -> AsyncIterator[bytes]:
        rate = self.settings.output_sample_rate
        per_chunk = max(1, rate * self.settings.tts_chunk_ms // 1000)
        total = int(rate * max(SECONDS_PER_CHARACTER * len(text.strip()), 0.5))

        async def chunks() -> AsyncIterator[bytes]:
            index = 0
            while index < total:
                if cancelled():
                    return
                frame = bytearray()
                for offset in range(index, min(index + per_chunk, total)):
                    value = int(AMPLITUDE * math.sin(2 * math.pi * TONE_HZ * offset / rate))
                    frame += value.to_bytes(2, "little", signed=True)
                index += per_chunk
                yield bytes(frame)

        return chunks()
