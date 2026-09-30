# Build context: apps/simulated-gym-agent (see compose.yaml).
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.9.17 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app

# Dependencies before the code so the layer can be reused.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN uv sync --frozen --no-dev


FROM python:3.12-slim

RUN useradd --create-home --uid 10001 finguard
WORKDIR /app

COPY --from=builder --chown=finguard:finguard /app /app
ENV PATH="/app/.venv/bin:$PATH"

USER finguard
EXPOSE 8000

CMD ["uvicorn", "finguard_simulated_gym.main:app", "--host", "0.0.0.0", "--port", "8000"]
