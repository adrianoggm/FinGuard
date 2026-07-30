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

| Endpoint | Qué hace |
| --- | --- |
| `GET /health` | Confirma que el proceso responde. No mira dependencias |
| `GET /ready` | Confirma que PostgreSQL contesta. `503` si no |
| `GET /v1/accounts/{id}/balance` | Saldo derivado del libro mayor. `404` si la cuenta no existe |

## Saldo

El saldo **se calcula sumando los asientos**, no se lee de una columna mutable: el libro mayor es la fuente de verdad (ADR-001). Solo cuentan los asientos de transacciones `BOOKED`.

El signo es el del titular: un `DEBIT` sobre su cuenta le quita dinero, un `CREDIT` se lo da. Es el criterio del ejemplo de Bizum del spec, donde enviar 25 € se anota como débito en la cuenta de quien envía.

```
GET /v1/accounts/{id}/balance
{"accountId":"…","availableMinor":146035,"bookedMinor":146035,"currency":"EUR","asOf":"…"}
```

`available` y `booked` coinciden hoy y dejarán de coincidir en Fase 5, cuando existan importes reservados por operaciones pendientes de confirmar. `balance_projections` está creada pero **sin usar**: se gana su sitio en Fase 5, cuando haga falta bloquear una fila con `FOR UPDATE` para cobrar sin condiciones de carrera.

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

## Datos de desarrollo

El dataset es un **artefacto versionado**, no algo que cada máquina genere distinto: los tests futuros, el benchmark de la Fase 2 y la demo trabajan sobre exactamente los mismos movimientos.

```bash
just seed            # carga seeds/0001_demo.sql y lo verifica. CUIDADO: empieza con TRUNCATE
just seed-verify     # comprueba lo que haya cargado ahora mismo
just seed-generate   # regenera el SQL
```

[`seeds/0001_demo.sql`](seeds/0001_demo.sql) trae 1.624 transacciones y 3.248 asientos: 3 titulares con nómina, alquiler, suscripciones, supermercado, gasolina, restaurantes, transporte, salud, ocio y bizums entre 2025-02 y 2026-07, más la cuenta de compensación que hace de contrapartida de todo.

| Propiedad | Por qué |
| --- | --- |
| Determinista | Misma semilla → mismo fichero byte a byte. Regenerar y encontrar `git diff` vacío es la prueba |
| `category` y `merchant_name` en `metadata` | Es lo que consultará el motor de la Fase 2 (§9.2) |
| 3 transacciones `PENDING` | Para poder comprobar que el saldo ignora lo que no está contabilizado |
| Fechas fijas | Reproducibilidad. El precio es que envejecen: para la demo, `--end-date` y regenerar |

[`seeds/verify.sql`](seeds/verify.sql) **falla** —no avisa— si alguna transacción no cuadra, si hay asientos en otra moneda que su cuenta, o si hay transacciones sin asientos. Medir un benchmark sobre datos incoherentes es peor que no medirlo.

Los tests de integración **no** dependen de este dataset: crean sus propios datos y los deshacen. Quien lo necesita es la evaluación de la Fase 2.

Las pruebas de [`test_ledger_integrity.py`](tests/test_ledger_integrity.py) comprueban cada rechazo contra la base real. Fuerzan los triggers diferidos con `SET CONSTRAINTS ALL IMMEDIATE` y deshacen la transacción al terminar, que es la única limpieza posible sobre un libro mayor inmutable.

Configuración por entorno con prefijo `BANKING_` (`BANKING_DATABASE_URL`). El default apunta a la base local del perfil `core`.

Siguiente: cuentas y libro mayor, Fase 1 del [roadmap](../../docs/roadmap.md).
