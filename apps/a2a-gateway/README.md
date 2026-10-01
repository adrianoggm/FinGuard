# FinGuard A2A gateway

`a2a-gateway` is the one FinGuard process that speaks A2A 1.0, and it speaks it as a **client**
only. It keeps the registry of trusted agents, validates each agent's Agent Card against the
standard before anyone uses it, presents the agent's credential where the card's security scheme
says, and parses and bounds every reply with the official `a2a-sdk`. The orchestrator reaches it
through two internal routes and never sees the protocol.

It is closed by design: no Agent Card of its own, no inbound A2A route, no push-notification
webhook, no LLM, no banking client and no conversation state. Reading a reply into FinGuard's own
shape and checking every value of it is the orchestrator's work, not this service's. The decision
and its known limits are in [ADR-012](../../docs/architecture/adr-012-a2a-consumer-gateway.md).

## Local execution

From the repository root:

```bash
just serve-a2a
# the same as
uv run --directory apps/a2a-gateway uvicorn finguard_a2a.main:app --reload --port 8003
```

With `A2A_GATEWAY_AGENTS` blank the gateway starts with no agents. To try it against the simulated
gyms on this machine, start each on its own loopback address, because an agent's id is its host:

```bash
just serve-gym norte 9101 127.0.0.1
just serve-gym sur 9102 127.0.0.2
just serve-gym legacy 9103 127.0.0.3

A2A_GATEWAY_AGENTS=http://127.0.0.1:9101,http://127.0.0.2:9102,http://127.0.0.3:9103 \
A2A_GATEWAY_CREDENTIAL_127_0_0_1=dev-gym-norte-key \
A2A_GATEWAY_CREDENTIAL_127_0_0_2=dev-gym-sur-token \
  just serve-a2a
```

Then point the orchestrator at it with `ORCHESTRATOR_A2A_GATEWAY_URL=http://127.0.0.1:8003` (and
`ORCHESTRATOR_A2A_GATEWAY_TOKEN`, the same token). Without that URL the orchestrator has no
expert-agent capability at all.

In compose, `just a2a` starts the gateway on `127.0.0.1:8003` with three simulated gyms (`gym-norte`,
`gym-sur`, `gym-legacy`) and wires the orchestrator to it. `/health` and `/version` are
unauthenticated.

## Configuration

`.env.defaults` lists the defaults. Every variable has the `A2A_GATEWAY_` prefix.

| Variable | Default | Meaning |
| --- | --- | --- |
| `A2A_GATEWAY_AGENTS` | blank | Comma-separated base URLs of the trusted agents. An agent's id is its host, lower case, dots turned into dashes, port dropped: `http://gym-norte:8000` is `gym-norte`. A URL that is not `http(s)`, a repeated id, or an id outside `[a-z0-9-]{1,64}` stops the service at startup, naming it. Blank means no agents. |
| `A2A_GATEWAY_TOKEN` | blank | The bearer token the orchestrator must present. Blank uses `development-only-a2a-gateway-token` and logs a warning. |
| `A2A_GATEWAY_CREDENTIAL_<AGENT_ID>` | none | One agent's credential, read by its id upper-cased with dashes turned into underscores: `gym-norte` reads `A2A_GATEWAY_CREDENTIAL_GYM_NORTE`. Blank counts as absent. |
| `A2A_GATEWAY_CARD_TTL_SECONDS` | `300` | How long a judged card is kept before it is read again. Cards are read on first use. Must not be negative. |
| `A2A_GATEWAY_TIMEOUT_SECONDS` | `10` | The ceiling on one send. Must be positive. |
| `A2A_GATEWAY_MAX_TEXT_BYTES` | `16384` | Text a reply may carry, summed over its parts. |
| `A2A_GATEWAY_MAX_DATA_BYTES` | `65536` | JSON data a reply may carry, summed over its parts. |
| `A2A_GATEWAY_OTEL_ENABLED` | `false` | Exports the `a2a.discover` and `a2a.send` spans, under the service name `a2a-gateway`. |
| `A2A_GATEWAY_OTEL_ENDPOINT` | blank | The OTLP/HTTP collector. `/v1/traces` is appended to a base URL; blank uses the SDK's default. |
| `A2A_GATEWAY_SERVICE_VERSION` | `0.1.0` | What `/version` and the trace resource report. |

### Adding an agent

One URL in `A2A_GATEWAY_AGENTS`, plus `A2A_GATEWAY_CREDENTIAL_<ID>` if its card asks for a
credential. No code is written for it, here or in the orchestrator.

### The conformance gate

An agent is usable only when its card passes every check below; the first failure decides its
closed `reason`, it stays in the listing with that reason, and it is never sent anything.

| Reason | When |
| --- | --- |
| `card_unreachable` | The card cannot be fetched from `/.well-known/agent-card.json`. |
| `version_unsupported` | It declares interfaces, none of them 1.x. Checked first, so a 0.3 card reads this rather than `card_invalid`. |
| `card_invalid` | It does not parse, or misses a field A2A 1.0 requires (a card with no interface at all is this). |
| `binding_unsupported` | No 1.x interface uses JSON-RPC or HTTP+JSON. |
| `interface_untrusted` | No such interface is on the host the agent was registered at. |
| `no_skills` | It declares no skill. |
| `auth_unsupported` | Its security requirement is not one `apiKey` header or `http` bearer scheme (OAuth 2.0, OpenID Connect and mutual TLS are not supported). |
| `credential_missing` | The scheme is supported, but no credential is configured. |

The credential travels only in a header — the `apiKey` scheme's header name, or `Authorization:
Bearer` — never in a message, and only to interfaces on the registered host. Agent Card signatures
are not verified: the registry is the trust anchor.

## Contract

Every route but `/health` and `/version` needs `Authorization: Bearer <A2A_GATEWAY_TOKEN>`;
otherwise it answers 401 with the code `unauthorised`. The wire is camelCase.

`GET /v1/agents` lists every registered agent:

```json
{"agents": [{"id": "gym-norte", "name": "Gimnasio Norte", "usable": true, "reason": null,
  "cardDigest": "sha256:…", "linkHosts": ["gym-norte:8000", "127.0.0.1:9101"],
  "skills": [{"id": "timetable", "name": "…", "description": "…", "tags": ["…"],
              "examples": ["…"], "inputModes": ["…"], "outputModes": ["…"]}]}]}
```

An unusable agent is listed with its `reason` and no skills; an agent whose card could not be read
has its id as its name. `linkHosts` are the registered host and the card's `provider.url` host: the
only hosts a link in a reply may be shown for. The card itself, its interfaces and its security
schemes are never returned.

`POST /v1/agents/{id}/messages` sends one message:

```json
{"skillId": "timetable", "messageId": "…", "text": "Busco clases de spinning …",
 "data": {"activity": "spinning", "from": "…", "to": "…", "memberRef": "…"}, "contextId": null}
```

The skill id travels as the message-metadata hint `finguardSkill`, because A2A addresses an agent,
not a skill. The data part is sent only when the skill (or the card's default) accepts
`application/json`. The answer:

```json
{"state": "COMPLETED", "taskId": "…", "contextId": "…",
 "parts": [{"kind": "text", "text": "…"}, {"kind": "data", "data": {"huecos": 4}}],
 "droppedParts": 0, "latencyMs": 812}
```

- `state` is the A2A task state without its prefix: `COMPLETED`, `INPUT_REQUIRED`,
  `AUTH_REQUIRED`, `FAILED`, `REJECTED` or `CANCELED`. A bare `Message` reply is `COMPLETED` with no
  `taskId`. Sends are blocking, so a reply still `SUBMITTED` or `WORKING` is `reply_invalid`.
- `parts` are the task's artifacts, then its status message, in order. `file` parts are dropped and
  counted in `droppedParts`. A whole-number value in a data part comes back as an integer.
- The gateway **never retries** a send; the orchestrator never re-sends a booking.

Errors carry `{"code": …, "reason": …}`:

| Status | `code` | Meaning |
| --- | --- | --- |
| 404 | `agent_unknown` | No registered agent has that id. |
| 409 | `agent_unusable` | The agent failed the gate; `reason` says why. Nothing was sent. |
| 422 | `skill_unknown` | The card has no such skill. Nothing was sent. |
| 502 | `reply_invalid` | The reply is not a valid A2A `Task` or `Message`. |
| 502 | `reply_too_large` | The reply exceeds a limit above. |
| 502 | `agent_error` | The agent or the transport failed. |
| 504 | `timeout` | No reply within `A2A_GATEWAY_TIMEOUT_SECONDS`. |

A request body that is not the shape above gets FastAPI's own 422.

Spans carry only `agent.id`, `usable` and `reason` (`a2a.discover`), and `agent.id`, `skill.id`,
`state`, `dropped_parts`, `latency_ms` and `error.code` (`a2a.send`) — never text, data or
credentials.

## Tests and container

```bash
uv run --directory apps/a2a-gateway pytest -q
uv run --directory apps/a2a-gateway ruff check .
uv run --directory apps/a2a-gateway ruff format --check .
uv run --directory apps/a2a-gateway mypy src
docker build -t finguard-a2a-gateway apps/a2a-gateway
```

The suite (50 tests) is in the CI matrix: Linux and Windows, Python 3.12 and 3.13. It never
imports the simulated gym: `tests/fakes.py` builds its own minimal SDK servers in process, and the gateway
reaches them through an in-memory transport. `test_registry.py` covers the registry,
`test_conformance.py` one case per reason, `test_contract.py` the routes, credentials and parts,
`test_limits.py` the limits, timeouts and the absence of retries, `test_tracing.py` the exporter.

The image is two-stage `python:3.12-slim`, runs as an unprivileged user and listens on port 8000;
compose publishes it on `127.0.0.1:8003` in the `a2a` profile.
