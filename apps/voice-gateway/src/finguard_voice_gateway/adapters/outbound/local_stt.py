from __future__ import annotations

import asyncio
import functools
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Protocol

from finguard_voice_gateway.config import Settings
from finguard_voice_gateway.ports.speech_to_text import (
    SpeechToTextAudioError,
    SpeechToTextAudioTooLongError,
    SpeechToTextEmptyAudioError,
    SpeechToTextEmptyError,
    SpeechToTextError,
    SpeechToTextLifecycle,
    SpeechToTextTimeoutError,
    SpeechToTextUnavailableError,
)


class WhisperEngine(Protocol):
    def transcribe(
        self, samples: Any, *, language: str, beam_size: int, vad_filter: bool
    ) -> str: ...
    def close(self) -> None: ...


EngineFactory = Callable[[Settings, str], WhisperEngine]


def pcm16le_to_samples(audio: bytes, *, max_seconds: int) -> Any:
    if not audio:
        raise SpeechToTextEmptyAudioError("PCM16 audio is empty")
    if len(audio) % 2:
        raise SpeechToTextAudioError("PCM16 audio must contain whole samples")
    if len(audio) > max_seconds * 16_000 * 2:
        raise SpeechToTextAudioTooLongError("PCM16 audio exceeds the configured duration")
    try:
        import numpy as np
    except ImportError as exc:
        raise SpeechToTextUnavailableError("local STT requires numpy") from exc
    values = np.frombuffer(audio, dtype="<i2")
    return (values.astype(np.float32) / np.float32(32768.0)).reshape(-1)


def _normalise(text: str) -> str:
    return " ".join(text.split()).strip()


@dataclass
class FasterWhisperEngine:
    model: Any

    def transcribe(self, samples: Any, *, language: str, beam_size: int, vad_filter: bool) -> str:
        segments, _info = self.model.transcribe(
            samples, language=language, beam_size=beam_size, vad_filter=vad_filter
        )
        return " ".join(str(segment.text) for segment in segments)

    @property
    def effective_device(self) -> str | None:
        value = getattr(self.model, "device", None)
        if value is None:
            backend = getattr(self.model, "model", None)
            value = getattr(backend, "device", None)
        return str(value) if value is not None else None

    def close(self) -> None:
        del self.model


def faster_whisper_factory(settings: Settings, device: str) -> WhisperEngine:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise SpeechToTextUnavailableError(
            "local STT requires the optional faster-whisper dependency"
        ) from exc
    try:
        return FasterWhisperEngine(
            WhisperModel(
                settings.stt_model,
                device=device,
                compute_type=settings.stt_compute_type,
                local_files_only=settings.stt_local_files_only,
            )
        )
    except Exception as exc:
        raise SpeechToTextUnavailableError(
            f"local STT model could not be loaded for device {device}"
        ) from exc


class LocalSpeechToText(SpeechToTextLifecycle):
    """Bounded local inference; cancellation fences native work but cannot abort it."""

    def __init__(self, settings: Settings, factory: EngineFactory = faster_whisper_factory) -> None:
        self.settings = settings
        self.requested_device = settings.stt_device
        self.factory = factory
        self._engine: WhisperEngine | None = None
        self._load_lock = asyncio.Lock()
        self._slots = asyncio.Semaphore(settings.stt_max_concurrency)
        self._executor = ThreadPoolExecutor(max_workers=settings.stt_max_concurrency)
        self._reapers: set[asyncio.Task[None]] = set()
        self._closed = False
        self._close_task: asyncio.Task[None] | None = None

    @property
    def is_closed(self) -> bool:
        return self._closed

    @property
    def effective_device(self) -> str:
        value = getattr(self._engine, "effective_device", None)
        return str(value) if value is not None else self.requested_device

    async def _get_engine(self) -> WhisperEngine:
        if self._closed:
            raise SpeechToTextUnavailableError("local STT is closed")
        if self._engine is None:
            async with self._load_lock:
                if self._engine is None:
                    loop = asyncio.get_running_loop()
                    self._engine = await loop.run_in_executor(
                        self._executor,
                        functools.partial(self.factory, self.settings, self.requested_device),
                    )
        return self._engine

    def _track_reaper(self, native: asyncio.Future[str]) -> None:
        task = asyncio.create_task(self._reap(native))
        self._reapers.add(task)
        task.add_done_callback(self._reapers.discard)

    async def _reap(self, native: asyncio.Future[str]) -> None:
        try:
            await asyncio.shield(native)
        except asyncio.CancelledError:
            await asyncio.shield(native)
            raise
        except BaseException:
            pass
        finally:
            self._slots.release()

    async def transcribe(self, audio: bytes, *, cancelled: Callable[[], bool]) -> str:
        samples = pcm16le_to_samples(audio, max_seconds=self.settings.max_utterance_seconds)
        if cancelled():
            return ""
        await self._slots.acquire()
        native: asyncio.Future[str] | None = None
        slot_owned = True
        try:
            if cancelled():
                self._slots.release()
                slot_owned = False
                return ""
            engine = await self._get_engine()
            if cancelled():
                self._slots.release()
                slot_owned = False
                return ""
            loop = asyncio.get_running_loop()
            call = functools.partial(
                engine.transcribe,
                samples,
                language=self.settings.stt_language,
                beam_size=self.settings.stt_beam_size,
                vad_filter=self.settings.stt_vad_filter,
            )
            native = loop.run_in_executor(self._executor, call)
            self._track_reaper(native)
            slot_owned = False
            try:
                text = await asyncio.wait_for(
                    asyncio.shield(native), self.settings.stt_timeout_seconds
                )
            except (asyncio.CancelledError, TimeoutError):
                raise
        except asyncio.CancelledError:
            raise
        except TimeoutError as exc:
            raise SpeechToTextTimeoutError("local STT timed out") from exc
        except SpeechToTextError:
            raise
        except Exception as exc:
            raise SpeechToTextError("local STT inference failed") from exc
        finally:
            if slot_owned:
                self._slots.release()
        if cancelled():
            return ""
        final = _normalise(text)
        if not final:
            raise SpeechToTextEmptyError("local STT returned no transcript")
        return final

    async def warmup(self) -> None:
        await self._get_engine()

    async def close(self) -> None:
        if self._close_task is None:
            self._closed = True
            self._close_task = asyncio.create_task(self._finish_close())
        await asyncio.shield(self._close_task)

    async def _finish_close(self) -> None:
        if self._reapers:
            await asyncio.gather(
                *(asyncio.shield(task) for task in tuple(self._reapers)),
                return_exceptions=True,
            )
        if self._engine is not None:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(self._executor, self._engine.close)
        await asyncio.get_running_loop().run_in_executor(
            None, functools.partial(self._executor.shutdown, wait=True, cancel_futures=True)
        )
