# FinGuard

A conversational, modular and secure financial engine. It lets you query banking information and prepare operations through natural language, by text or by voice.

> FinGuard interprets. Deterministic services validate. The user authorises. The system executes.

The generative model interprets intent and writes answers, but it never reaches the database and never moves money on its own. Security is a structural property, not a layer added afterwards.

## Status

Banking core working: double-entry ledger with invariants enforced by PostgreSQL, balance derived from the entries, and a query engine that compiles validated plans into parameterised SQL. Phase 2 of the roadmap is closed; the assistant (phase 3) is next.

## Documentation

- [docs/vision.md](docs/vision.md) — product vision, architecture, data model, security, A2A and ADRs.
- [docs/roadmap.md](docs/roadmap.md) — phases, dates and success metrics.
- [docs/architecture/monorepo.md](docs/architecture/monorepo.md) — repository structure, tooling and allowed dependencies.
- [docs/architecture/development-rules.md](docs/architecture/development-rules.md) — development rules.

## Services

A monorepo of independent projects: each service has its own dependencies, lockfile, Dockerfile, tests and pipeline.

| `apps/` | Responsibility | State |
| --- | --- | --- |
| `web` | Chat, audio, visualisations and confirmations | Pending |
| `assistant-api` | Conversational context, intent, tool coordination, LLM | Working, local models only |
| `banking-api` | Accounts, ledger, balances, contacts, Bizum. The only writer of financial data | Working |
| `voice-gateway` | Streaming audio, transcription, synthesis, barge-in | Pending |
| `a2a-gateway` | Agent Card, A2A client and server | Pending |
| `audit-worker` | Append-only audit of accesses and operations | Pending |

## Getting started

```bash
just hooks     # installs the pre-commit hook. Once per clone
just back      # postgres + banking-api + assistant-api
just migrate   # applies the migrations
just seed      # loads the synthetic dataset and checks that it adds up
just check     # ruff + mypy + tests
just evaluate  # execution accuracy of the query engine
```

`just` on its own lists every task. Requires Docker, [uv](https://docs.astral.sh/uv/) and [just](https://just.systems/).

Everything is published on `127.0.0.1` only — PostgreSQL on **5442**, `banking-api` on **8001**, `assistant-api` on **8000** — because the development credentials are trivial. One database and one role per service, and no role can connect to another service's database.

## Stack

Python 3.13 · FastAPI · Pydantic · asyncpg · Alembic · PostgreSQL 18 · Redis · RabbitMQ · React/Next.js + TypeScript · Vega-Lite · Docker Compose · pytest.

## Language

Code, comments, tests and documentation are written in English. The data — merchant names, movement descriptions, the questions in the evaluation dataset — stays in Spanish: it is what a Spanish-speaking user says and reads.
