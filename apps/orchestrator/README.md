# orchestrator

Conversational orchestrator. Local port `8000`. Swagger at `/docs`, ReDoc at `/redoc`.

Keeps the context, interprets intent, builds the `FinancialQuery`, selects tools, coordinates queries and operations, produces explanations and the visual plan, and integrates the LLM providers.

It knows nothing about the banking tables and never runs SQL. It calls `banking-api` through the client generated from the contract.

Public contract: [`packages/contracts/openapi/orchestrator.yaml`](../../packages/contracts/openapi/orchestrator.yaml) (pending).

## Layers

```text
domain/          value objects, models, ports, errors — no framework, no pydantic
application/     bus/ (QueryBus · CommandBus) · queries/ · commands/ · policy_store.py
infrastructure/  controllers/ · dto/ · mappers/ · llm/ · config/ · error_handlers.py
bootstrap/       composition root
```

CQRS: reads are queries, and swapping the policy is a command, because it changes state.

| Message | Answers with |
| --- | --- |
| `AnswerMessageQuery` | `AnswerMessageResult` — the text, which model served it, and what was attempted first |
| `GetLlmCatalogueQuery` | `CatalogueResult` — active policy, fallback, available models and providers |
| `ReplaceLlmPolicyCommand` | `CatalogueResult` — the state after the swap |
| `ResetLlmPolicyCommand` | `CatalogueResult` |

Commands answer with the resulting state rather than nothing: the endpoint has to reply with it, and a second read to fetch what was just written would be a race waiting to happen.

A **query never answers with a domain model**, so `LlmPolicy` and `LlmModel` stop at the application boundary and a mapper turns the Result into the wire shape.

The domain raises `DomainError` subclasses with a `code`, never bare `ValueError`, and [`error_handlers.py`](src/finguard_orchestrator/infrastructure/error_handlers.py) maps that code to a status: unknown model or provider → `422`, invalid value → `422`, every model failed → `503`, one provider failed → `502`.

## The model is the unit, not the provider

A model carries the provider that serves it, so anything holding an [`LlmModel`](src/finguard_orchestrator/domain/value_objects/llm_model.py) can say where an answer came from without a second lookup. Two models of the same provider are two separate choices.

The **active policy** — which model answers, with what temperature and token budget — lives in memory and is swapped at runtime, without restarting. The service is deliberately not stateless. The swap is atomic in the sense that matters: the policy is frozen and rebound in one assignment, so a reader sees the whole old policy or the whole new one, never a mix.

| Endpoint | What it does |
| --- | --- |
| `GET /admin/llm-policy` | Active policy, fallback policy, available models and providers |
| `PUT /admin/llm-policy` | Swaps it. Only `modelAlias` travels; a `422` if the alias is not in the catalogue, without touching the state |
| `POST /admin/llm-policy/reset` | Back to the startup policy |
| `POST /v1/messages` | Answers with the active model, and reports which models were attempted first |
| `GET /ready` | `503` if this build cannot talk to the active model's provider |

Only the alias travels on a swap: the provider and the model name come from the catalogue, so a caller cannot invent a pairing this deployment does not have.

## The catalogue is configuration

Which models exist, which provider serves each one and what happens when one fails come from a TOML file, not from code — `tomllib` is in the standard library, so it costs no dependency. Point `ORCHESTRATOR_LLM_CATALOGUE_FILE` at it; with no file the service falls back to a built-in `echo` catalogue and still starts.

```toml
fallback = "NEXT"        # FAIL | NEXT
default = "echo-1"
temperature = 0.2
max_output_tokens = 1024

[[models]]
alias = "echo-1"
provider = "echo"
model = "echo-1"

[[models]]
alias = "echo-2"
provider = "echo"
model = "echo-2"
```

`FAIL` surfaces the error, so a measurement run cannot be skewed by a silent substitution. `NEXT` walks the rest of the catalogue in file order and records every alias it tried, so a degraded answer stays distinguishable from a clean one:

```jsonc
{"text": "…", "policy": {"model": {"alias": "backup", "provider": "echo", …}},
 "attempted": ["primary"]}          // non-empty means a fallback served this
```

See [`config/llm.toml`](config/llm.toml) for the annotated file used in development.

**Adding a real provider** is three steps: a module in [`infrastructure/llm/`](src/finguard_orchestrator/infrastructure/llm/) that fulfils [`LlmProviderPort`](src/finguard_orchestrator/domain/ports/llm_provider.py), one line in [`provider_registry.py`](src/finguard_orchestrator/infrastructure/llm/provider_registry.py), and an entry in the catalogue file. Neither the domain nor the use cases change. Today only `echo` exists: deterministic, no network calls.

Known limit: the policy lives in this process, so with several replicas each would have its own. `/admin/*` asks for no credentials yet, which is why nothing is published beyond `localhost`.

## Conversational routing

A turn is analysed as an intent, a relationship to the conversation (new request, slot answer,
follow-up or correction), and a focus. Two substantive clauses are treated as a compound request:
neither is executed until the user chooses one of the returned `suggestions`.

The `hybrid` interpreter keeps every deterministic match and asks the semantic reader only about a
plausible catch-all. A model cannot create a payment or an own-account transfer, and explicit
movement questions cannot be changed into a balance. If the financial planner violates its JSON
schema, it receives one schema-only repair attempt; domain and safety rejections are never retried
or coerced.

The deterministic router currently preserves 95 of 96 independently generated paraphrases across
twelve intent groups. The remaining phrase no longer says that either account belongs to the
holder, so it deliberately stays in the semantic fallback instead of being promoted to an action.

Payment amounts may be written as figures or Spanish cardinal words. Word amounts are parsed again
outside the model and must match exactly before a preview can be produced. Explicit payment
concepts are also recovered outside the model: known instruction suffixes are excluded, places such
as `viaje a Málaga` keep their preposition, and a concept invented by the model is rejected.
Balance questions,
financial filters and own-account transfers can also use the holder's custom account display names;
generic labels such as `Hucha` remain ambiguous when several accounts share that kind.

## Operational settings

The values live in [`.env.defaults`](.env.defaults) — versioned, free of secrets, and the
only place they exist. There is no number in the Python: the settings fields declare a type
and nothing else, and the adapters take them as required arguments, so a provider nobody
configured cannot be built.

`.env` is loaded after it and wins. That is where a token table or a real credential goes,
and it is gitignored.

**Without configuration the service refuses to start**, field by field, rather than
inventing a timeout. That is deliberate: a service that guesses its own operational numbers
is a service nobody configured.

Each value is a measurement rather than a preference, and the file says which experiment
produced it beside the value — because a default with no reason next to it is the next thing
somebody changes.

| variable | default | what it is |
| --- | --- | --- |
| `ORCHESTRATOR_PLANNER_MODEL` | `qwen2.5-coder:7b` | The model that turns a sentence into a plan |
| `ORCHESTRATOR_PLANNER_TEMPERATURE` | `0.0` | Zero on purpose: a plan that varies between identical questions cannot be measured |
| `ORCHESTRATOR_PLANNER_MAX_OUTPUT_TOKENS` | `384` | The plan is ~120 tokens; this is headroom, not a target |
| `ORCHESTRATOR_OLLAMA_CONTEXT_TOKENS` | `4096` | Raising it grows the KV cache, and a model that no longer fits is **silently** split with the CPU. This one took P50 from 5.28s to 2.58s |
| `ORCHESTRATOR_OLLAMA_SEED` | `0` | Pinned. Unpinned, the same prompt against the same model moved a score by three points |
| `ORCHESTRATOR_OLLAMA_KEEP_ALIVE` | `30m` | How long the model stays resident. A cold load is ~7s against ~0.1s warm |
| `ORCHESTRATOR_OLLAMA_TIMEOUT_SECONDS` | `120` | A ceiling for "something is wrong", not a latency target. A model that does not fit the GPU needs it raised |
| `ORCHESTRATOR_BANKING_TIMEOUT_SECONDS` | `10` | |
| `ORCHESTRATOR_SUPPORTING_MOVEMENTS` | `10` | Movements returned as evidence. Enough to show the figure is real, few enough that a chat reply is not a statement |
| `ORCHESTRATOR_TURN_INTERPRETER` | `router` | `router`, `shadow`, conservative `hybrid`, or model authority. Compose opts into `hybrid` |
| `ORCHESTRATOR_ACCOUNT_SCOPE_DEFAULT` | `all` | A spending or income question that names no account, with none chosen yet: every account (`all`), or «¿En qué cuenta quieres consultarlo?» first (`ask`, the behaviour until 2026-10-07) |
| `ORCHESTRATOR_DISABLED_GUARDS` | *(none)* | Deterministic rules to switch off, by name. A name this build does not have stops the service |
| `ORCHESTRATOR_QUESTIONS_ENABLED` | `false` | The financial endpoints. Off until the open risks clear an agreed bar |

## Identity, authorisation and scopes

A token entry is `token:holder:account_uuid[:timezone[:scopes]]`. The zone is the holder's,
because "hoy" belongs to whoever is asking. The scopes are comma-separated and default to
the two read permissions — **not** the admin one, so swapping the model that answers every
question is something somebody was allowed to do rather than something anybody on the
network can do.

| scope | opens |
| --- | --- |
| `questions:read` | `POST /v1/questions` and its streaming twin |
| `assistant:use` | `POST /v1/messages` — a prompt straight to a model, with no plan, no verifier and no ledger |
| `assistant:admin` | every `/admin/*` route |
| `payments:prepare` | phase 5: build an intent and see its preview |
| `payments:confirm` | phase 5: move the money. Deliberately not implied by prepare — a human confirmation step is meaningless if the credential that drafted the operation can also complete it |

An unknown scope name stops the service at startup rather than granting nothing quietly. A
caller who believes they have a permission and does not is a support call; an unvalidated
string is worse, because it becomes a real permission the day somebody adds a scope with
that spelling.

Still not OAuth. §12.2 asks for short-lived tokens from an issuer, and what exists is the
seam: the account and the permissions come from the credential, so swapping the issuer is a
change of adapter rather than a change of design.

**The loopback default is not a trust boundary.** `just app`, every demo, and any deployment
that never sets `ORCHESTRATOR_METRICS_SIGNING_KEYS` seal every visual context and every saved
metric — not only the ones metrics-flagged endpoints touch — with the development key
committed in this repository, forgeable by anyone who reads the source. `bootstrap/main.py`
logs one warning naming this at startup, and refuses to start on that key the moment
`ORCHESTRATOR_WEB_ORIGINS` names a non-loopback origin (A-13); short of that, an "altered
context is refused" demonstration run against `just app` alone proves nothing about the
signature — set a real signing key first.

## Running it

```bash
just back            # postgres + banking-api + orchestrator
just serve-orchestrator # locally with autoreload, on port 8000
just llm-policy      # the active policy
just check           # ruff + mypy + tests
```

Next: conversational context and tool selection, phase 3 of the [roadmap](../../docs/roadmap.md).
