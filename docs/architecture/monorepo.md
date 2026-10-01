# Repository strategy

A single Git repository called `finguard`, made up of autonomous projects that can be deployed independently.

> **A monorepo is not a monolithic application.**

Every service has its own dependencies, lockfile, Dockerfile, tests, configuration, migrations and pipeline. The root repository only coordinates the whole.

No Git submodules and no nested repositories: they would complicate development too much. Every directory must behave like a small independent repository and be extractable in the future without rewriting the code.

## Table of contents

1. [Monorepo tooling](#1-monorepo-tooling)
2. [General structure](#2-general-structure)
3. [Deployable components](#3-deployable-components)
4. [Dependency diagram](#4-dependency-diagram)
5. [Contracts as the source of truth](#5-contracts-as-the-source-of-truth)
6. [What to share and what not to](#6-what-to-share-and-what-not-to)
7. [pnpm configuration](#7-pnpm-configuration)
8. [Nx root configuration](#8-nx-root-configuration)
9. [Nx configuration for a Python service](#9-nx-configuration-for-a-python-service)
10. [Python independence](#10-python-independence)
11. [Docker Compose profiles](#11-docker-compose-profiles)
12. [Building images](#12-building-images)
13. [Tests](#13-tests)
14. [CI/CD](#14-cicd)
15. [Implementation order](#15-implementation-order)
16. [Rules worth writing down](#16-rules-worth-writing-down)

---

## 1. Monorepo tooling

| Need | Tool |
| --- | --- |
| Global orchestration | Nx |
| Frontend and TypeScript packages | pnpm workspace |
| Dependencies of each Python service | uv |
| Local development | Docker Compose |
| Image building | Docker Buildx Bake |
| Contracts | OpenAPI, JSON Schema and AsyncAPI |
| CI/CD | GitHub Actions |
| Observability | OpenTelemetry |

**Nx** would act as the polyglot coordinator. It can run arbitrary commands per project, cache them and run only the tasks affected by a change through `nx affected`, so it does not force every project to be TypeScript.

For TypeScript, a **pnpm workspace** would manage the frontend and the shared packages. pnpm can group several projects inside one repository and reference local packages explicitly through `workspace:`.

For Python, a single global uv workspace would **not** be used. Each microservice has its own `pyproject.toml` and its own `uv.lock`. uv workspaces share a single lockfile and environment, and its documentation advises against them when members need conflicting dependencies or separate environments. That could easily happen between voice, A2A, banking and orchestration.

---

## 2. General structure

```text
finguard/
├── apps/
│   ├── web/
│   ├── orchestrator/
│   ├── banking-api/
│   ├── voice-gateway/
│   ├── a2a-gateway/
│   ├── simulated-gym-agent/
│   └── audit-worker/
│
├── packages/
│   ├── contracts/
│   │   ├── openapi/
│   │   ├── asyncapi/
│   │   ├── json-schema/
│   │   ├── a2a/
│   │   └── generated/
│   │       ├── python/
│   │       └── typescript/
│   │
│   ├── python/
│   │   ├── finguard-observability/
│   │   ├── finguard-security/
│   │   └── finguard-testing/
│   │
│   └── typescript/
│       ├── ui/
│       ├── chart-renderer/
│       ├── api-client/
│       └── eslint-config/
│
├── infrastructure/
│   ├── compose/
│   ├── docker/
│   ├── postgres/
│   ├── rabbitmq/
│   ├── redis/
│   ├── telemetry/
│   └── monitoring/
│
├── tests/
│   ├── e2e/
│   ├── contract/
│   ├── security/
│   ├── performance/
│   └── fixtures/
│
├── docs/
│   ├── architecture/
│   ├── adr/
│   ├── api/
│   ├── threat-model/
│   └── roadmap/
│
├── tools/
│   ├── codegen/
│   ├── generators/
│   └── scripts/
│
├── .github/
│   └── workflows/
│
├── compose.yaml
├── docker-bake.hcl
├── nx.json
├── package.json
├── pnpm-workspace.yaml
├── CODEOWNERS
├── README.md
└── .env.example
```

---

## 3. Deployable components

### `apps/web`

The client application.

```text
apps/web/
├── src/
│   ├── app/
│   ├── features/
│   │   ├── chat/
│   │   ├── voice/
│   │   ├── transactions/
│   │   ├── charts/
│   │   └── payment-confirmation/
│   ├── components/
│   ├── hooks/
│   └── infrastructure/
├── tests/
├── package.json
├── project.json
├── Dockerfile
├── next.config.ts
└── README.md
```

Responsibilities:

- Chat and streaming.
- Audio capture and playback.
- Vega-Lite rendering.
- Bizum confirmations.
- Displaying the movements that were queried.
- User session handling.

It holds no financial rules and does not call `banking-api` directly.

### `apps/orchestrator`

The FinGuard orchestrator.

```text
apps/orchestrator/
├── src/
│   └── finguard_orchestrator/
│       ├── domain/
│       │   ├── conversation.py
│       │   ├── message.py
│       │   ├── query_plan.py
│       │   └── pending_action.py
│       │
│       ├── application/
│       │   ├── process_message.py
│       │   ├── plan_financial_query.py
│       │   ├── prepare_payment.py
│       │   └── generate_visualization.py
│       │
│       ├── ports/
│       │   ├── llm_provider.py
│       │   ├── banking_client.py
│       │   ├── session_repository.py
│       │   ├── visualization_provider.py
│       │   └── audit_publisher.py
│       │
│       ├── adapters/
│       │   ├── inbound/
│       │   │   ├── http/
│       │   │   └── websocket/
│       │   └── outbound/
│       │       ├── banking_http/
│       │       ├── gemini/
│       │       ├── openai/
│       │       ├── redis/
│       │       └── vega_lite/
│       │
│       └── bootstrap/
│           ├── config.py
│           ├── dependencies.py
│           └── main.py
│
├── tests/
│   ├── unit/
│   ├── integration/
│   └── contract/
├── pyproject.toml
├── uv.lock
├── project.json
├── Dockerfile
├── .env.example
└── README.md
```

Responsibilities:

- Keeping the conversational context.
- Interpreting intent.
- Building a `FinancialQuery`.
- Selecting tools.
- Coordinating queries and operations.
- Producing explanations.
- Building the visual plan.
- Integrating with LLM providers.

It knows nothing about the banking tables and never runs SQL directly.

### `apps/banking-api`

The deterministic financial core.

```text
apps/banking-api/
├── src/
│   └── finguard_banking/
│       ├── domain/
│       │   ├── accounts/
│       │   ├── ledger/
│       │   ├── payments/
│       │   ├── contacts/
│       │   └── policies/
│       │
│       ├── application/
│       │   ├── get_balance.py
│       │   ├── query_transactions.py
│       │   ├── prepare_bizum.py
│       │   ├── confirm_bizum.py
│       │   └── expire_payment_intent.py
│       │
│       ├── ports/
│       │   ├── account_repository.py
│       │   ├── ledger_repository.py
│       │   ├── payment_repository.py
│       │   └── event_publisher.py
│       │
│       ├── adapters/
│       │   ├── inbound/http/
│       │   └── outbound/
│       │       ├── postgres/
│       │       └── rabbitmq/
│       │
│       └── bootstrap/
│
├── migrations/
├── scripts/
│   └── generate_financial_data.py
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── contract/
│   └── concurrency/
├── pyproject.toml
├── uv.lock
├── project.json
├── Dockerfile
└── README.md
```

Responsibilities:

- Accounts.
- Double-entry ledger.
- Balances.
- Movements.
- Contacts.
- Payment intents.
- Simulated Bizum.
- Idempotency.
- Financial policies.
- Domain events.

This is the only service with write access to the financial database.

### `apps/voice-gateway`

```text
apps/voice-gateway/
├── src/
│   └── finguard_voice/
│       ├── domain/
│       ├── application/
│       ├── ports/
│       │   ├── speech_to_text.py
│       │   ├── text_to_speech.py
│       │   └── assistant_client.py
│       ├── adapters/
│       │   ├── inbound/websocket/
│       │   └── outbound/
│       │       ├── realtime_provider/
│       │       └── assistant_http/
│       └── bootstrap/
├── tests/
├── pyproject.toml
├── uv.lock
├── project.json
├── Dockerfile
└── README.md
```

Responsibilities:

- Audio streaming.
- Turn detection.
- Transcription.
- Synthesis.
- Barge-in.
- Voice session handling.

It does not interpret movements and does not execute operations. It converts:

```text
Audio → text → orchestrator → text → audio
```

### `apps/a2a-gateway`

```text
apps/a2a-gateway/
├── src/
│   └── finguard_a2a/
│       ├── domain/
│       │   └── agents.py               # registered agent, its status, the closed reasons
│       ├── ports/
│       │   ├── agent_registry.py
│       │   └── remote_agents.py
│       ├── application/
│       │   ├── conformance.py          # the A2A 1.0 gate over each Agent Card
│       │   └── directory.py            # each agent's judged card, kept for a TTL
│       ├── adapters/
│       │   └── outbound/
│       │       ├── env_registry.py     # A2A_GATEWAY_AGENTS
│       │       └── sdk_remote_agents.py  # the SDK client: credentials, limits, no retries
│       ├── app.py                      # GET /v1/agents, POST /v1/agents/{id}/messages
│       ├── bootstrap.py
│       ├── config.py
│       └── main.py
├── tests/
├── .env.defaults
├── pyproject.toml
├── uv.lock
├── Dockerfile
└── README.md
```

This service is an adapter, not the core of FinGuard, and it works in one direction: FinGuard is an A2A **client**.

```text
orchestrator
   ↓ ExpertAgentsPort, internal HTTP with a bearer token
a2a-gateway
   ↓ A2A 1.0, through the official SDK
external agent
```

The A2A protocol uses the Agent Card to declare identity, interfaces, capabilities, skills and authentication requirements. The gateway reads each registered agent's card, validates it against A2A 1.0 before anyone uses it, presents the agent's credential in a header, and parses and bounds every reply. It has no LLM, no banking client and no conversation state; it publishes no card of its own and has no inbound A2A route. Turning a reply into FinGuard's own shape, and checking it, is the orchestrator's. See [ADR-012](adr-012-a2a-consumer-gateway.md) and the [gateway's README](../../apps/a2a-gateway/README.md).

### `apps/simulated-gym-agent`

An A2A 1.0 server built with the same SDK, standing in for third parties the way the synthetic ledger stands in for a bank. It is outside the product: nothing in FinGuard imports it, and the gateway knows it only by URL. The `a2a` compose profile deploys it three times — `norte` answers with data in its own vocabulary, `sur` in Spanish text, `legacy` with an A2A 0.3 card the gate refuses. See [its README](../../apps/simulated-gym-agent/README.md).

### `apps/audit-worker`

```text
apps/audit-worker/
├── src/
│   └── finguard_audit/
│       ├── consumers/
│       ├── integrity/
│       ├── persistence/
│       └── bootstrap/
├── tests/
├── pyproject.toml
├── uv.lock
├── project.json
├── Dockerfile
└── README.md
```

Responsibilities:

- Consuming events.
- Recording accesses and operations.
- Building traceability.
- Reconstructing the history of a conversation.
- Detecting duplicate events.
- Checking the integrity of the audit chain.

---

## 4. Dependency diagram

### Allowed dependencies

```text
web             → orchestrator
voice-gateway   → orchestrator
orchestrator    → a2a-gateway
a2a-gateway     → external A2A agents in its registry
orchestrator   → banking-api
banking-api     → PostgreSQL
banking-api     → RabbitMQ
audit-worker    → RabbitMQ
audit-worker    → audit database
```

### Forbidden dependencies

```text
web             ✗ banking-api directly
voice-gateway   ✗ banking-api
a2a-gateway     ✗ orchestrator
a2a-gateway     ✗ banking-api and PostgreSQL
external agents ✗ FinGuard (no inbound A2A route, no webhook)
orchestrator   ✗ banking-api's tables
audit-worker    ✗ modifying the ledger
banking-api     ✗ LLM provider
```

---

## 5. Contracts as the source of truth

The centrepiece of the monorepo should be `packages/contracts/`.

No ORM models or internal classes are shared between services. Public contracts are.

```text
packages/contracts/
├── openapi/
│   ├── orchestrator.yaml
│   ├── banking-api.yaml
│   └── voice-gateway.yaml
│
├── asyncapi/
│   └── domain-events.yaml
│
├── json-schema/
│   ├── financial-query.schema.json
│   ├── visualization-result.schema.json
│   ├── payment-preview.schema.json
│   └── audit-event.schema.json
│
└── a2a/
    ├── agent-card.schema.json
    ├── query-movements.input.json
    └── query-movements.output.json
```

**No client generation.** The contracts are written and maintained by hand, and each consumer writes its own thin client against them. Generating clients from a service's live OpenAPI would make one service's build depend on another's, and would invert the source of truth: the contract would become a by-product of the implementation instead of the agreement the implementation has to honour.

So that:

- `orchestrator` calls `banking-api` through a hand-written client that follows the contract.
- `web` calls `orchestrator` the same way.
- Events are validated against AsyncAPI or JSON Schema.
- A2A translates its messages into known internal contracts.

The trade-off is deliberate: nothing detects a drift between a service and its contract automatically, so the divergence is corrected by hand when it shows up. What is bought with that is that no component depends on another to build.

---

## 6. What to share and what not to

### Worth sharing

- `packages/python/finguard-observability`
- `packages/python/finguard-security`
- `packages/python/finguard-testing`
- `packages/typescript/ui`
- `packages/typescript/chart-renderer`
- `packages/contracts`

Examples:

- OpenTelemetry initialisation.
- A common log format.
- `correlation_id` propagation.
- Authentication helpers.
- Test fixtures.
- Visual components.
- Generated clients.

### Not worth sharing

- Financial entities.
- Database repositories.
- SQLAlchemy models.
- Use cases.
- Bizum policies.
- Conversational context.
- Gemini- or OpenAI-specific code.
- Direct table access.

Creating `packages/common/` should be avoided. Packages named `common`, `shared` or `utils` tend to become containers with no boundaries and end up coupling every service.

---

## 7. pnpm configuration

The TypeScript workspace would only contain the frontend and the TypeScript packages:

```yaml
# pnpm-workspace.yaml

packages:
  - "apps/web"
  - "packages/typescript/*"
  - "packages/contracts/generated/typescript"
```

In `apps/web/package.json`:

```json
{
  "dependencies": {
    "@finguard/ui": "workspace:*",
    "@finguard/chart-renderer": "workspace:*",
    "@finguard/api-client": "workspace:*"
  }
}
```

The `workspace:` protocol forces the dependency to resolve from the monorepo itself and stops pnpm from accidentally using a different published version.

---

## 8. Nx root configuration

```json
{
  "$schema": "./node_modules/nx/schemas/nx-schema.json",
  "defaultBase": "main",
  "targetDefaults": {
    "lint": {
      "cache": true
    },
    "typecheck": {
      "cache": true
    },
    "test": {
      "cache": true,
      "dependsOn": ["^build"]
    },
    "build": {
      "cache": true,
      "dependsOn": ["^build"]
    }
  }
}
```

The root `package.json` could look like:

```json
{
  "name": "finguard",
  "private": true,
  "scripts": {
    "dev": "nx run-many -t serve --parallel=6",
    "check": "nx run-many -t lint,typecheck,test",
    "build": "nx run-many -t build",
    "affected:check": "nx affected -t lint,typecheck,test,build",
    "graph": "nx graph",
    "compose:up": "docker compose up --build",
    "compose:down": "docker compose down"
  },
  "devDependencies": {
    "nx": "<pinned-version>"
  }
}
```

Python dependencies would not live in the root.

---

## 9. Nx configuration for a Python service

Each application would carry a `project.json`. Example of `apps/banking-api/project.json`:

```json
{
  "name": "finguard-banking-api",
  "root": "apps/banking-api",
  "projectType": "application",
  "tags": [
    "scope:banking",
    "type:service",
    "language:python"
  ],
  "targets": {
    "serve": {
      "executor": "nx:run-commands",
      "options": {
        "cwd": "apps/banking-api",
        "command": "uv run uvicorn finguard_banking.bootstrap.main:app --reload --port 8001"
      }
    },
    "lint": {
      "executor": "nx:run-commands",
      "options": {
        "cwd": "apps/banking-api",
        "command": "uv run ruff check ."
      }
    },
    "format-check": {
      "executor": "nx:run-commands",
      "options": {
        "cwd": "apps/banking-api",
        "command": "uv run ruff format --check ."
      }
    },
    "typecheck": {
      "executor": "nx:run-commands",
      "options": {
        "cwd": "apps/banking-api",
        "command": "uv run mypy src"
      }
    },
    "test": {
      "executor": "nx:run-commands",
      "options": {
        "cwd": "apps/banking-api",
        "command": "uv run pytest"
      }
    },
    "build": {
      "executor": "nx:run-commands",
      "options": {
        "command": "docker build -f apps/banking-api/Dockerfile -t finguard/banking-api:local ."
      }
    }
  }
}
```

Nx can map any command to a target and run it for one project, for all of them, or only for the affected ones.

---

## 10. Python independence

Each service would carry its own lockfile:

```text
apps/orchestrator/uv.lock
apps/banking-api/uv.lock
apps/voice-gateway/uv.lock
apps/a2a-gateway/uv.lock
apps/audit-worker/uv.lock
```

And its own `pyproject.toml`:

```toml
# apps/banking-api/pyproject.toml

[project]
name = "finguard-banking-api"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi",
    "uvicorn",
    "sqlalchemy",
    "asyncpg",
    "pydantic"
]

[dependency-groups]
dev = [
    "pytest",
    "pytest-asyncio",
    "ruff",
    "mypy"
]
```

This means that:

- `voice-gateway` can use audio libraries without installing them in banking.
- `a2a-gateway` can update the A2A SDK without touching the ledger.
- `orchestrator` can carry LLM SDKs without bringing them into `banking-api`.
- Each Docker image contains only what it needs.

---

## 11. Docker Compose profiles

Not every service would be started every time.

```yaml
services:
  postgres:
    image: postgres
    profiles: ["core"]

  redis:
    image: redis
    profiles: ["core"]

  banking-api:
    build:
      context: .
      dockerfile: apps/banking-api/Dockerfile
    profiles: ["core"]

  orchestrator:
    build:
      context: .
      dockerfile: apps/orchestrator/Dockerfile
    profiles: ["core"]

  web:
    build:
      context: .
      dockerfile: apps/web/Dockerfile
    profiles: ["core"]

  voice-gateway:
    build:
      context: .
      dockerfile: apps/voice-gateway/Dockerfile
    profiles: ["voice"]

  a2a-gateway:
    build:
      context: .
      dockerfile: apps/a2a-gateway/Dockerfile
    profiles: ["a2a"]

  rabbitmq:
    image: rabbitmq
    profiles: ["events"]

  audit-worker:
    build:
      context: .
      dockerfile: apps/audit-worker/Dockerfile
    profiles: ["events"]
```

Commands:

```bash
# Functional core
docker compose --profile core up

# Core and voice
docker compose --profile core --profile voice up

# The whole system
docker compose \
  --profile core \
  --profile voice \
  --profile a2a \
  --profile events \
  up
```

Docker Compose can enable optional services through profiles, which fits nicely with keeping voice, A2A and observability out of the minimal startup.

---

## 12. Building images

`docker-bake.hcl` would centralise the build:

```hcl
group "core" {
  targets = [
    "web",
    "orchestrator",
    "banking-api"
  ]
}

group "all" {
  targets = [
    "web",
    "orchestrator",
    "banking-api",
    "voice-gateway",
    "a2a-gateway",
    "audit-worker"
  ]
}

target "banking-api" {
  context    = "."
  dockerfile = "apps/banking-api/Dockerfile"
  tags       = ["finguard/banking-api:local"]
}

target "orchestrator" {
  context    = "."
  dockerfile = "apps/orchestrator/Dockerfile"
  tags       = ["finguard/orchestrator:local"]
}
```

```bash
docker buildx bake core
docker buildx bake all
```

Bake can declare several build targets and suits building multiple images from a monorepo.

---

## 13. Tests

Tests are split across two levels.

### Inside each service

```text
apps/banking-api/tests/
├── unit/
├── integration/
├── contract/
└── concurrency/
```

### At the root

```text
tests/
├── e2e/
│   ├── text_conversation/
│   ├── financial_queries/
│   ├── bizum/
│   ├── voice/
│   └── a2a/
│
├── contract/
│   ├── assistant_banking/
│   ├── web_assistant/
│   └── a2a_assistant/
│
├── security/
│   ├── prompt_injection/
│   ├── unauthorized_access/
│   ├── payment_confirmation/
│   └── duplicate_operations/
│
└── performance/
    ├── query_latency/
    ├── websocket/
    └── voice_realtime/
```

---

## 14. CI/CD

What exists today is [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml), with three jobs:

| Job | What it runs | With a database |
| --- | --- | --- |
| `check` | One per service in parallel: `uv sync --frozen`, `ruff check`, `ruff format --check`, `mypy src`, `pytest -m "not integration"`. Across two Python versions and both Linux and Windows | No |
| `web` | `npm ci`, `npm run typecheck`, `npm test`, `npm run build` | No |
| `integration` | Creates the databases and roles with the same `01-databases.sql` the compose file uses, applies the migrations, runs the full suite, loads the dataset and verifies it, and checks that the generator is still deterministic | PostgreSQL 18 as a service container |

Three decisions:

- **`uv sync --frozen`**, not `uv sync`. If the lock has drifted from `pyproject.toml`, the job fails instead of re-resolving and verifying something different from what runs locally.
- **No path filters and no `nx affected`.** With two services, the matrix runs both in parallel and finishes sooner than it would take to reason about what changed. Filters come in when CI time starts to hurt; at that point `dorny/paths-filter` is enough and does not force Nx into the repo.
- **It runs on `develop` → `main`, and otherwise only when asked.** It used to run on every push to every branch, which is up to ten jobs a push — and four of them are Windows runners, billed at twice a Linux minute. GitHub Pro includes 3.000 minutes a month on private repositories and invoices the excess, so a branch with a day's worth of commits was spending real money answering a question `just check` answers locally for free. The `branches:` filter matches the *target*, so each job also carries `github.head_ref == 'develop'` to filter the source, and `workflow_dispatch` verifies a branch before it gets there. One caveat if these ever become required checks: GitHub counts a skipped job as satisfied, so a branch opened straight against `main` would go green having run nothing.

The verification a working branch gets is therefore local, and [`just check`](../../justfile) is the whole of it.

Pending, once the things they verify exist:

- **Contracts** — on changes to `packages/contracts/**`: valid schemas and consumer tests. No client regeneration: §5 rules codegen out. The folder is empty today.
- **Images** — build and publish only what is affected.
- **Security** — dependency and secret scanning.
- **Release** — tagging and publishing.

---

## 15. Implementation order

Not every directory would be created with empty code from the first moment.

### First iteration

```text
apps/
├── web/
├── orchestrator/
└── banking-api/

packages/
└── contracts/

infrastructure/
└── postgres/
```

Goal:

```text
User → web → orchestrator → banking-api → PostgreSQL
```

### Second iteration

- `apps/voice-gateway`
- `packages/typescript/chart-renderer`

### Third iteration

- `apps/audit-worker`
- RabbitMQ

### Fourth iteration

- `apps/a2a-gateway`
- `packages/contracts/a2a`

This avoids building an enormous architecture before the main flow exists.

---

## 16. Rules worth writing down

The development rules live in [development-rules.md](development-rules.md).

### Minimum structure to start with

```text
finguard/
├── apps/
│   ├── web/
│   ├── orchestrator/
│   └── banking-api/
├── packages/
│   └── contracts/
├── infrastructure/
│   └── postgres/
├── tests/
│   └── e2e/
├── docs/
│   ├── architecture/
│   └── adr/
├── compose.yaml
├── nx.json
├── package.json
├── pnpm-workspace.yaml
└── README.md
```

This structure is already enough to develop the core without closing the door on voice, audit or A2A. The key is that FinGuard has independent projects inside a single repository, not several artificial services sharing code and a database like a distributed monolith.
