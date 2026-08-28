# FinGuard

A conversational, modular and secure financial engine. It lets you query banking information and prepare operations through natural language, by text or by voice.

> FinGuard interprets. Deterministic services validate. The user authorises. The system executes.

The generative model interprets intent and writes answers, but it never reaches the database and never moves money on its own. Security is a structural property, not a layer added afterwards.

## Status

Phases 3 (conversational assistant), 5 (secure Bizum) and 6 (real-time voice) are implemented and measured. Phase 4 (visualisation and «Mis métricas») is built as far as reshaping: a chart is derived with no model call, emitted as Vega-Lite and drawn in the chat, and a metric is saved, reopened, retitled, reordered and deleted. Reshaping a saved metric was never built, nothing validates an emitted spec against a schema, and the spec declares Vega-Lite v5 against a v6.4.3 bundle — so phase 4 is not closed on its own acceptance list.

Three defects are open and named rather than left to be discovered: a failed payment confirmation is invisible on both money screens, the saved-metric authorisation guard refuses a single account the rest of the system serves, and the orchestrator writes no audit event. See [docs/next.md](docs/next.md) for what is still open, in order.

## Documentation

- [docs/vision.md](docs/vision.md) — product vision, architecture, data model, security, A2A and ADRs.
- [docs/roadmap.md](docs/roadmap.md) — phases, dates and success metrics.
- [docs/conversational-capabilities.md](docs/conversational-capabilities.md) — which conversational capabilities are active, and on what evidence. The reference the product texts must not contradict.
- [docs/architecture/monorepo.md](docs/architecture/monorepo.md) — repository structure, tooling and allowed dependencies.
- [docs/architecture/development-rules.md](docs/architecture/development-rules.md) — development rules.
- [docs/audits/2026-08-28-do-the-tests-prove-anything.md](docs/audits/2026-08-28-do-the-tests-prove-anything.md) — what the suite proves, measured by breaking the code on purpose.
- [docs/audits/2026-08-28-ux.md](docs/audits/2026-08-28-ux.md) — the web client's interaction and visual quality, measured against the running app.

## Services

A monorepo of independent projects: each service has its own dependencies, lockfile, Dockerfile, tests and pipeline.

| `apps/` | Responsibility | State |
| --- | --- | --- |
| `web` | Chat, audio, visualisations and confirmations | Working — six screens plus the chat sheet, charts, voice, 85 tests. Not containerised: it runs from `just web` |
| `orchestrator` | Conversational context, intent, tool coordination, LLM | Working, local models only |
| `banking-api` | Accounts, ledger, balances, contacts, Bizum, the append-only audit trail. The only writer of financial data | Working |
| `voice-gateway` | Streaming audio, transcription, synthesis, barge-in | Working, local providers only. Measured end to end in [its own evaluation](apps/voice-gateway/evaluation/RESULTS.md) |
| `a2a-gateway` | Agent Card, A2A client and server | Pending — no code exists yet, only the phase 7 design |
| `audit-worker` | Append-only audit of accesses and operations | Never built as a worker. The trail lives inside `banking-api`, hooked to its command bus; the orchestrator writes no event at all |

## Getting started

```bash
just hooks     # installs the pre-commit hook. Once per clone
just install   # dependencies of every service, the web client included
just back      # postgres + banking-api + orchestrator
just voice     # adds the voice gateway
just web       # the client, outside Docker
just migrate   # applies the migrations
just seed      # loads the synthetic dataset and checks that it adds up
just check     # ruff + mypy + tests, plus the client's types, tests and build
just evaluate  # execution accuracy of the query engine
```

`just` on its own lists every task. Requires Docker, [uv](https://docs.astral.sh/uv/), [just](https://just.systems/) and Node 22 for the client.

Everything is published on `127.0.0.1` only — PostgreSQL on **5442**, `banking-api` on **8001**, `orchestrator` on **8000**, `voice-gateway` on **8002** and the client on **3000** — because the development credentials are trivial. One database and one role per service, and no role can connect to another service's database.

## Stack

Python 3.12+ · FastAPI · Pydantic · asyncpg · Alembic · PostgreSQL 18 · React + TypeScript · Vite · Vega-Lite · Vitest · Ollama · faster-whisper · Piper · Docker Compose · pytest.

Redis and RabbitMQ are in the vision's design and nothing uses them yet, so they are not listed above. `packages/contracts/` is still two empty folders: the services share nothing but conventions.

## Language

Code, comments, tests and documentation are written in English. The data — merchant names, movement descriptions, the questions in the evaluation dataset — stays in Spanish: it is what a Spanish-speaking user says and reads.
