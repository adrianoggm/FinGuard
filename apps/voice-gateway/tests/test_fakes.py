import asyncio

from finguard_voice_gateway.adapters.outbound.fake_tts import FakeTextToSpeech


def test_fake_tts_emits_even_length_pcm16_chunks() -> None:
    async def collect() -> list[bytes]:
        return [
            chunk
            async for chunk in FakeTextToSpeech().synthesize("hola voz", cancelled=lambda: False)
        ]

    chunks = asyncio.run(collect())
    assert chunks
    assert all(len(chunk) % 2 == 0 for chunk in chunks)
