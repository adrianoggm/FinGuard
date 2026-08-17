FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.9.17 /uv /uvx /bin/

WORKDIR /app
ARG INSTALL_LOCAL_STT=0
# This slim image is CPU-oriented; CUDA needs a CUDA 12+/cuBLAS/cuDNN 9 runtime image and GPU wiring.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN if [ "$INSTALL_LOCAL_STT" = "1" ]; then uv sync --frozen --no-dev --extra local-stt; else uv sync --frozen --no-dev; fi

EXPOSE 8000
CMD ["uv", "run", "uvicorn", "finguard_voice_gateway.main:app", "--host", "0.0.0.0", "--port", "8000"]
