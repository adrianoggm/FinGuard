# assistant-api

Conversational orchestrator. Local port `8000`.

Keeps the context, interprets intent, builds the `FinancialQuery`, selects tools, coordinates queries and operations, produces explanations and the visual plan, and integrates the LLM providers.

It knows nothing about the banking tables and never runs SQL. It calls `banking-api` through the client generated from the contract.

Public contract: [`packages/contracts/openapi/assistant-api.yaml`](../../packages/contracts/openapi/assistant-api.yaml) (pending).

## LLM provider layer

The whole service talks through a single contract, [`ports/llm_provider.py`](src/finguard_assistant/ports/llm_provider.py):

```python
async def complete(self, prompt: str, *, model: str, temperature: float) -> str
```

Which provider is used is decided by the **active policy**, which lives in memory and is swapped **at runtime**, without restarting the service. The service is deliberately not stateless.

| Endpoint | What it does |
| --- | --- |
| `GET /admin/llm-policy` | Active policy and available providers |
| `PUT /admin/llm-policy` | Replaces it. `422` if the provider does not exist, without touching the state |
| `POST /admin/llm-policy/reset` | Back to the startup policy |
| `POST /v1/messages` | Answers using the active policy, and returns it alongside the answer |
| `GET /ready` | `503` if the policy points at a provider that does not exist |

The startup policy comes from configuration (`ASSISTANT_LLM_PROVIDER`, `ASSISTANT_LLM_MODEL`, `ASSISTANT_LLM_TEMPERATURE`, `ASSISTANT_LLM_MAX_OUTPUT_TOKENS`).

**Adding a real provider** means writing a module in [`adapters/outbound/llm/`](src/finguard_assistant/adapters/outbound/llm/) that fulfils the port and registering it in [`registry.py`](src/finguard_assistant/adapters/outbound/llm/registry.py). Neither the domain nor the use cases change. Today only `echo` exists: deterministic, no network calls, useful for development and tests.

Two known limits: the policy lives in the process, so with several replicas each would have its own (Redis, once they exist), and `/admin/*` asks for no credentials yet, so the service cannot leave `localhost` until it sits behind OAuth with an administration scope.

## Running it

```bash
just back            # postgres + banking-api + assistant-api
just serve-assistant # locally with autoreload, on port 8000
just llm-policy      # the active policy
```

Next: conversational context and tool selection, phase 3 of the [roadmap](../../docs/roadmap.md).
