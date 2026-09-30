# Build context: apps/a2a-gateway (see compose.yaml).
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.9.17 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app

# Dependencies before the code so the layer can be reused.
# --frozen: fails if uv.lock does not match pyproject.toml, instead of re-resolving.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN uv sync --frozen --no-dev


FROM python:3.12-slim

# ponytail: unprivileged user. It writes nothing.
RUN useradd --create-home --uid 10001 finguard
WORKDIR /app

COPY --from=builder --chown=finguard:finguard /app /app
ENV PATH="/app/.venv/bin:$PATH"

USER finguard
EXPOSE 8000

# `uvicorn` from the venv on PATH rather than `uv run`: `uv run` re-syncs the environment at every
# start, which pulled the dev group — ruff and mypy — into the running container and made booting
# depend on the network. The other two services already did it this way.
CMD ["uvicorn", "finguard_a2a.main:app", "--host", "0.0.0.0", "--port", "8000"]
