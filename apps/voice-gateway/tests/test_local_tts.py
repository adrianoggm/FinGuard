import asyncio
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, cast

import pytest

from finguard_voice_gateway.adapters.outbound.local_tts import (
    LocalTextToSpeech,
    PiperVoiceEngine,
    resample_pcm16le,
)
from finguard_voice_gateway.bootstrap import build_services, ensure_services_open
from finguard_voice_gateway.config import Settings
from finguard_voice_gateway.ports.text_to_speech import (
    TextToSpeechAudioError,
    TextToSpeechUnavailableError,
)


class FakeEngine:
    sample_rate = 16_000

    def __init__(self, chunks: Iterable[bytes] = (b"\x00\x00" * 400,)) -> None:
        self.chunks = tuple(chunks)

    def synthesize(
        self, text: str, *, speaker_id: int | None, length_scale: float | None
    ) -> Iterable[bytes]:
        return iter(self.chunks)


@dataclass
class FakeAudioChunk:
    audio_int16_bytes: bytes
    sample_rate: int = 22_050
    sample_width: int = 2
    sample_channels: int = 1


class FakePiperVoice:
    class Config:
        sample_rate = 22_050

    config = Config()

    def __init__(self) -> None:
        self.syn_config: Any | None = None

    def synthesize(self, text: str, *, syn_config: object) -> Iterable[FakeAudioChunk]:
        self.syn_config = syn_config
        return iter((FakeAudioChunk(b"\x01\x00" * 10),))


def local_settings(**kwargs: object) -> Settings:
    values: dict[str, Any] = {"tts_mode": "local", "tts_model_path": "voice.onnx"}
    values.update(cast(dict[str, Any], kwargs))
    return Settings(**values)


def test_resampler_returns_pcm16_24khz() -> None:
    source = (b"\x00\x00" * 160)  # 10 ms at 16 kHz
    converted = resample_pcm16le(source, 16_000)
    assert len(converted) == 480
    assert len(converted) % 2 == 0


def test_resampler_rejects_incomplete_sample() -> None:
    with pytest.raises(TextToSpeechAudioError):
        resample_pcm16le(b"\x00", 16_000)


def test_piper_engine_uses_audio_chunk_api() -> None:
    voice = FakePiperVoice()
    engine = PiperVoiceEngine(voice)
    chunks = list(engine.synthesize("hola", speaker_id=2, length_scale=1.1))
    assert chunks == [b"\x01\x00" * 10]
    assert voice.syn_config is not None
    assert voice.syn_config.speaker_id == 2
    assert voice.syn_config.length_scale == 1.1


@pytest.mark.asyncio
async def test_local_tts_chunks_are_nonempty_pcm16_24khz() -> None:
    tts = LocalTextToSpeech(local_settings(), lambda _: FakeEngine((b"\x01\x00" * 400,)))
    chunks = [chunk async for chunk in tts.synthesize("hola", cancelled=lambda: False)]
    assert chunks
    assert all(chunk and len(chunk) % 2 == 0 for chunk in chunks)
    assert len(chunks[0]) == 960  # 20 ms at 24 kHz, except possibly the final chunk
    await tts.close()


@pytest.mark.asyncio
async def test_local_tts_cancellation_is_cooperative() -> None:
    cancelled = False
    tts = LocalTextToSpeech(
        local_settings(), lambda _: FakeEngine(b"\x01\x00" * 4000 for _ in range(5))
    )
    stream = tts.synthesize("hola", cancelled=lambda: cancelled)
    first = await anext(stream)
    assert first
    cancelled = True
    assert [chunk async for chunk in stream] == []
    await tts.close()


@pytest.mark.asyncio
async def test_local_tts_cancels_while_waiting_for_first_audio() -> None:
    release = threading.Event()

    class SlowEngine(FakeEngine):
        def synthesize(
            self, text: str, *, speaker_id: int | None, length_scale: float | None
        ) -> Iterable[bytes]:
            release.wait(timeout=1)
            return iter((b"\x00\x00",))

    cancelled = False
    tts = LocalTextToSpeech(local_settings(), lambda _: SlowEngine())
    stream = tts.synthesize("hola", cancelled=lambda: cancelled)
    pending: asyncio.Future[bytes] = asyncio.ensure_future(anext(stream))
    await asyncio.sleep(0.01)
    cancelled = True
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(pending, 0.2)
    release.set()
    await tts.close()


@pytest.mark.asyncio
async def test_local_tts_reports_empty_provider_audio() -> None:
    tts = LocalTextToSpeech(local_settings(), lambda _: FakeEngine(()))
    with pytest.raises(TextToSpeechAudioError, match="no audio"):
        _ = [chunk async for chunk in tts.synthesize("hola", cancelled=lambda: False)]
    await tts.close()


@pytest.mark.asyncio
async def test_local_tts_missing_optional_dependency_is_sanitized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from finguard_voice_gateway.adapters.outbound import local_tts

    monkeypatch.setattr(local_tts, "piper_factory", lambda _: (_ for _ in ()).throw(
        TextToSpeechUnavailableError("missing")
    ))
    tts = LocalTextToSpeech(local_settings(), local_tts.piper_factory)
    with pytest.raises(TextToSpeechUnavailableError):
        _ = [chunk async for chunk in tts.synthesize("hola", cancelled=lambda: False)]
    await tts.close()


def test_fake_is_default_and_local_is_selected() -> None:
    from finguard_voice_gateway.adapters.outbound.fake_tts import FakeTextToSpeech
    from finguard_voice_gateway.adapters.outbound.local_tts import LocalTextToSpeech

    assert isinstance(build_services(Settings()).tts, FakeTextToSpeech)
    assert isinstance(build_services(local_settings()).tts, LocalTextToSpeech)


def test_local_settings_require_model() -> None:
    with pytest.raises(ValueError, match="TTS_MODEL_PATH"):
        Settings(tts_mode="local")


def test_closed_local_tts_is_rejected_by_bootstrap() -> None:
    services = build_services(local_settings())
    asyncio.run(services.tts.close())  # type: ignore[attr-defined]
    with pytest.raises(RuntimeError, match="text-to-speech"):
        ensure_services_open(services)
