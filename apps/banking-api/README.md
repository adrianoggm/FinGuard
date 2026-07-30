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
| `POST /v1/financial-queries` | Ejecuta un plan de consulta validado. `422` si el plan no cabe en el contrato |

## Consultas financieras

El cuerpo de `POST /v1/financial-queries` es la **representación intermedia** de [vision.md §9.2](../../docs/vision.md), nunca SQL. El modelo de dominio tiene `extra="forbid"`: un campo que no reconoce —`"sql"`, por ejemplo— es un `422`, no algo que se ignore en silencio.

```jsonc
{
  "accountId": "…",
  "direction": "EXPENSE",              // EXPENSE | INCOME | ALL
  "categories": ["TRANSPORT_FUEL"],    // patrón cerrado, máximo 20
  "dateRange": {"from": "2026-07-01", "to": "2026-07-31"},
  "groupBy": "WEEK",                   // NONE | DAY | WEEK | MONTH | CATEGORY | MERCHANT
  "supportingLimit": 3,                // justificantes, máximo 100
  "explain": true                      // devuelve el SQL compilado y sus parámetros
}
```

[`query_compiler.py`](src/finguard_banking/adapters/outbound/postgres/query_compiler.py) traduce ese plan a SQL: **la estructura sale de enums** definidos en el código y **todo valor viaja como parámetro**. Se puede ver con `"explain": true`:

```sql
SELECT to_char(date_trunc('week', t.occurred_at AT TIME ZONE $1), 'YYYY-MM-DD') …
  WHERE e.account_id = $2 AND t.status = 'BOOKED'
    AND t.occurred_at >= (($3::date)::timestamp AT TIME ZONE $1)
    AND t.occurred_at <  (($4::date + 1)::timestamp AT TIME ZONE $1)
    AND e.entry_type = $5 AND t.metadata ->> 'category' = ANY($6)
```

Detalles que cambian el resultado:

| Decisión | Por qué |
| --- | --- |
| El gasto se devuelve **en positivo** | «He gastado 34,20», no «-34,20». Con `direction: ALL` el total va con signo, porque es el neto |
| El día final del rango entra **completo** | Comparar con `<= to` dejaría fuera todo lo posterior a medianoche |
| Se agrupa en `Europe/Madrid` | Configurable con `BANKING_REPORTING_TIMEZONE`. En UTC, una compra de las 23:30 caería en el día siguiente |
| Solo cuenta lo `BOOKED` | Igual que el saldo |
| Se devuelven los movimientos que sustentan el cálculo | La suma tiene que poder comprobarse a mano (§2.1) |
| La traza es opt-in | Expone el SQL; útil para la memoria, no para respuestas normales |

Topes: 500 grupos y 100 justificantes por consulta (§8.6). Sin implementar todavía: `comparison` (comparar con el periodo anterior) y la métrica `AVG`. `metric` viaja en el plan porque es parte del contrato, pero `SUM` y `COUNT` se devuelven siempre las dos: el cliente lee la que pidió.

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
