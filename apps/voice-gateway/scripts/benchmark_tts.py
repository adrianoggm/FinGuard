"""Manual local-TTS benchmark; it never writes or prints generated audio."""

import argparse
import asyncio
import time

from finguard_voice_gateway.adapters.outbound.local_tts import LocalTextToSpeech
from finguard_voice_gateway.config import Settings


async def run(args: argparse.Namespace) -> None:
    settings = Settings(
        tts_mode="local",
        tts_model_path=args.model,
        tts_config_path=args.config,
        tts_device=args.device,
        tts_speaker=args.speaker,
        tts_length_scale=args.length_scale,
    )
    tts = LocalTextToSpeech(settings)
    try:
        started = time.perf_counter()
        await tts.warmup()
        loaded = time.perf_counter() - started
        started = time.perf_counter()
        chunks = [chunk async for chunk in tts.synthesize(args.text, cancelled=lambda: False)]
        synthesis = time.perf_counter() - started
        total_bytes = sum(map(len, chunks))
        audio_seconds = total_bytes / (24_000 * 2)
        print(f"model={args.model} device={args.device} audio_seconds={audio_seconds:.3f}")
        print(f"model_load_seconds={loaded:.3f} synthesis_seconds={synthesis:.3f}")
        print(
            f"real_time_factor={synthesis / audio_seconds:.3f}"
            if audio_seconds
            else "no_audio=true"
        )
        print(f"chunks={len(chunks)} bytes={total_bytes}")
    finally:
        await tts.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("text")
    parser.add_argument("--model", required=True, help="local Piper .onnx voice model")
    parser.add_argument("--config", default="", help="optional Piper .onnx.json path")
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument("--speaker", type=int)
    parser.add_argument("--length-scale", type=float)
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
