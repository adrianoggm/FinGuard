# Build context: apps/voice-gateway (see compose.yaml).
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.9.17 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app

# This slim image is CPU-oriented; CUDA needs a CUDA 12+/cuBLAS/cuDNN 9 runtime image and GPU wiring.
ARG INSTALL_LOCAL_STT=0
ARG INSTALL_LOCAL_TTS=0

# Dependencies before the code so the layer can be reused.
# --frozen: fails if uv.lock does not match pyproject.toml, instead of re-resolving.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
# The extras are separate because the models are: speech in and speech out are two downloads and
# two runtimes, and a demo may want either without the other.
RUN set -eu; extras=""; if [ "$INSTALL_LOCAL_STT" = "1" ]; then extras="--extra local-stt"; fi; if [ "$INSTALL_LOCAL_TTS" = "1" ]; then extras="$extras --extra local-tts"; fi; uv sync --frozen --no-dev $extras


FROM python:3.12-slim

# ponytail: unprivileged user. It writes nothing except the model cache below.
RUN useradd --create-home --uid 10001 finguard
WORKDIR /app

COPY --from=builder --chown=finguard:finguard /app /app
ENV PATH="/app/.venv/bin:$PATH"

# Where a named model is cached. Created here and owned by the service user so that a named volume
# mounted over it inherits that ownership: a root-owned cache means an unprivileged process cannot
# download a model, and half a gigabyte is not something to fetch on every container start.
RUN mkdir -p /models/huggingface && chown -R finguard:finguard /models
ENV HF_HOME=/models/huggingface

USER finguard
EXPOSE 8000

# `uvicorn` from the venv on PATH rather than `uv run`: `uv run` re-syncs the environment at every
# start, which pulled the dev group — ruff and mypy — into the running container and made booting
# depend on the network. The other two services already did it this way.
CMD ["uvicorn", "finguard_voice_gateway.main:app", "--host", "0.0.0.0", "--port", "8000"]
