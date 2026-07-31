# contracts

The source of truth for communication between services. Only public contracts are shared, never ORM models or internal classes.

| Folder | Contents |
| --- | --- |
| `openapi/` | HTTP APIs: `assistant-api.yaml`, `banking-api.yaml`, `voice-gateway.yaml` |
| `json-schema/` | `financial-query`, `visualization-result`, `payment-preview`, `audit-event` |
| `asyncapi/` | Domain events (from the 3rd iteration onwards) |
| `a2a/` | Agent Card and skill schemas (4th iteration) |

These files are written by hand and are not generated from any running service, and no client is generated from them either: each consumer writes its own. That keeps every component buildable on its own, at the cost of correcting a drift between a service and its contract manually.

See [docs/architecture/monorepo.md §5](../../docs/architecture/monorepo.md#5-contracts-as-the-source-of-truth).
