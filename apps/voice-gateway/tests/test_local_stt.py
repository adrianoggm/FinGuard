import asyncio
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import pytest

from finguard_voice_gateway.adapters.outbound.local_stt import (
    FasterWhisperEngine,
    LocalSpeechToText,
    WhisperEngine,
    pcm16le_to_samples,
)
from finguard_voice_gateway.bootstrap import build_services, ensure_services_open
from finguard_voice_gateway.config import Settings
from finguard_voice_gateway.ports.speech_to_text import (
    SpeechToTextAudioError,
    SpeechToTextAudioTooLongError,
    SpeechToTextEmptyAudioError,
    SpeechToTextEmptyError,
    SpeechToTextLifecycle,
    SpeechToTextTimeoutError,
)


def settings(**kwargs: Any) -> Settings:
    return Settings(stt_mode="local", **kwargs)


def test_pcm16_conversion_has_float32_mono_boundary() -> None:
    samples = pcm16le_to_samples(b"\x00\x80\xff\x7f", max_seconds=30)
    assert isinstance(samples, np.ndarray)
    assert samples.dtype == np.float32
    assert samples.shape == (2,)
    np.testing.assert_allclose(samples, [-1.0, 32767 / 32768])


def test_pcm_validation_has_typed_codes() -> None:
    with pytest.raises(SpeechToTextEmptyAudioError) as empty:
        pcm16le_to_samples(b"", max_seconds=30)
    with pytest.raises(SpeechToTextAudioError) as odd:
        pcm16le_to_samples(b"x", max_seconds=30)
    with pytest.raises(SpeechToTextAudioTooLongError) as long:
        pcm16le_to_samples(b"\x00\x00" * 16_000 * 31, max_seconds=30)
    assert empty.value.code == "STT_EMPTY_AUDIO"
    assert odd.value.code == "STT_INVALID_AUDIO"
    assert long.value.code == "STT_AUDIO_TOO_LONG"


class CaptureModel:
    device = "cuda"

    def __init__(self) -> None:
        self.samples: Any = None
        self.kwargs: dict[str, Any] = {}

    def transcribe(self, samples: Any, **kwargs: Any) -> tuple[list[Any], None]:
        self.samples = samples
        self.kwargs = kwargs
        return [], None


def test_faster_whisper_boundary_passes_ndarray_and_options() -> None:
    model = CaptureModel()
    FasterWhisperEngine(model).transcribe(
        np.array([0.25, -0.5], dtype=np.float32), language="es", beam_size=3, vad_filter=True
    )
    assert isinstance(model.samples, np.ndarray)
    assert model.samples.dtype == np.float32
    assert model.samples.shape == (2,)
    assert model.kwargs == {"language": "es", "beam_size": 3, "vad_filter": True}


def test_effective_device_checks_nested_ctranslate_backend() -> None:
    class Backend:
        device = "cuda:0"

    class Wrapper:
        model = Backend()

        def transcribe(self, samples: Any, **kwargs: Any) -> tuple[list[Any], None]:
            return [], None

        def close(self) -> None:
            pass

    assert FasterWhisperEngine(Wrapper()).effective_device == "cuda:0"


@dataclass
class Engine:
    result: str = "  hola   mundo  "
    calls: int = 0
    entered: threading.Event | None = None
    release: threading.Event | None = None

    def transcribe(
        self, samples: Sequence[float], *, language: str, beam_size: int, vad_filter: bool
    ) -> str:
        assert (language, beam_size, vad_filter) == ("es", 5, True)
        self.calls += 1
        if self.entered:
            self.entered.set()
        if self.release:
            self.release.wait()
        return self.result

    def close(self) -> None:
        pass


def factory_for(engine: Engine) -> Any:
    def factory(_settings: Settings, _device: str) -> WhisperEngine:
        return engine

    return factory


@pytest.mark.asyncio
async def test_lazy_load_once_normalises_and_passes_options() -> None:
    engine = Engine()
    loads = 0

    def factory(_settings: Settings, _device: str) -> WhisperEngine:
        nonlocal loads
        loads += 1
        return engine

    stt = LocalSpeechToText(settings(), factory)
    try:
        assert await stt.transcribe(b"\x00\x00\x01\x00", cancelled=lambda: False) == "hola mundo"
        assert await stt.transcribe(b"\x00\x00\x01\x00", cancelled=lambda: False) == "hola mundo"
        assert loads == 1
    finally:
        await stt.close()


@pytest.mark.asyncio
async def test_empty_and_timeout_are_typed() -> None:
    empty = LocalSpeechToText(settings(), factory_for(Engine(result="")))
    with pytest.raises(SpeechToTextEmptyError):
        await empty.transcribe(b"\x00\x00\x01\x00", cancelled=lambda: False)
    await empty.close()

    entered = threading.Event()
    release = threading.Event()
    slow = LocalSpeechToText(
        settings(stt_timeout_seconds=0.01), factory_for(Engine(entered=entered, release=release))
    )
    with pytest.raises(SpeechToTextTimeoutError):
        await slow.transcribe(b"\x00\x00\x01\x00", cancelled=lambda: False)
    assert not release.is_set()
    release.set()
    await slow.close()


@pytest.mark.asyncio
async def test_timeout_keeps_slot_until_native_finishes() -> None:
    entered = threading.Event()
    release = threading.Event()
    engine = Engine(entered=entered, release=release)
    # One timeout has two jobs here and they pull in opposite directions: it must fire for the
    # first call and must not fire for the second. The second one waits twice — for the slot, and
    # then for the single executor thread to be handed its job, because `max_workers` follows
    # `stt_max_concurrency` and that thread is still returning call #1. At 10 ms the handoff lost
    # that race on Windows every time, and the test failed reporting the adapter had released the
    # slot early when the evidence says it had not. Production waits 45 s; the bar here only has
    # to clear a thread handoff by a wide margin.
    stt = LocalSpeechToText(
        settings(stt_timeout_seconds=0.2, stt_max_concurrency=1), factory_for(engine)
    )
    try:
        first = asyncio.create_task(stt.transcribe(b"\x00\x00", cancelled=lambda: False))
        await asyncio.to_thread(entered.wait)
        with pytest.raises(SpeechToTextTimeoutError):
            await first
        second = asyncio.create_task(stt.transcribe(b"\x00\x00", cancelled=lambda: False))
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(second), timeout=0.01)
        assert not second.done()
        assert engine.calls == 1
        release.set()
        assert await second == "hola mundo"
        assert engine.calls == 2
    finally:
        release.set()
        await stt.close()


@pytest.mark.asyncio
async def test_close_cancellation_does_not_release_reaper_slot_early() -> None:
    entered = threading.Event()
    release = threading.Event()
    engine = Engine(entered=entered, release=release)
    stt = LocalSpeechToText(settings(), factory_for(engine))
    request = asyncio.create_task(stt.transcribe(b"\x00\x00", cancelled=lambda: False))
    await asyncio.to_thread(entered.wait)
    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request
    close = asyncio.create_task(stt.close())
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(asyncio.shield(close), timeout=0.01)
    # close normally remains pending while the native call is fenced; cancellation is tested below.
    assert close.done() is False
    close.cancel()
    with pytest.raises(asyncio.CancelledError):
        await close
    assert stt._reapers and all(not task.done() for task in stt._reapers)
    release.set()
    await stt.close()


@pytest.mark.asyncio
async def test_cancel_returns_before_native_finishes_and_slot_is_reaped() -> None:
    entered = threading.Event()
    release = threading.Event()
    engine = Engine(entered=entered, release=release)
    stt = LocalSpeechToText(settings(stt_max_concurrency=1), factory_for(engine))
    first = asyncio.create_task(stt.transcribe(b"\x00\x00", cancelled=lambda: False))
    await asyncio.to_thread(entered.wait)
    second = asyncio.create_task(stt.transcribe(b"\x00\x00", cancelled=lambda: False))
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert not second.done()
    release.set()
    assert await second == "hola mundo"
    assert engine.calls == 2
    await stt.close()


def test_fake_mode_ignores_local_stt_validation() -> None:
    Settings(
        stt_mode="fake",
        stt_model="",
        stt_beam_size=0,
        stt_timeout_seconds=0,
        stt_compute_type="not-a-real-type",
        stt_device="not-a-device",
    )
    with pytest.raises(ValueError):
        Settings(stt_mode="local", stt_compute_type="not-a-real-type")


@pytest.mark.asyncio
async def test_ensure_services_open_rejects_reused_closed_local_stt() -> None:
    services = build_services(Settings(stt_mode="local"))
    assert isinstance(services.stt, SpeechToTextLifecycle)
    await services.stt.close()
    with pytest.raises(RuntimeError, match="speech-to-text resources"):
        ensure_services_open(services)
