from collections.abc import Callable
from typing import Protocol, runtime_checkable


class SpeechToTextError(RuntimeError):
    code = "STT_PROVIDER_ERROR"


class SpeechToTextUnavailableError(SpeechToTextError):
    code = "STT_UNAVAILABLE"


class SpeechToTextTimeoutError(SpeechToTextError):
    code = "STT_TIMEOUT"


class SpeechToTextEmptyError(SpeechToTextError):
    code = "STT_EMPTY"


class SpeechToTextAudioError(SpeechToTextError):
    code = "STT_INVALID_AUDIO"


class SpeechToTextEmptyAudioError(SpeechToTextError):
    code = "STT_EMPTY_AUDIO"


class SpeechToTextAudioTooLongError(SpeechToTextError):
    code = "STT_AUDIO_TOO_LONG"


class SpeechToText(Protocol):
    async def transcribe(self, audio: bytes, *, cancelled: Callable[[], bool]) -> str: ...


@runtime_checkable
class SpeechToTextLifecycle(Protocol):
    @property
    def is_closed(self) -> bool: ...

    async def close(self) -> None: ...

    async def warmup(self) -> None: ...
