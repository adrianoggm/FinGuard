# contracts

Fuente de verdad de la comunicación entre servicios. Se comparten contratos públicos, nunca modelos ORM ni clases internas.

| Carpeta | Contenido |
| --- | --- |
| `openapi/` | APIs HTTP: `assistant-api.yaml`, `banking-api.yaml`, `voice-gateway.yaml` |
| `json-schema/` | `financial-query`, `visualization-result`, `payment-preview`, `audit-event` |
| `asyncapi/` | Eventos de dominio (a partir de la 3ª iteración) |
| `a2a/` | Agent Card y esquemas de skills (4ª iteración) |
| `generated/` | Clientes Python y TypeScript generados desde los contratos |

El contrato se define **antes** de generar los clientes. Un cambio incompatible se detecta en CI, no en producción.

Ver [docs/architecture/monorepo.md §5](../../docs/architecture/monorepo.md#5-contratos-como-fuente-de-verdad).
