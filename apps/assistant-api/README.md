# assistant-api

Conversational orchestrator. Local port `8000`. Swagger at `/docs`, ReDoc at `/redoc`.

Keeps the context, interprets intent, builds the `FinancialQuery`, selects tools, coordinates queries and operations, produces explanations and the visual plan, and integrates the LLM providers.

It knows nothing about the banking tables and never runs SQL. It calls `banking-api` through the client generated from the contract.

Public contract: [`packages/contracts/openapi/assistant-api.yaml`](../../packages/contracts/openapi/assistant-api.yaml) (pending).

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

The domain raises `DomainError` subclasses with a `code`, never bare `ValueError`, and [`error_handlers.py`](src/finguard_assistant/infrastructure/error_handlers.py) maps that code to a status: unknown model or provider → `422`, invalid value → `422`, every model failed → `503`, one provider failed → `502`.

## The model is the unit, not the provider

A model carries the provider that serves it, so anything holding an [`LlmModel`](src/finguard_assistant/domain/value_objects/llm_model.py) can say where an answer came from without a second lookup. Two models of the same provider are two separate choices.

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

Which models exist, which provider serves each one and what happens when one fails come from a TOML file, not from code — `tomllib` is in the standard library, so it costs no dependency. Point `ASSISTANT_LLM_CATALOGUE_FILE` at it; with no file the service falls back to a built-in `echo` catalogue and still starts.

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

**Adding a real provider** is three steps: a module in [`infrastructure/llm/`](src/finguard_assistant/infrastructure/llm/) that fulfils [`LlmProviderPort`](src/finguard_assistant/domain/ports/llm_provider.py), one line in [`provider_registry.py`](src/finguard_assistant/infrastructure/llm/provider_registry.py), and an entry in the catalogue file. Neither the domain nor the use cases change. Today only `echo` exists: deterministic, no network calls.

Known limit: the policy lives in this process, so with several replicas each would have its own. `/admin/*` asks for no credentials yet, which is why nothing is published beyond `localhost`.

## Running it

```bash
just back            # postgres + banking-api + assistant-api
just serve-assistant # locally with autoreload, on port 8000
just llm-policy      # the active policy
just check           # ruff + mypy + tests
```

Next: conversational context and tool selection, phase 3 of the [roadmap](../../docs/roadmap.md).
