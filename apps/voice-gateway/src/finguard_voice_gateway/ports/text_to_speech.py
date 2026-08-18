from collections.abc import AsyncIterator, Callable
from typing import Protocol, runtime_checkable


class TextToSpeechError(RuntimeError):
    """Base error for local and hosted speech synthesis providers."""

    code = "TTS_PROVIDER_ERROR"


class TextToSpeechUnavailableError(TextToSpeechError):
    code = "TTS_UNAVAILABLE"


class TextToSpeechAudioError(TextToSpeechError):
    code = "TTS_INVALID_AUDIO"


class TextToSpeech(Protocol):
    def synthesize(self, text: str, *, cancelled: Callable[[], bool]) -> AsyncIterator[bytes]: ...


@runtime_checkable
class TextToSpeechLifecycle(Protocol):
    @property
    def is_closed(self) -> bool: ...

    async def warmup(self) -> None: ...

    async def close(self) -> None: ...
