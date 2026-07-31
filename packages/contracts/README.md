# contracts

The source of truth for communication between services. Only public contracts are shared, never ORM models or internal classes.

| Folder | Contents |
| --- | --- |
| `openapi/` | HTTP APIs: `assistant-api.yaml`, `banking-api.yaml`, `voice-gateway.yaml` |
| `json-schema/` | `financial-query`, `visualization-result`, `payment-preview`, `audit-event` |
| `asyncapi/` | Domain events (from the 3rd iteration onwards) |
| `a2a/` | Agent Card and skill schemas (4th iteration) |
| `generated/` | Python and TypeScript clients generated from the contracts |

The contract is defined **before** the clients are generated. A breaking change is caught in CI, not in production.

See [docs/architecture/monorepo.md §5](../../docs/architecture/monorepo.md#5-contracts-as-the-source-of-truth).
