# FinGuard voice gateway

`voice-gateway` is the Phase 6 voice channel adapter. It owns session transport, audio framing,
fake speech adapters and cancellation fencing; the orchestrator remains the only financial
authority. Version 1 never confirms a payment from speech.

## Local execution

From the repository root:

```bash
uv run --directory apps/voice-gateway uvicorn finguard_voice_gateway.main:app --reload
```

The service listens on `http://127.0.0.1:8000`. Create a session with the same bearer credential
used by the web client:

```bash
curl -H 'Authorization: Bearer development-credential' \
  -X POST http://127.0.0.1:8000/v1/voice/sessions
```

Present the returned one-use ticket in the WebSocket subprotocol list beside
`finguard.voice.v1`. `/health` and `/version` are unauthenticated.

`/health` is a liveness check only. For local STT, readiness is obtained with
`VOICE_GATEWAY_STT_WARMUP=true`: model loading then happens during startup and startup fails if the
model cannot be loaded.

## Configuration

`.env.defaults` documents the supported `VOICE_GATEWAY_*` settings. Input and output sample rates
are fixed by the v1 protocol at 16 kHz and 24 kHz; other values are rejected by configuration.
The current fake STT buffers up to the configured 30-second utterance limit. The five-second queue
limit becomes active when a streaming STT adapter is introduced.

Local STT uses the optional `faster-whisper` extra and is lazy: CI and the default `fake` mode do
not import or download a model. Set `VOICE_GATEWAY_STT_MODE=local`, choose a model path (the
zero-download option), `VOICE_GATEWAY_STT_DEVICE=cpu|cuda|auto`, compute type and optionally
`VOICE_GATEWAY_STT_LOCAL_FILES_ONLY=true`. Named models such as `small` are downloaded by
faster-whisper when absent from its cache unless local-files-only is enabled. Inference runs in a
bounded worker pool; cancellation fences the result, while a native call already executing cannot
be force-aborted safely and retains its slot until completion. The service maps empty, timeout and
provider failures to sanitized WebSocket errors. `VOICE_GATEWAY_STT_WARMUP=true` loads the model
during startup and makes startup fail if it cannot load; otherwise loading is lazy.

Local TTS uses the optional `piper-tts` extra. Piper is an ONNX-based local adapter with Spanish
voices available; the voice model is deliberately not bundled or downloaded by CI. Set
`VOICE_GATEWAY_TTS_MODE=local` and point `VOICE_GATEWAY_TTS_MODEL_PATH` to a downloaded Spanish
`.onnx` voice beside its `.onnx.json` configuration. `VOICE_GATEWAY_TTS_DEVICE=cpu` is the portable
default; `cuda` requires a compatible ONNX Runtime GPU installation. Piper output is converted to
mono PCM16 little-endian at the protocol's fixed 24 kHz and emitted in configurable 20 ms chunks.
Inference runs in a worker thread, with bounded backpressure and cooperative cancellation.
`VOICE_GATEWAY_TTS_WARMUP=true` loads the voice during startup.
`piper-tts` is GPL-3.0 software and remains an optional, separately installed local dependency;
review its [license](https://github.com/OHF-Voice/piper1-gpl/blob/main/LICENSE.md) before distributing
a bundled voice-gateway image.

The assistant is fake by default. To use the real orchestrator, set
`VOICE_GATEWAY_ASSISTANT_MODE=orchestrator_http` and configure
`VOICE_GATEWAY_ORCHESTRATOR_BASE_URL`. The gateway calls the observed
`POST /v1/questions/stream` contract with the session's Bearer credential, the opaque conversation,
and the stable `Idempotency-Key` `voice:<session-id>:<turn-id>`. The orchestrator emits JSON SSE
events named `stage` followed by one `answer`; those stages are forwarded as `assistant.stage`.
The client intentionally has no retries: a cancelled or payment-related command must not be
replayed automatically. Remote bodies are never copied into gateway events or logs.

The real orchestrator returns a conversation payload whose `workflow` is
`PAYMENT_CONFIRMATION` for a payment preview (and uses `AWAITING_CONFIRMATION` as its outcome).
The gateway exposes that as `paymentConfirmationRequired`; spoken follow-up remains blocked at the
gateway and cannot be sent to the orchestrator.

## Tests and container

```bash
uv run --directory apps/voice-gateway pytest -q
uv run --directory apps/voice-gateway ruff check .
uv run --directory apps/voice-gateway mypy src tests
docker build -t finguard-voice-gateway apps/voice-gateway
docker build --build-arg INSTALL_LOCAL_STT=1 -t finguard-voice-gateway:local-stt apps/voice-gateway
```

The `local-stt` image is based on `python:3.12-slim` and is CPU-oriented. CUDA requires a CUDA 12+
runtime with cuBLAS and cuDNN 9, plus compatible host/container GPU configuration; the slim image
does not promise a functional CUDA runtime.

Manual benchmark (requires `uv sync --extra local-stt`; loading and inference are timed separately):

```bash
uv run --directory apps/voice-gateway --extra local-stt \
  python scripts/benchmark_stt.py sample.wav --model /models/whisper-small \
  --device cpu --local-files-only
```

The WAV must be PCM16 little-endian, mono, 16 kHz. Output reports model/device, timings, real-time
factor and transcript character count; it never prints the transcript or audio. `--model small`
may download from Hugging Face if not cached; use a local model path plus `--local-files-only` for
zero downloads.

Manual local-TTS benchmark (requires `uv sync --extra local-tts`; the voice must already exist):

```bash
uv run --directory apps/voice-gateway --extra local-tts \
  python scripts/benchmark_tts.py "Hola, este es FinGuard." \
  --model /models/es/voice.onnx
```

Use `--config` only when the voice JSON is not beside the model. The benchmark never downloads a
voice or prints generated audio; it reports loading, synthesis, real-time factor, output chunks
and bytes.

Observed dependency-only check (no model loaded or downloaded): Python 3.14.7 installed
`faster-whisper==1.2.1` and `ctranslate2==4.8.1`; CTranslate2 detected one CUDA device. This does
not validate cuDNN, model loading, or real CUDA inference.
