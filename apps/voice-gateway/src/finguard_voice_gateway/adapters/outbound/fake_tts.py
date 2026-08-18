from collections.abc import AsyncIterator, Callable


class FakeTextToSpeech:
    def synthesize(self, text: str, *, cancelled: Callable[[], bool]) -> AsyncIterator[bytes]:
        async def chunks() -> AsyncIterator[bytes]:
            for word in text.split():
                if cancelled():
                    return
                payload = word.encode("utf-8")
                yield payload if len(payload) % 2 == 0 else payload + b"\x00"

        return chunks()
