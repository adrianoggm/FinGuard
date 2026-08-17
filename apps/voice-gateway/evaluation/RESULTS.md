# Voice latency — local end-to-end validation

Measured on 17 August 2026 with one synthetic Spanish utterance, two warmup turns and twenty
recorded turns. Every recorded turn crossed the complete local path:

```text
PCM16 → Faster Whisper → voice-gateway → orchestrator → Ollama → banking
      → orchestrator answer → Piper → streamed PCM16
```

The orchestrator logs confirmed `PLANNED` for every accepted sample and HTTP 200 from both Ollama
and banking. The runner rejects provider/banking error outcomes, missing audio, malformed timing
schemas and events belonging to another turn.

## Result

| milestone | P50 | P95 | samples |
| --- | ---: | ---: | ---: |
| input start → input commit | 1.511 ms | 1.913 ms | 20 |
| input commit → transcript final | 769.634 ms | 874.648 ms | 20 |
| input commit → first orchestrator stage | 777.673 ms | 881.834 ms | 20 |
| first stage → assistant text final | 3,938.925 ms | 3,983.803 ms | 20 |
| assistant text final → first TTS byte | 80.080 ms | 87.641 ms | 20 |
| first TTS byte → turn completed | 126.482 ms | 131.229 ms | 20 |
| input start → turn completed | 4,927.124 ms | 5,076.250 ms | 20 |
| client receives first audio frame | 4,806.690 ms | 4,943.636 ms | 20 |
| cancellation acknowledgement | 1.690 ms | 1.802 ms | 3 |

`client receives first audio frame` is a network-side playback proxy. It does not measure when a
browser audio device makes the sample audible. Cancellation probes wait for streamed audio, cancel
that exact turn and use a ping/pong fence to reject audio arriving after the acknowledgement.

## Configuration

- CPU: AMD Ryzen 7 7700X, 8 cores / 16 threads; RAM: 61 GiB.
- GPU: NVIDIA GeForce RTX 3060, 12 GiB; driver 560.35.03.
- STT: `faster-whisper==1.2.1`, model `small`, Spanish, CPU `int8`, warmed at startup.
- STT runtime: `ctranslate2==4.8.1`.
- Planner: Ollama `ministral-3:14b`, 13.9B parameters, Q4_K_M, 4,096-token configured context,
  warmed by the benchmark.
- TTS: `piper-tts==1.7.0`, `es_ES-davefx-medium`, CPU, warmed at startup.
- Input: raw mono PCM16 little-endian at 16 kHz, generated from the phrase “Cuánto he gastado
  este mes”.
- Data: the deterministic demo seed, 1,678 transactions and 3,356 ledger entries.

CUDA STT was attempted first. Model loading succeeded, but the first inference failed because
`libcublas.so.12` was unavailable to CTranslate2. The accepted run therefore uses the supported
CPU `int8` fallback; the result must not be represented as a GPU STT measurement. Piper also runs
on CPU intentionally.

## Reproduce

After migrating and seeding banking, start the orchestrator where it can reach Ollama and start the
gateway with `orchestrator_http`, local STT and local TTS. Then run:

```bash
uv run --directory apps/voice-gateway python scripts/benchmark_e2e.py \
  --url http://127.0.0.1:8080 --token "$VOICE_GATEWAY_TOKEN" \
  --pcm-file utterance.pcm --warmup 2 --runs 20 --cancel-runs 3 \
  --require-stage-timings --timeout 300 --format json
```

The token is never emitted. `utterance.pcm` is deliberately not committed: a reproducible run may
use any non-empty mono PCM16 16 kHz fixture, while real user audio must not become repository data.
