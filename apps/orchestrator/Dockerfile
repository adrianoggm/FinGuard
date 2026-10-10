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
COPY apps/orchestrator/migrations ./migrations
COPY apps/orchestrator/alembic.ini ./alembic.ini
RUN uv sync --frozen --no-dev

# The operational values, and the only place they exist. The settings fields have no
# defaults, so without this the service refuses to start rather than inventing a timeout —
# which is the intended failure and a poor way to discover a missing COPY. Anything private
# still comes from the environment, which is loaded after this and wins.
COPY apps/orchestrator/.env.defaults ./.env.defaults


FROM python:3.13-slim

# ponytail: unprivileged user; the service never needs to write to disk.
RUN useradd --create-home --uid 10001 finguard
WORKDIR /app

COPY --from=builder --chown=finguard:finguard /app /app
ENV PATH="/app/.venv/bin:$PATH"

# Which commit this is. An argument rather than a copied .git directory: the repository would
# invalidate every layer on every commit and ship history into a runtime image.
ARG BUILD_SHA=unknown
ARG BUILD_REF=unknown
ARG BUILD_SOURCE_SHA256=unknown
ENV ORCHESTRATOR_BUILD_SHA=$BUILD_SHA
ENV ORCHESTRATOR_BUILD_REF=$BUILD_REF
ENV ORCHESTRATOR_BUILD_SOURCE_SHA256=$BUILD_SOURCE_SHA256

USER finguard
EXPOSE 8000

CMD ["uvicorn", "finguard_orchestrator.bootstrap.main:app", "--host", "0.0.0.0", "--port", "8000"]
