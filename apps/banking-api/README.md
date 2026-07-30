# banking-api

Núcleo financiero determinista. Sin IA. Puerto local `8001`.

Único servicio con escritura sobre la base financiera: cuentas, libro mayor de doble entrada, saldos, movimientos, contactos, intenciones de pago, Bizum simulado, idempotencia, políticas financieras y publicación de eventos de dominio.

No conoce el LLM ni el contexto conversacional.

Contrato público: [`packages/contracts/openapi/banking-api.yaml`](../../packages/contracts/openapi/banking-api.yaml) (pendiente).

Pendiente: Fase 1 del [roadmap](../../docs/roadmap.md).
