from collections.abc import Callable

from finguard_voice_gateway.config import Settings


class FakeSpeechToText:
    """Deterministic transcription for the protocol suite, and for a browser with no model.

    Two callers send two different shapes. The benchmark sends its sentence as UTF-8 bytes and
    wants it back verbatim, which is what keeps the protocol tests independent of any model. A
    browser sends microphone PCM16, which is not text: decoding it raised UnicodeDecodeError and
    every turn came back as a provider failure. So text is honoured when the audio *is* text, and
    real audio transcribes to a configured sentence rather than to an error.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()

    async def transcribe(self, audio: bytes, *, cancelled: Callable[[], bool]) -> str:
        if cancelled() or not audio:
            return ""
        try:
            return audio.decode("utf-8")
        except UnicodeDecodeError:
            return self.settings.fake_transcript
