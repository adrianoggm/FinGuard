"""Manual local-STT benchmark; never prints audio or transcript contents."""

import argparse
import asyncio
import time
import wave

from finguard_voice_gateway.adapters.outbound.local_stt import LocalSpeechToText
from finguard_voice_gateway.config import Settings


def read_wav(path: str) -> tuple[bytes, float]:
    with wave.open(path, "rb") as wav:
        if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or wav.getframerate() != 16_000:
            raise ValueError("WAV must be PCM16 LE mono 16 kHz")
        frames = wav.readframes(wav.getnframes())
        return frames, wav.getnframes() / wav.getframerate()


async def run(args: argparse.Namespace) -> None:
    audio, duration = read_wav(args.wav)
    settings = Settings(
        stt_mode="local",
        stt_model=args.model,
        stt_device=args.device,
        stt_compute_type=args.compute_type,
        stt_language=args.language,
        stt_local_files_only=args.local_files_only,
    )
    stt = LocalSpeechToText(settings)
    try:
        started = time.perf_counter()
        await stt.warmup()
        loaded = time.perf_counter() - started
        started = time.perf_counter()
        transcript = await stt.transcribe(audio, cancelled=lambda: False)
        inference = time.perf_counter() - started
        print(
            f"model={args.model} requested_device={stt.requested_device} "
            f"effective_device={stt.effective_device} audio_seconds={duration:.3f}"
        )
        print(f"model_load_seconds={loaded:.3f} inference_seconds={inference:.3f}")
        print(f"real_time_factor={inference / duration:.3f}")
        print(f"transcript_chars={len(transcript)}")
    finally:
        await stt.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("wav", help="PCM16 LE mono 16 kHz WAV")
    parser.add_argument("--model", default="small")
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument("--compute-type", default="int8")
    parser.add_argument("--language", default="es")
    parser.add_argument(
        "--local-files-only",
        action="store_true",
        help="fail if the model is not already cached; prevents model downloads",
    )
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
