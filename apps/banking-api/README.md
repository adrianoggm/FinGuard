# banking-api

Núcleo financiero determinista. Sin IA. Puerto local `8001`.

Único servicio con escritura sobre la base financiera: cuentas, libro mayor de doble entrada, saldos, movimientos, contactos, intenciones de pago, Bizum simulado, idempotencia, políticas financieras y publicación de eventos de dominio.

No conoce el LLM ni el contexto conversacional.

Contrato público: [`packages/contracts/openapi/banking-api.yaml`](../../packages/contracts/openapi/banking-api.yaml) (pendiente).

## Ejecutar

```bash
just up              # PostgreSQL, si no está levantado
just install         # uv sync
just serve-banking   # http://127.0.0.1:8001
just check           # ruff + mypy + pytest
```

| Endpoint | Comprueba |
| --- | --- |
| `GET /health` | Que el proceso responde. No mira dependencias |
| `GET /ready` | Que PostgreSQL contesta. `503` si no |

Configuración por entorno con prefijo `BANKING_` (`BANKING_DATABASE_URL`). El default apunta a la base local del perfil `core`.

Siguiente: cuentas y libro mayor, Fase 1 del [roadmap](../../docs/roadmap.md).
