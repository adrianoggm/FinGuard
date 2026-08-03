# Build context: the repository root (see compose.yaml).
FROM python:3.13-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app

# Dependencies are installed before the code so the layer can be reused.
# --frozen: fails if uv.lock does not match pyproject.toml, instead of re-resolving.
COPY apps/orchestrator/pyproject.toml apps/orchestrator/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY apps/orchestrator/src ./src
RUN uv sync --frozen --no-dev


FROM python:3.13-slim

# ponytail: unprivileged user; the service never needs to write to disk.
RUN useradd --create-home --uid 10001 finguard
WORKDIR /app

COPY --from=builder --chown=finguard:finguard /app /app
ENV PATH="/app/.venv/bin:$PATH"

USER finguard
EXPOSE 8000

CMD ["uvicorn", "finguard_orchestrator.bootstrap.main:app", "--host", "0.0.0.0", "--port", "8000"]
