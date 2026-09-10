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

---

## Second run — 18 August 2026, in containers, and what a browser found

The measurement above is the accepted phase-6 baseline: `small`, fake providers for the protocol
suite, a one-second fixture, and a Python client. Running the same path from a **browser** — the
client somebody actually speaks into — found six defects that a Python runner cannot see. They are
recorded here because the shape of the mistake matters more than the numbers: every one of them was
invisible to the harness that declared the phase measured.

| what broke | why nothing caught it |
| --- | --- |
| The gateway answered no CORS preflight, so the page could not open a session at all | the runner is not a browser; `curl` answered every route |
| The answer played as a run of clicks: one frame per `onended`, an event-loop gap every 20 ms | the runner records when the first frame *arrives*, never how it sounds |
| The fake transcriber decoded microphone PCM16 as UTF-8 | the runner sends its sentence *as* text bytes, which decodes |
| `small` heard "hazle un bizum a Aitor de 23 euros" as "hazle un bizuma y torde 23 euros" | the fixture was one second of a phrase the model already knew |
| A near-silent clip where a name should be came back as "Adiós." | a synthetic fixture is never near-silent |
| The runner sent each utterance as one frame, over the 64 KiB the protocol publishes | one second of PCM16 is 32 KiB, so the limit was never reached |

### Result, real speech both ways

`docker compose -f compose.yaml -f compose.voice-real.yaml`: Faster Whisper `medium` CPU `int8`,
Piper `es_ES-davefx-medium`, 2.9 s of Spanish speech, three recorded turns after one warmup.

| milestone | P50 | P95 |
| --- | ---: | ---: |
| input commit → transcript final | 3,040 ms | 3,061 ms |
| input commit → first orchestrator stage | 3,073 ms | 3,077 ms |
| first stage → assistant text final | 5.5 ms | 6.1 ms |
| assistant text final → first TTS byte | 212 ms | 218 ms |
| input start → turn completed | 3,445 ms | 3,478 ms |
| cancellation acknowledgement | 15 ms | — |

The assistant stage is milliseconds because the sentence is a payment: the deterministic router
answers it and no model is called. Transcription is now the whole cost, at roughly one second of
CPU per second of speech.

### Vocabulary, measured

`initial_prompt` carries the words a general Spanish model has never seen. On the same audio:

```
small,  no prompt:  "Hazle un vizuma y torde 23 euros."     → the assistant can only refuse
small,  with prompt: "Hazle un bizuma y torde 23 euros."    → the rail and the amount survive
medium, with prompt: "Hazle un bizum a Aitor de 23 euros."  → every word, name included
```

A hypothesis that did **not** survive measurement: the browser's 48 kHz → 16 kHz resampling
interpolates without an anti-aliasing stage, which looked like an obvious cause. Feeding the same
48 kHz utterance through the current interpolation and through a windowed-sinc decimation produced
byte-identical transcripts. The capture path now asks for a 16 kHz `AudioContext` so the browser's
own resampler does the work, but that is hygiene, not the fix — the model was.

Speech synthesised by Piper and transcribed back by Whisper returns the same sentence bar one word,
which is the check that the audio is intelligible speech rather than well-formed noise.

## Follow-up turn (Wave 4.4, fake providers)

Design §8 states the latency gate against Wave 3's own single-turn measurement rather than a
number invented for it. `scripts/benchmark_e2e.py` now accepts `--follow-up-text`: every recorded
turn is followed by a second turn on the same session, and its durations are reported under a
`followUp:` prefix. Run locally against the gateway with fake providers (the default:
`VOICE_GATEWAY_ASSISTANT_MODE`/`VOICE_GATEWAY_STT_MODE`/`VOICE_GATEWAY_TTS_MODE` all `fake` per
`.env.defaults` — nothing extra to set), two warmup turns and twenty recorded turns:

```bash
uv run --directory apps/voice-gateway uvicorn finguard_voice_gateway.main:app \
  --host 127.0.0.1 --port 8123
VOICE_GATEWAY_TOKEN=dev-benchmark-token \
uv run --directory apps/voice-gateway python scripts/benchmark_e2e.py \
  --url http://127.0.0.1:8123 --text "¿Cuánto gasté el mes pasado? gráfico" \
  --follow-up-text "Ponlo en horizontal" --warmup 2 --runs 20 --timeout 30 --format json
```

The `--text` turn is the charted turn the fake assistant draws a chart for («gráfico»); the
`--follow-up-text` turn is the presentation-only restyle («Ponlo en horizontal»). This run used the
gateway's own fake assistant, which does not restyle a chart — the fake STT/TTS/assistant loop
still measures the gateway's own protocol overhead for a second turn on an open session, but not
the orchestrator's real restyle cost, which is measured in the orchestrator (Task 4's own
`test_a_presentation_only_restyle_makes_zero_banking_calls_and_no_policy_call`, `apps/orchestrator`)
rather than here.

| milestone | P50 | P95 | samples |
| --- | ---: | ---: | ---: |
| followUp:input start → turn completed | 16.000 ms | 16.000 ms | 20 |

P50 and P95 land on the same value because Windows' default ~15.6 ms scheduler timer tick is the
dominant source of jitter at this scale, not because the samples are a fabricated flat line; every
one of the 20 fake-provider runs quantises to that tick.

**§8 gate:** a follow-up turn's P95 must not exceed Wave 3's single-turn P95 (5,076 ms, real
providers, 17 August 2026) plus the presentation-only path's own cost (16.000 ms here), measured
before it is promised — sum: 5,076.250 ms + 16.000 ms = 5,092.250 ms; the real-provider follow-up
row is the operator's run (Task 9's runbook).
