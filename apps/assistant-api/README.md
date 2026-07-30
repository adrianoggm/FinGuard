# assistant-api

Orquestador conversacional. Puerto local `8000`.

Mantiene el contexto, interpreta la intención, construye el `FinancialQuery`, selecciona herramientas, coordina consultas y operaciones, genera explicaciones y el plan visual, e integra los proveedores LLM.

No conoce las tablas bancarias ni ejecuta SQL. Llama a `banking-api` mediante el cliente generado desde el contrato.

Contrato público: [`packages/contracts/openapi/assistant-api.yaml`](../../packages/contracts/openapi/assistant-api.yaml) (pendiente).

## Capa de proveedores de LLM

Todo el servicio habla con un único contrato, [`ports/llm_provider.py`](src/finguard_assistant/ports/llm_provider.py):

```python
async def complete(self, prompt: str, *, model: str, temperature: float) -> str
```

Qué proveedor se usa lo decide la **política activa**, que vive en memoria y se cambia **en caliente**, sin reiniciar el servicio. El servicio no es stateless a propósito.

| Endpoint | Qué hace |
| --- | --- |
| `GET /admin/llm-policy` | Política activa y proveedores disponibles |
| `PUT /admin/llm-policy` | La sustituye. `422` si el proveedor no existe, sin tocar el estado |
| `POST /admin/llm-policy/reset` | Vuelve a la política de arranque |
| `POST /v1/messages` | Responde con la política activa, y la devuelve en la respuesta |
| `GET /ready` | `503` si la política apunta a un proveedor que no existe |

La política de arranque viene de la configuración (`ASSISTANT_LLM_PROVIDER`, `ASSISTANT_LLM_MODEL`, `ASSISTANT_LLM_TEMPERATURE`, `ASSISTANT_LLM_MAX_OUTPUT_TOKENS`).

**Añadir un proveedor real** es escribir un módulo en [`adapters/outbound/llm/`](src/finguard_assistant/adapters/outbound/llm/) que cumpla el puerto y registrarlo en [`registry.py`](src/finguard_assistant/adapters/outbound/llm/registry.py). Ni el dominio ni los casos de uso cambian. Hoy solo está `echo`, determinista y sin llamadas de red, que sirve para desarrollo y tests.

Dos límites conocidos: la política vive en el proceso, así que con varias réplicas cada una tendría la suya (toca Redis cuando existan), y `/admin/*` todavía no pide credenciales, así que el servicio no puede salir de `localhost` hasta que esté detrás de OAuth con scope de administración.

## Ejecutar

```bash
just back            # postgres + banking-api + assistant-api
just serve-assistant # en local con recarga, en el 8000
just llm-policy      # política activa
```

Siguiente: contexto conversacional y selección de herramientas, Fase 3 del [roadmap](../../docs/roadmap.md).
