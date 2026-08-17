from collections.abc import AsyncIterator, Callable
from typing import Protocol


class TextToSpeech(Protocol):
    def synthesize(self, text: str, *, cancelled: Callable[[], bool]) -> AsyncIterator[bytes]: ...
