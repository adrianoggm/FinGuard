# assistant-api

Orquestador conversacional. Puerto local `8000`.

Mantiene el contexto, interpreta la intención, construye el `FinancialQuery`, selecciona herramientas, coordina consultas y operaciones, genera explicaciones y el plan visual, e integra los proveedores LLM.

No conoce las tablas bancarias ni ejecuta SQL. Llama a `banking-api` mediante el cliente generado desde el contrato.

Contrato público: [`packages/contracts/openapi/assistant-api.yaml`](../../packages/contracts/openapi/assistant-api.yaml) (pendiente).

Pendiente: Fase 3 del [roadmap](../../docs/roadmap.md).
