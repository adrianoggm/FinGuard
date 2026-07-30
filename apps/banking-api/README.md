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

## Esquema

Migraciones con Alembic en [`migrations/`](migrations/). La URL sale de `BANKING_DATABASE_URL`, nunca del `alembic.ini`, para que las migraciones y el servicio no puedan apuntar a bases distintas.

```bash
just migrate                  # alembic upgrade head
just migration "contactos"    # nueva migración vacía
```

`0001_ledger` crea `customers`, `accounts`, `ledger_transactions`, `ledger_entries` y `balance_projections` con el DDL de [vision.md §8.5](../../docs/vision.md), y deja **dos invariantes garantizadas por PostgreSQL**, no por el código que escribe:

| Invariante | Cómo se garantiza | ADR |
| --- | --- | --- |
| Cada transacción cuadra, y cuadra **por moneda** | `CONSTRAINT TRIGGER` diferido al COMMIT | ADR-003 |
| Un asiento no se modifica ni se borra | Trigger `BEFORE UPDATE OR DELETE` | ADR-002 |

Un asiento tampoco puede ir en una moneda distinta a la de su cuenta: 2.500 al debe en USD contra 2.500 al haber en USD cuadra aritméticamente, pero si las cuentas son en euros no es una transacción válida.

De `ledger_transactions` solo se bloquea el `DELETE`: su `status` sí cambia, por ejemplo al pasar a `BOOKED`. Para deshacer un movimiento se anota una transacción compensatoria.

Las pruebas de [`test_ledger_integrity.py`](tests/test_ledger_integrity.py) comprueban cada rechazo contra la base real. Fuerzan los triggers diferidos con `SET CONSTRAINTS ALL IMMEDIATE` y deshacen la transacción al terminar, que es la única limpieza posible sobre un libro mayor inmutable.

Configuración por entorno con prefijo `BANKING_` (`BANKING_DATABASE_URL`). El default apunta a la base local del perfil `core`.

Siguiente: cuentas y libro mayor, Fase 1 del [roadmap](../../docs/roadmap.md).
