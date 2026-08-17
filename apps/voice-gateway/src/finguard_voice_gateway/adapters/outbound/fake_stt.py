from collections.abc import Callable


class FakeSpeechToText:
    async def transcribe(self, audio: bytes, *, cancelled: Callable[[], bool]) -> str:
        if cancelled():
            return ""
        return audio.decode("utf-8") if audio else ""
