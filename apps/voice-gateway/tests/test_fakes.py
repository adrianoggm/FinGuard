import asyncio

from finguard_voice_gateway.adapters.outbound.fake_stt import FakeSpeechToText
from finguard_voice_gateway.adapters.outbound.fake_tts import FakeTextToSpeech
from finguard_voice_gateway.config import Settings


def collect(source: object) -> list[bytes]:
    async def drain() -> list[bytes]:
        return [chunk async for chunk in source]  # type: ignore[union-attr]

    return asyncio.run(drain())


def test_fake_tts_emits_even_length_pcm16_chunks() -> None:
    chunks = collect(FakeTextToSpeech(Settings()).synthesize("hola voz", cancelled=lambda: False))
    assert chunks
    assert all(len(chunk) % 2 == 0 for chunk in chunks)


def test_fake_tts_lasts_long_enough_to_be_heard_and_interrupted() -> None:
    # A fake that returned the answer's own bytes as audio produced under a millisecond of sound for
    # a whole sentence: the browser played a click, and barge-in had nothing to interrupt. Length
    # proportional to the text is what makes "I heard it" and "I cut it off" testable without a GPU.
    settings = Settings()
    chunks = collect(
        FakeTextToSpeech(settings).synthesize(
            "Has gastado 1.234,56 euros este mes", cancelled=lambda: False
        )
    )
    samples = sum(len(chunk) for chunk in chunks) // 2
    assert samples / settings.output_sample_rate > 1.0
    assert any(chunk.count(b"\x00\x00") < len(chunk) // 2 for chunk in chunks), (
        "must not be silence"
    )


def test_fake_tts_stops_when_cancelled() -> None:
    served = 0

    def cancelled() -> bool:
        nonlocal served
        served += 1
        return served > 3

    chunks = collect(
        FakeTextToSpeech(Settings()).synthesize("una frase larga", cancelled=cancelled)
    )
    assert 0 < len(chunks) <= 3


def test_fake_stt_reads_text_bytes_and_survives_real_audio() -> None:
    # Two callers, two shapes. The benchmark sends its sentence as UTF-8 bytes and expects it back
    # verbatim, which is what makes the protocol suite deterministic. A browser sends microphone
    # PCM16, which is not UTF-8, and decoding it raised UnicodeDecodeError — the page reported
    # "voice provider failed" for every single turn.
    stt = FakeSpeechToText(Settings())
    assert asyncio.run(stt.transcribe("cuánto gasté".encode(), cancelled=lambda: False)) == (
        "cuánto gasté"
    )
    microphone = bytes(range(256)) * 4
    assert asyncio.run(stt.transcribe(microphone, cancelled=lambda: False)) == (
        Settings().fake_transcript
    )
    assert asyncio.run(stt.transcribe(b"", cancelled=lambda: False)) == ""
    assert asyncio.run(stt.transcribe(microphone, cancelled=lambda: True)) == ""


def test_fake_transcript_is_configurable() -> None:
    stt = FakeSpeechToText(Settings(fake_transcript="hazle un bizum de 20 euros a Marta"))
    spoken = asyncio.run(stt.transcribe(bytes(range(256)), cancelled=lambda: False))
    assert spoken == "hazle un bizum de 20 euros a Marta"
