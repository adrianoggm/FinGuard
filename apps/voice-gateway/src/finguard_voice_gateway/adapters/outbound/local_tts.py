from __future__ import annotations

import asyncio
import functools
import threading
import time
from collections.abc import AsyncIterator, Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from pathlib import Path
from typing import Any, Protocol, cast

from finguard_voice_gateway.config import Settings
from finguard_voice_gateway.ports.text_to_speech import (
    TextToSpeechAudioError,
    TextToSpeechUnavailableError,
)


class PiperEngine(Protocol):
    sample_rate: int

    def synthesize(
        self, text: str, *, speaker_id: int | None, length_scale: float | None
    ) -> Iterable[bytes]: ...


EngineFactory = Callable[[Settings], PiperEngine]
_END = object()


def resample_pcm16le(audio: bytes, source_rate: int, target_rate: int = 24_000) -> bytes:
    """Resample mono PCM16 with linear interpolation, without blocking or native deps."""
    if not audio:
        return b""
    if len(audio) % 2:
        raise TextToSpeechAudioError("Piper returned an incomplete PCM16 sample")
    if source_rate <= 0:
        raise TextToSpeechAudioError("Piper returned an invalid sample rate")
    if source_rate == target_rate:
        return audio
    import array

    source = array.array("h")
    source.frombytes(audio)
    if source.itemsize != 2:
        raise TextToSpeechAudioError("host does not support 16-bit PCM samples")
    target_length = max(1, round(len(source) * target_rate / source_rate))
    target = array.array("h")
    for index in range(target_length):
        position = index * source_rate / target_rate
        left = min(int(position), len(source) - 1)
        right = min(left + 1, len(source) - 1)
        fraction = position - left
        value = round(source[left] + (source[right] - source[left]) * fraction)
        target.append(max(-32768, min(32767, value)))
    if __import__("sys").byteorder != "little":
        target.byteswap()
    return target.tobytes()


class PiperVoiceEngine:
    def __init__(self, voice: Any) -> None:
        self.voice = voice
        self.sample_rate = int(voice.config.sample_rate)

    def synthesize(
        self, text: str, *, speaker_id: int | None, length_scale: float | None
    ) -> Iterable[bytes]:
        # Piper >= 1.3 yields AudioChunk objects. Keeping that SDK detail inside
        # the adapter means the rest of the gateway only ever sees raw PCM16.
        from piper import SynthesisConfig

        synthesis = SynthesisConfig(speaker_id=speaker_id, length_scale=length_scale)

        def audio_bytes() -> Iterable[bytes]:
            for chunk in self.voice.synthesize(text, syn_config=synthesis):
                if chunk.sample_width != 2 or chunk.sample_channels != 1:
                    raise TextToSpeechAudioError("Piper returned unsupported audio format")
                if chunk.sample_rate != self.sample_rate:
                    raise TextToSpeechAudioError("Piper changed sample rate during synthesis")
                yield cast(bytes, chunk.audio_int16_bytes)

        return audio_bytes()


def piper_factory(settings: Settings) -> PiperEngine:
    try:
        from piper import PiperVoice
    except ImportError as exc:
        raise TextToSpeechUnavailableError(
            "local TTS requires the optional piper-tts dependency"
        ) from exc
    model = Path(settings.tts_model_path).expanduser()
    if not model.is_file():
        raise TextToSpeechUnavailableError(f"local TTS model does not exist: {model}")
    config = Path(settings.tts_config_path).expanduser() if settings.tts_config_path else None
    try:
        voice = PiperVoice.load(
            model,
            config_path=config,
            use_cuda=settings.tts_device == "cuda",
        )
    except Exception as exc:
        raise TextToSpeechUnavailableError("local TTS model could not be loaded") from exc
    return PiperVoiceEngine(voice)


class LocalTextToSpeech:
    """Piper adapter: inference runs in a worker and output is bounded PCM16 streaming."""

    def __init__(self, settings: Settings, factory: EngineFactory = piper_factory) -> None:
        self.settings = settings
        self.factory = factory
        self._engine: PiperEngine | None = None
        self._load_lock = asyncio.Lock()
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._reapers: set[asyncio.Future[None]] = set()
        self._closed = False

    @property
    def is_closed(self) -> bool:
        return self._closed

    async def _get_engine(self) -> PiperEngine:
        if self._closed:
            raise TextToSpeechUnavailableError("local TTS is closed")
        if self._engine is None:
            async with self._load_lock:
                if self._engine is None:
                    loop = asyncio.get_running_loop()
                    self._engine = await loop.run_in_executor(
                        self._executor, functools.partial(self.factory, self.settings)
                    )
        return self._engine

    def synthesize(self, text: str, *, cancelled: Callable[[], bool]) -> AsyncIterator[bytes]:
        async def chunks() -> AsyncIterator[bytes]:
            if not text.strip():
                return
            if cancelled():
                return
            engine = await self._get_engine()
            output = asyncio.Queue[object](maxsize=2)
            stop = threading.Event()
            loop = asyncio.get_running_loop()

            def put(item: object) -> None:
                while not stop.is_set():
                    pending_put = asyncio.run_coroutine_threadsafe(output.put(item), loop)
                    try:
                        pending_put.result(timeout=0.1)
                        return
                    except FutureTimeoutError:
                        pending_put.cancel()
                        continue

            def produce() -> None:
                emitted = False
                pending = bytearray()
                target_bytes = self.settings.tts_chunk_ms * 24_000 * 2 // 1000
                try:
                    for raw in engine.synthesize(
                        text,
                        speaker_id=self.settings.tts_speaker,
                        length_scale=self.settings.tts_length_scale,
                    ):
                        if stop.is_set() or cancelled():
                            return
                        converted = resample_pcm16le(raw, engine.sample_rate)
                        pending.extend(converted)
                        while len(pending) >= target_bytes:
                            put(bytes(pending[:target_bytes]))
                            del pending[:target_bytes]
                            emitted = True
                    if pending and not stop.is_set() and not cancelled():
                        put(bytes(pending))
                        emitted = True
                    if not emitted and not stop.is_set():
                        put(TextToSpeechAudioError("local TTS returned no audio"))
                except BaseException as exc:
                    if not stop.is_set():
                        put(exc)
                finally:
                    put(_END)

            native = loop.run_in_executor(self._executor, produce)
            self._reapers.add(native)
            native.add_done_callback(self._reapers.discard)
            try:
                deadline = time.monotonic() + self.settings.tts_timeout_seconds
                while True:
                    if cancelled():
                        return
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TextToSpeechUnavailableError("local TTS timed out")
                    try:
                        item = await asyncio.wait_for(
                            output.get(), min(remaining, 0.05)
                        )
                    except TimeoutError:
                        continue
                    if item is _END:
                        return
                    if isinstance(item, BaseException):
                        raise item
                    if isinstance(item, bytes) and item:
                        yield item
            finally:
                stop.set()

        return chunks()

    async def warmup(self) -> None:
        await self._get_engine()

    async def close(self) -> None:
        self._closed = True
        for future in tuple(self._reapers):
            await asyncio.shield(future)
        await asyncio.get_running_loop().run_in_executor(
            None, functools.partial(self._executor.shutdown, wait=True, cancel_futures=True)
        )
