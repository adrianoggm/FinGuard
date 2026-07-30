# Contexto de build: la raíz del repositorio (ver compose.yaml).
FROM python:3.13-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app

# Las dependencias se instalan antes del código para que la capa se reutilice.
# --frozen: falla si uv.lock no coincide con pyproject.toml, en lugar de re-resolver.
COPY apps/assistant-api/pyproject.toml apps/assistant-api/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY apps/assistant-api/src ./src
RUN uv sync --frozen --no-dev


FROM python:3.13-slim

# ponytail: usuario sin privilegios; el servicio no necesita escribir en el disco.
RUN useradd --create-home --uid 10001 finguard
WORKDIR /app

COPY --from=builder --chown=finguard:finguard /app /app
ENV PATH="/app/.venv/bin:$PATH"

USER finguard
EXPOSE 8000

CMD ["uvicorn", "finguard_assistant.bootstrap.main:app", "--host", "0.0.0.0", "--port", "8000"]
