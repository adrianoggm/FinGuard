# Conversaciones reales — 2026-10-11, `35d7101b`

**Preflight** (2026-10-11T12:37:58+02:00)

- Servicios: orchestrator ok · banking ok · gateway skipped (not used by this evaluation)
- Orquestador: `35d7101b` (task/legacy-removal) · HEAD `35d7101b` · source `1f04c049701ce9a8fdf459336cc1f61ca8040aa374731534ee4816ea61d29ced` · código del producto: igual que el código local
- Banking API: `35d7101b` (task/legacy-removal) · source `9cd9a8ca27c731603fb5eb407e13930e2e9bef6f38e9e82d577ba384169e9f43`
- Libro: último movimiento 2026-10-07 (hoy 2026-10-11) · antigüedad aceptada a mano: 4 días
- GPU: P5, 352 MHz · Ollama en memoria: qwen2.5-coder:7b

## Resumen

Sin ejecución anterior con la que comparar.

| Mecanismo | Turnos | Fallidos | Sin juzgar | Acierto | Antes |
|---|---:|---:|---:|---:|---:|
| comparison | 3 | 1 | 0 | 67 % | — |
| follow_up | 11 | 1 | 0 | 91 % | — |
| intent | 25 | 1 | 0 | 96 % | — |
| interruption | 2 | 0 | 0 | 100 % | — |
| pending_resolution | 6 | 0 | 0 | 100 % | — |
| reference | 2 | 1 | 0 | 50 % | — |
| reset | 2 | 0 | 0 | 100 % | — |
| slot_carryover | 9 | 2 | 0 | 78 % | — |

### Gestiones de las sesiones

| Gestión | Turnos | Estado | Fallidos | Sin juzgar | Invariantes |
|---|---:|---|---:|---:|---|
| sesion-1-consultas-y-correcciones / consulta-de-contactos | 1 | completa | 0 | 0 | — |
| sesion-1-consultas-y-correcciones / consulta-de-supermercado | 1 | completa | 0 | 0 | — |
| sesion-1-consultas-y-correcciones / retomar-supermercado-y-cambiar-filtros | 6 | fallida | 2 | 0 | — |
| sesion-1-consultas-y-correcciones / saldo-de-hucha | 1 | completa | 0 | 0 | — |
| sesion-1-consultas-y-correcciones / suscripciones-y-cambios-de-comercio | 5 | completa | 0 | 0 | — |
| sesion-2-aclaraciones-y-erratas / cambio-entre-categorias | 4 | completa | 0 | 0 | — |
| sesion-2-aclaraciones-y-erratas / comparacion-de-restaurantes-y-gasolina | 2 | completa | 0 | 0 | — |
| sesion-2-aclaraciones-y-erratas / desglose-de-supermercado-y-reagrupacion | 5 | fallida | 1 | 0 | — |
| sesion-2-aclaraciones-y-erratas / primer-saldo | 1 | completa | 0 | 0 | — |
| sesion-2-aclaraciones-y-erratas / segundo-saldo | 1 | completa | 0 | 0 | — |
| sesion-3-pagos-y-comparaciones / comparar-categorias-y-seleccionar-periodo | 3 | fallida | 2 | 0 | — |
| sesion-3-pagos-y-comparaciones / consultar-bizums-por-direccion | 2 | completa | 0 | 0 | — |
| sesion-3-pagos-y-comparaciones / consultar-contactos | 1 | completa | 0 | 0 | — |
| sesion-3-pagos-y-comparaciones / preparar-y-cancelar-primer-bizum | 4 | completa | 0 | 0 | — |
| sesion-3-pagos-y-comparaciones / preparar-y-cancelar-segundo-bizum | 2 | completa | 0 | 0 | — |
| sesion-3-pagos-y-comparaciones / primer-saldo | 1 | completa | 0 | 0 | — |
| sesion-3-pagos-y-comparaciones / segundo-saldo | 1 | completa | 0 | 0 | — |
| sesion-4-graficos-y-recuperacion / corregir-consulta-y-cancelar-borrador | 4 | completa | 0 | 0 | — |
| sesion-4-graficos-y-recuperacion / gastos-de-restaurantes-y-seguimiento | 2 | completa | 0 | 0 | — |
| sesion-4-graficos-y-recuperacion / ingresos-y-seguimiento-de-periodo | 2 | completa | 0 | 0 | — |
| sesion-4-graficos-y-recuperacion / quitar-top-n-y-visualizar-datos | 6 | fallida | 3 | 0 | — |

- Recuperación (tras un turno fallido, ¿el siguiente va bien?): 3 de 5 (60 %)
- Fallos de disponibilidad (también fallan la sesión): provider 0 · banking 0 · upstream_http 0 · transport 0
- Provider failure attempts (safe category): ninguno registrado
- Terminal provider-call failures: none recorded
- Turnos intentados / omitidos / previstos: 60 / 0 / 60
- Cifras contra el libro: 24 match · 0 mismatch · 0 sin poder comprobar (error) · 36 n/a
- Latencia por turno: mediana 10,5 s · p95 23,3 s · máx 24,6 s (60 turnos)
  - motor `model`: mediana 10,5 s · p95 23,3 s · máx 24,6 s (60 turnos)
- Conversaciones logradas (todos los turnos bien y objetivo cumplido): 0 de 4 juzgadas (0 %) · 0 sin juzgar
- Llamadas al modelo por turno: media 0,97 · p95 1 (60 turnos con dato)
- Peticiones HTTP reales al proveedor (incluye reintentos/fallback): 58 en 60 turnos · media 0,97 · p95 1
- Modelos intentados/usados: `gemini-3.5-flash-lite`: 58 intentos, 58 respuestas
- Tokens informados por proveedor: 245231 total en 58 turnos (241289 entrada, 3942 salida; 58 respuestas con usage)

## sesion-1-consultas-y-correcciones

Objetivo: Mantener consultas y filtros a trav?s de cambios de tema y correcciones. · canal: API

**1.** «q llevo gastado en suscripciones estos ultimos 9 meses» → `PLANNED` · 1,6 s

> 610,64 € en suscripciones en los últimos 9 meses, en 31 movimientos.
> Consultado en todas tus cuentas.

- Consulta: en los últimos 9 meses, gastos, suscripciones · Cifra: 610,64 € en 31 mov. · SQL: 610,64 € en 31 mov. · Consulta comprobada: `{"categories": ["SUBSCRIPTIONS"], "direction": "EXPENSE", "end": null, "group_by": "NONE", "group_limit": null, "merchant_terms": [], "merchants": [], "metric": "SUM", "n": 9, "period": "LAST_N_MONTHS", "rank": null, "start": null}` · **match** (comprobada)
- Grupos: total 610,64 €
- Esperado: `intent` QUERY — En el corpus UX: {ran: {categories: [SUBSCRIPTIONS], window: 'LAST_N_MONTHS:9'}}

**2.** «solo Netflix» → `PLANNED` · 2,5 s

> 125,91 € en Netflix en los últimos 9 meses, en 9 movimientos.
> Consultado en todas tus cuentas.

- Consulta: en los últimos 9 meses, gastos, suscripciones, Netflix · Cifra: 125,91 € en 9 mov. · SQL: 125,91 € en 9 mov. · Consulta comprobada: `{"categories": ["SUBSCRIPTIONS"], "direction": "EXPENSE", "end": null, "group_by": "NONE", "group_limit": null, "merchant_terms": [], "merchants": ["Netflix"], "metric": "SUM", "n": 9, "period": "LAST_N_MONTHS", "rank": null, "start": null}` · **match** (comprobada)
- Grupos: total 125,91 €
- Esperado: `slot_carryover` QUERY — En el corpus UX: {ran: {merchants: [Netflix], window: 'LAST_N_MONTHS:9'}}

**3.** «y este mes?» → `PLANNED` · 1,2 s

> 13,99 € en Netflix en lo que va de mes, en 1 movimiento.
> Consultado en todas tus cuentas.

- Consulta: en lo que va de mes, gastos, suscripciones, Netflix · Cifra: 13,99 € en 1 mov. · SQL: 13,99 € en 1 mov. · Consulta comprobada: `{"categories": ["SUBSCRIPTIONS"], "direction": "EXPENSE", "end": null, "group_by": "NONE", "group_limit": null, "merchant_terms": [], "merchants": ["Netflix"], "metric": "SUM", "n": null, "period": "THIS_MONTH", "rank": null, "start": null}` · **match** (comprobada)
- Grupos: total 13,99 €
- Esperado: `follow_up` QUERY — En el corpus UX: {ran: {merchants: [Netflix], window: THIS_MONTH}}

**4.** «no, Spotify» → `PLANNED` · 2,4 s

> 10,99 € en Spotify en lo que va de mes, en 1 movimiento.
> Consultado en todas tus cuentas.

- Consulta: en lo que va de mes, gastos, suscripciones, Spotify · Cifra: 10,99 € en 1 mov. · SQL: 10,99 € en 1 mov. · Consulta comprobada: `{"categories": ["SUBSCRIPTIONS"], "direction": "EXPENSE", "end": null, "group_by": "NONE", "group_limit": null, "merchant_terms": [], "merchants": ["Spotify"], "metric": "SUM", "n": null, "period": "THIS_MONTH", "rank": null, "start": null}` · **match** (comprobada)
- Grupos: total 10,99 €
- Esperado: `slot_carryover` QUERY — En el corpus UX: {ran: {merchants: [Spotify], window: THIS_MONTH}}

**5.** «y el mes pasado?» → `PLANNED` · 1,4 s

> 10,99 € en Spotify el mes pasado, en 1 movimiento.
> Consultado en todas tus cuentas.

- Consulta: el mes pasado, gastos, suscripciones, Spotify · Cifra: 10,99 € en 1 mov. · SQL: 10,99 € en 1 mov. · Consulta comprobada: `{"categories": ["SUBSCRIPTIONS"], "direction": "EXPENSE", "end": null, "group_by": "NONE", "group_limit": null, "merchant_terms": [], "merchants": ["Spotify"], "metric": "SUM", "n": null, "period": "LAST_MONTH", "rank": null, "start": null}` · **match** (comprobada)
- Grupos: total 10,99 €
- Esperado: `follow_up` QUERY — En el corpus UX: {ran: {merchants: [Spotify], window: LAST_MONTH}}

**6.** «gracias» → `COURTESY` · 2,3 s

> De nada. Si necesitas algo más de tus cuentas, dímelo.

- **n/a** (COURTESY: no es la cifra de una consulta)
- Esperado: `intent` COURTESY

**7.** «cuanto gaste en el super en julio de 2026» → `PLANNED` · 4,2 s

> 449,62 € en supermercado del 2026-07-01 al 2026-07-31, en 8 movimientos.
> Consultado en todas tus cuentas.

- Consulta: del 2026-07-01 al 2026-07-31, gastos, supermercado · Cifra: 449,62 € en 8 mov. · SQL: 449,62 € en 8 mov. · Consulta comprobada: `{"categories": ["GROCERIES"], "direction": "EXPENSE", "end": "2026-07-31", "group_by": "NONE", "group_limit": null, "merchant_terms": [], "merchants": [], "metric": "SUM", "n": null, "period": null, "rank": null, "start": "2026-07-01"}` · **match** (comprobada)
- Grupos: total 449,62 €
- Esperado: `intent` QUERY — En el corpus UX: {ran: {categories: [GROCERIES], window: 2026-07-01..2026-07-31}}

**8.** «cuanto tengo en la hucha» → `BALANCE` · 1,5 s

> Tienes 6.405,00 € en Hucha.

- Consulta: saldo de Hucha · Cifra: 6.405,00 € · **n/a** (BALANCE: no es la cifra de una consulta)
- Esperado: `intent` BALANCE

**9.** «quienes son mis contactos» → `CONTACTS` · 15,6 s

> Tienes estos contactos para enviar dinero: Aitor Beltran Diaz, Carmen Nieto Lara, Marta Ruiz Gomez, Marta Solis Vega.

- **n/a** (CONTACTS: no es la cifra de una consulta)
- Esperado: `intent` CONTACTS

**10.** «volviendo al super, y en junio de 2026?» → `PLANNED` · 23,5 s

> 506,93 € en supermercado del 2026-06-01 al 2026-06-30, en 8 movimientos.
> Consultado en todas tus cuentas.

- Consulta: del 2026-06-01 al 2026-06-30, gastos, supermercado · Cifra: 506,93 € en 8 mov. · SQL: 506,93 € en 8 mov. · Consulta comprobada: `{"categories": ["GROCERIES"], "direction": "EXPENSE", "end": "2026-06-30", "group_by": "NONE", "group_limit": null, "merchant_terms": [], "merchants": [], "metric": "SUM", "n": null, "period": null, "rank": null, "start": "2026-06-01"}` · **match** (comprobada)
- Grupos: total 506,93 €
- Esperado: `interruption` QUERY — En el corpus UX: {ran: {categories: [GROCERIES], window: 2026-06-01..2026-06-30}}

**11.** «solo Mercadona» → `PLANNED` · 13,0 s

> 86,57 € en Mercadona del 2026-06-01 al 2026-06-30, en 1 movimiento.
> Consultado en todas tus cuentas.

- Consulta: del 2026-06-01 al 2026-06-30, gastos, supermercado, Mercadona · Cifra: 86,57 € en 1 mov. · SQL: 86,57 € en 1 mov. · Consulta comprobada: `{"categories": ["GROCERIES"], "direction": "EXPENSE", "end": "2026-06-30", "group_by": "NONE", "group_limit": null, "merchant_terms": [], "merchants": ["Mercadona"], "metric": "SUM", "n": null, "period": null, "rank": null, "start": "2026-06-01"}` · **match** (comprobada)
- Grupos: total 86,57 €
- Esperado: `slot_carryover` QUERY — En el corpus UX: {ran: {merchants: [Mercadona], window: 2026-06-01..2026-06-30}}

**12.** «y en julio de 2026?» → `PLANNED` · 23,2 s

> 131,21 € en Mercadona del 2026-07-01 al 2026-07-31, en 2 movimientos.
> Consultado en todas tus cuentas.

- Consulta: del 2026-07-01 al 2026-07-31, gastos, supermercado, Mercadona · Cifra: 131,21 € en 2 mov. · SQL: 131,21 € en 2 mov. · Consulta comprobada: `{"categories": ["GROCERIES"], "direction": "EXPENSE", "end": "2026-07-31", "group_by": "NONE", "group_limit": null, "merchant_terms": [], "merchants": ["Mercadona"], "metric": "SUM", "n": null, "period": null, "rank": null, "start": "2026-07-01"}` · **match** (comprobada)
- Grupos: total 131,21 €
- Esperado: `follow_up` QUERY — En el corpus UX: {ran: {merchants: [Mercadona], window: 2026-07-01..2026-07-31}}

**13.** «gastos de julio de 2026» → `PLANNED` · 12,8 s

> 131,21 € en Mercadona del 2026-07-01 al 2026-07-31, en 2 movimientos.
> Consultado en todas tus cuentas.

- Consulta: del 2026-07-01 al 2026-07-31, gastos, supermercado, Mercadona · Cifra: 131,21 € en 2 mov. · SQL: 131,21 € en 2 mov. · Consulta comprobada: `{"categories": ["GROCERIES"], "direction": "EXPENSE", "end": "2026-07-31", "group_by": "NONE", "group_limit": null, "merchant_terms": [], "merchants": ["Mercadona"], "metric": "SUM", "n": null, "period": null, "rank": null, "start": "2026-07-01"}` · **match** (comprobada)
- Grupos: total 131,21 €
- Esperado: `intent` QUERY — En el corpus UX: {ran: {window: 2026-07-01..2026-07-31}}

**14.** «perdon junio de 2026» → `PLANNED` · 10,3 s

> 86,57 € en Mercadona del 2026-06-01 al 2026-06-30, en 1 movimiento.
> Consultado en todas tus cuentas.

- Consulta: del 2026-06-01 al 2026-06-30, gastos, supermercado, Mercadona · Cifra: 86,57 € en 1 mov. · SQL: 86,57 € en 1 mov. · Consulta comprobada: `{"categories": ["GROCERIES"], "direction": "EXPENSE", "end": "2026-06-30", "group_by": "NONE", "group_limit": null, "merchant_terms": [], "merchants": ["Mercadona"], "metric": "SUM", "n": null, "period": null, "rank": null, "start": "2026-06-01"}` · **match** (comprobada)
- Grupos: total 86,57 €
- Esperado: `slot_carryover` QUERY — En el corpus UX: {ran: {window: 2026-06-01..2026-06-30}}

**15.** «solo gasolina» → `PLANNED` · 11,3 s

> 102,80 € en gasolina del 2026-06-01 al 2026-06-30, en 2 movimientos.
> Consultado en todas tus cuentas.

- Consulta: del 2026-06-01 al 2026-06-30, gastos, gasolina · Cifra: 102,80 € en 2 mov. · SQL: 102,80 € en 2 mov. · Consulta comprobada: `{"categories": ["TRANSPORT_FUEL"], "direction": "EXPENSE", "end": "2026-06-30", "group_by": "NONE", "group_limit": null, "merchant_terms": [], "merchants": [], "metric": "SUM", "n": null, "period": null, "rank": null, "start": "2026-06-01"}` · **match** (comprobada)
- Grupos: total 102,80 €
- Esperado: `slot_carryover` QUERY — En el corpus UX: {ran: {categories: [TRANSPORT_FUEL], window: 2026-06-01..2026-06-30}}

| Turno | Esperado | Cifras | intent | context | pending | clarification | recovery | naturalness 1-5 | mechanism | notes | s |
|---|---|---|---|---|---|---|---|---|---|---|---:|
| 1 | intent | match |  |  |  |  |  |  | intent |  | 1,6 s |
| 2 | slot_carryover | match |  |  |  |  |  |  | slot_carryover |  | 2,5 s |
| 3 | follow_up | match |  |  |  |  |  |  | follow_up |  | 1,2 s |
| 4 | slot_carryover | match |  |  |  |  |  |  | slot_carryover |  | 2,4 s |
| 5 | follow_up | match |  |  |  |  |  |  | follow_up |  | 1,4 s |
| 6 | intent | n/a |  |  |  |  |  |  | intent |  | 2,3 s |
| 7 | intent | match |  |  |  |  |  |  | intent |  | 4,2 s |
| 8 | intent | n/a |  |  |  |  |  |  | intent |  | 1,5 s |
| 9 | intent | n/a |  |  |  |  |  |  | intent |  | 15,6 s |
| 10 | interruption | match |  |  |  |  |  |  | interruption |  | 23,5 s |
| 11 | slot_carryover | match |  |  |  |  |  |  | slot_carryover |  | 13,0 s |
| 12 | follow_up | match |  |  |  |  |  |  | follow_up |  | 23,2 s |
| 13 | intent | match |  | fail |  |  |  |  | intent | «gastos de julio de 2026» es una petición completa nueva: arrastra el filtro Mercadona del turno anterior y repite la respuesta del turno 12. | 12,8 s |
| 14 | slot_carryover | match |  |  |  |  |  |  | slot_carryover |  | 10,3 s |
| 15 | slot_carryover | match |  |  |  |  |  |  | slot_carryover |  | 11,3 s |

## sesion-2-aclaraciones-y-erratas

Objetivo: Completar consultas, corregirlas y probar una nueva consulta dentro de la misma conversaci?n. · canal: API

**1.** «cuanto gaste en supermercados el mes pasado» → `PLANNED` · 12,9 s

> 358,71 € en supermercado el mes pasado, en 6 movimientos.
> Consultado en todas tus cuentas.

- Consulta: el mes pasado, gastos, supermercado · Cifra: 358,71 € en 6 mov. · SQL: 358,71 € en 6 mov. · Consulta comprobada: `{"categories": ["GROCERIES"], "direction": "EXPENSE", "end": null, "group_by": "NONE", "group_limit": null, "merchant_terms": [], "merchants": [], "metric": "SUM", "n": null, "period": "LAST_MONTH", "rank": null, "start": null}` · **match** (comprobada)
- Grupos: total 358,71 €
- Esperado: `intent` QUERY — En el corpus UX: {ran: {categories: [GROCERIES], window: LAST_MONTH}}

**2.** «en todas» → `PLANNED` · 23,3 s

> 358,71 € en supermercado el mes pasado, en 6 movimientos.
> Consultado en todas tus cuentas.

- Consulta: el mes pasado, gastos, supermercado · Cifra: 358,71 € en 6 mov. · SQL: 358,71 € en 6 mov. · Consulta comprobada: `{"categories": ["GROCERIES"], "direction": "EXPENSE", "end": null, "group_by": "NONE", "group_limit": null, "merchant_terms": [], "merchants": [], "metric": "SUM", "n": null, "period": "LAST_MONTH", "rank": null, "start": null}` · **match** (comprobada)
- Grupos: total 358,71 €
- Esperado: `pending_resolution` QUERY — En el corpus UX: {ran: {categories: [GROCERIES], window: LAST_MONTH}}

**3.** «por comercio» → `PLANNED` · 13,0 s

> 358,71 € en supermercado el mes pasado, en 6 movimientos.
>   · Dia: 166,23 € (3)
>   · Mercadona: 90,48 € (1)
>   · Carrefour: 64,47 € (1)
>   · Alcampo: 37,53 € (1)
> Consultado en todas tus cuentas.

- Consulta: el mes pasado, gastos, supermercado, por comercio · Cifra: 358,71 € en 6 mov. · **n/a** (no queda un plan verificable en la respuesta)
- Grupos: Dia 166,23 € · Mercadona 90,48 € · Carrefour 64,47 € · Alcampo 37,53 €
- Esperado: `follow_up` QUERY — En el corpus UX: {ran: {categories: [GROCERIES], group_by: COUNTERPARTY, window: LAST_MONTH}}

**4.** «y este mes» → `PLANNED` · 9,9 s

> No he encontrado movimientos en supermercado en lo que va de mes. Puedes buscar sin categoría o ampliar el periodo.
> Consultado en todas tus cuentas.

- Consulta: en lo que va de mes, gastos, supermercado, por comercio · Cifra: 0,00 € en 0 mov. · **n/a** (no queda un plan verificable en la respuesta)
- Esperado: `follow_up` QUERY — En el corpus UX: {ran: {categories: [GROCERIES], group_by: COUNTERPARTY, window: THIS_MONTH}}

**5.** «mejor por categoria» → `PLANNED` · 11,2 s

> No he encontrado movimientos en supermercado en lo que va de mes. Puedes buscar sin categoría o ampliar el periodo.
> Consultado en todas tus cuentas.

- Consulta: en lo que va de mes, gastos, supermercado, por categoría · Cifra: 0,00 € en 0 mov. · **n/a** (no queda un plan verificable en la respuesta)
- Esperado: `slot_carryover` QUERY — En el corpus UX: {ran: {group_by: CATEGORY, window: THIS_MONTH}}

**6.** «cuanto tengo?» → `BALANCE` · 22,0 s

> Tienes 7.019,56 € entre tus cuentas: 614,56 € en Cuenta corriente y 6.405,00 € en Hucha. Aparte, tienes 3.150,00 € en Fondo indexado, que es lo que invertiste y no lo que vale hoy.

- Consulta: saldo de tus cuentas · Cifra: 7.019,56 € · **n/a** (BALANCE: no es la cifra de una consulta)
- Esperado: `intent` BALANCE

**7.** «cuanto gaste en el super el mes pasao» → `PLANNED` · 13,4 s

> 358,71 € en supermercado el mes pasado, en 6 movimientos.
> Consultado en todas tus cuentas.

- Consulta: el mes pasado, gastos, supermercado · Cifra: 358,71 € en 6 mov. · SQL: 358,71 € en 6 mov. · Consulta comprobada: `{"categories": ["GROCERIES"], "direction": "EXPENSE", "end": null, "group_by": "NONE", "group_limit": null, "merchant_terms": [], "merchants": [], "metric": "SUM", "n": null, "period": "LAST_MONTH", "rank": null, "start": null}` · **match** (comprobada)
- Grupos: total 358,71 €
- Esperado: `intent` QUERY — En el corpus UX: {ran: {categories: [GROCERIES], window: LAST_MONTH}}

**8.** «y restauantes?» → `PLANNED` · 9,9 s

> 187,94 € en restaurantes el mes pasado, en 6 movimientos.
> Consultado en todas tus cuentas.

- Consulta: el mes pasado, gastos, restaurantes · Cifra: 187,94 € en 6 mov. · SQL: 187,94 € en 6 mov. · Consulta comprobada: `{"categories": ["RESTAURANTS"], "direction": "EXPENSE", "end": null, "group_by": "NONE", "group_limit": null, "merchant_terms": [], "merchants": [], "metric": "SUM", "n": null, "period": "LAST_MONTH", "rank": null, "start": null}` · **match** (comprobada)
- Grupos: total 187,94 €
- Esperado: `follow_up` QUERY — En el corpus UX: {ran: {categories: [RESTAURANTS], merchants: [], window: LAST_MONTH}}

**9.** «solo esta semna» → `PLANNED` · 24,4 s

> 46,47 € en restaurantes esta semana, en 2 movimientos.
> Consultado en todas tus cuentas.

- Consulta: esta semana, gastos, restaurantes · Cifra: 46,47 € en 2 mov. · SQL: 46,47 € en 2 mov. · Consulta comprobada: `{"categories": ["RESTAURANTS"], "direction": "EXPENSE", "end": null, "group_by": "NONE", "group_limit": null, "merchant_terms": [], "merchants": [], "metric": "SUM", "n": null, "period": "THIS_WEEK", "rank": null, "start": null}` · **match** (comprobada)
- Grupos: total 46,47 €
- Esperado: `slot_carryover` QUERY — En el corpus UX: {ran: {categories: [RESTAURANTS], merchants: [], window: THIS_WEEK}}

**10.** «y gasolina?» → `PLANNED` · 23,1 s

> No he encontrado movimientos en gasolina esta semana. Puedes buscar sin categoría o ampliar el periodo.
> Consultado en todas tus cuentas.

- Consulta: esta semana, gastos, gasolina · Cifra: 0,00 € en 0 mov. · SQL: 0,00 € en 0 mov. · Consulta comprobada: `{"categories": ["TRANSPORT_FUEL"], "direction": "EXPENSE", "end": null, "group_by": "NONE", "group_limit": null, "merchant_terms": [], "merchants": [], "metric": "SUM", "n": null, "period": "THIS_WEEK", "rank": null, "start": null}` · **match** (comprobada)
- Esperado: `follow_up` QUERY — En el corpus UX: {ran: {categories: [TRANSPORT_FUEL], merchants: [], window: THIS_WEEK}}

**11.** «cunato tengo» → `BALANCE` · 12,7 s

> Tienes 7.019,56 € entre tus cuentas: 614,56 € en Cuenta corriente y 6.405,00 € en Hucha. Aparte, tienes 3.150,00 € en Fondo indexado, que es lo que invertiste y no lo que vale hoy.

- Consulta: saldo de tus cuentas · Cifra: 7.019,56 € · **n/a** (BALANCE: no es la cifra de una consulta)
- Esperado: `intent` BALANCE

**12.** «gracias» → `COURTESY` · 9,9 s

> De nada. Si necesitas algo más de tus cuentas, dímelo.

- **n/a** (COURTESY: no es la cifra de una consulta)
- Esperado: `intent` COURTESY

**13.** «Compara mis gastos de restaurantes entre junio y julio de 2026» → `PLANNED` · 11,3 s

> En junio de 2026: 202,44 €; en julio de 2026: 133,57 €. El gasto en restaurantes fue 68,87 € mayor en junio de 2026.
> Consultado en todas tus cuentas.

- Consulta: del 2026-06-01 al 2026-06-30, gastos, restaurantes frente a del 2026-07-01 al 2026-07-31, gastos, restaurantes · Cifra: 11 mov. · **n/a** (una comparación: la juzga la rúbrica)
- Grupos: en junio de 2026 202,44 € · en julio de 2026 133,57 €
- Esperado: `intent` QUERY — Comparaci?n de restaurantes en junio y julio, con los dos resultados.

**14.** «Y en gasolina?» → `PLANNED` · 8,9 s

> En junio de 2026: 102,80 €; en julio de 2026: 159,46 €. El gasto en gasolina fue 56,66 € mayor en julio de 2026.
> Consultado en todas tus cuentas.

- Consulta: del 2026-06-01 al 2026-06-30, gastos, gasolina frente a del 2026-07-01 al 2026-07-31, gastos, gasolina · Cifra: 5 mov. · **n/a** (una comparación: la juzga la rúbrica)
- Grupos: en junio de 2026 102,80 € · en julio de 2026 159,46 €
- Esperado: `comparison` QUERY — Conserva ambos meses y cambia ?nicamente la categor?a a gasolina.

**15.** «Gracias» → `COURTESY` · 10,4 s

> De nada. Si necesitas algo más de tus cuentas, dímelo.

- **n/a** (COURTESY: no es la cifra de una consulta)
- Esperado: `intent` COURTESY — Cierra brevemente sin iniciar otra consulta.

| Turno | Esperado | Cifras | intent | context | pending | clarification | recovery | naturalness 1-5 | mechanism | notes | s |
|---|---|---|---|---|---|---|---|---|---|---|---:|
| 1 | intent | match |  |  |  |  |  |  | intent |  | 12,9 s |
| 2 | pending_resolution | match |  |  |  |  |  |  | pending_resolution |  | 23,3 s |
| 3 | follow_up | n/a |  |  |  |  |  |  | follow_up |  | 13,0 s |
| 4 | follow_up | n/a |  |  |  |  |  |  | follow_up |  | 9,9 s |
| 5 | slot_carryover | n/a |  | fail |  |  |  |  | slot_carryover | «mejor por categoría» no reagrupa: repite «no hay movimientos en supermercado este mes» en vez de desglosar por categoría. | 11,2 s |
| 6 | intent | n/a |  |  |  |  |  |  | intent |  | 22,0 s |
| 7 | intent | match |  |  |  |  |  |  | intent |  | 13,4 s |
| 8 | follow_up | match |  |  |  |  |  |  | follow_up |  | 9,9 s |
| 9 | slot_carryover | match |  |  |  |  |  |  | slot_carryover |  | 24,4 s |
| 10 | follow_up | match |  |  |  |  |  |  | follow_up |  | 23,1 s |
| 11 | intent | n/a |  |  |  |  |  |  | intent |  | 12,7 s |
| 12 | intent | n/a |  |  |  |  |  |  | intent |  | 9,9 s |
| 13 | intent | n/a |  |  |  |  |  |  | intent |  | 11,3 s |
| 14 | comparison | n/a |  |  |  |  |  |  | comparison |  | 8,9 s |
| 15 | intent | n/a |  |  |  |  |  |  | intent |  | 10,4 s |

## sesion-3-pagos-y-comparaciones

Objetivo: Distinguir consultas de pagos, cancelar con seguridad y mantener una comparaci?n. · canal: API

**1.** «haz un bixum a Marta Solis Vega» → `NEEDS_CLARIFICATION` · 12,8 s

> ¿De cuánto quieres hacerle el bizum a Marta Solis Vega?

- **n/a** (NEEDS_CLARIFICATION: no es la cifra de una consulta)
- Esperado: `intent` PAYMENT

**2.** «20 euros» → `AWAITING_CONFIRMATION` · 10,2 s

> Vas a enviar 20,00 € a Marta Solis Vega (+34600333444). Te quedarían 594,56 €. ¿Confirmas? Responde sí o no.

- **n/a** (AWAITING_CONFIRMATION: no es la cifra de una consulta)
- Estado: workflow PAYMENT_CONFIRMATION, awaiting CONFIRMATION
- Esperado: `pending_resolution` PAYMENT

**3.** «mejor 25» → `AWAITING_CONFIRMATION` · 11,4 s

> Vas a enviar 25,00 € a Marta Solis Vega (+34600333444). Te quedarían 589,56 €. ¿Confirmas? Responde sí o no.

- **n/a** (AWAITING_CONFIRMATION: no es la cifra de una consulta)
- Estado: workflow PAYMENT_CONFIRMATION, awaiting CONFIRMATION
- Esperado: `pending_resolution` PAYMENT

**4.** «no» → `CANCELLED` · 0,1 s

> Vale, no he enviado nada.

- **n/a** (CANCELLED: no es la cifra de una consulta)
- Esperado: `pending_resolution` PAYMENT — En el corpus UX: {ends: true}

**5.** «cuanto tengo» → `BALANCE` · 10,3 s

> Tienes 7.019,56 € entre tus cuentas: 614,56 € en Cuenta corriente y 6.405,00 € en Hucha. Aparte, tienes 3.150,00 € en Fondo indexado, que es lo que invertiste y no lo que vale hoy.

- Consulta: saldo de tus cuentas · Cifra: 7.019,56 € · **n/a** (BALANCE: no es la cifra de una consulta)
- Esperado: `intent` BALANCE

**6.** «gracias» → `COURTESY` · 24,6 s

> De nada. Si necesitas algo más de tus cuentas, dímelo.

- **n/a** (COURTESY: no es la cifra de una consulta)
- Esperado: `intent` COURTESY

**7.** «cuanto le mande a Marta este mes» → `PLANNED` · 10,0 s

> No he encontrado movimientos a Marta en lo que va de mes. Puedes buscar sin comercio o ampliar el periodo.
> Consultado en todas tus cuentas.

- Consulta: en lo que va de mes, gastos, Marta · Cifra: 0,00 € en 0 mov. · **n/a** (consulta con filtros privados omitidos del plan verificable)
- Esperado: `intent` QUERY — En el corpus UX: {ran: {direction: EXPENSE, window: THIS_MONTH}}

**8.** «y lo que me mando ella?» → `PLANNED` · 21,3 s

> No he encontrado movimientos de Marta en lo que va de mes. Puedes buscar sin comercio o ampliar el periodo.
> Consultado en todas tus cuentas.

- Consulta: en lo que va de mes, ingresos, Marta · Cifra: 0,00 € en 0 mov. · **n/a** (consulta con filtros privados omitidos del plan verificable)
- Esperado: `reference` QUERY — En el corpus UX: {ran: {direction: INCOME, window: THIS_MONTH}}

**9.** «ahora quiero mandarle 20 euros a Marta» → `NEEDS_CLARIFICATION` · 13,2 s

> Tienes varios contactos con ese nombre: Marta Ruiz Gomez, Marta Solis Vega. ¿A quién se lo envío?

- **n/a** (NEEDS_CLARIFICATION: no es la cifra de una consulta)
- Esperado: `interruption` PAYMENT

**10.** «no» → `CANCELLED` · 0,1 s

> Vale, he cancelado el borrador. No se ha enviado nada.

- **n/a** (CANCELLED: no es la cifra de una consulta)
- Esperado: `pending_resolution` PAYMENT — En el corpus UX: {ends: true}

**11.** «mis contactos» → `CONTACTS` · 0,9 s

> Tienes estos contactos para enviar dinero: Aitor Beltran Diaz, Carmen Nieto Lara, Marta Ruiz Gomez, Marta Solis Vega.

- **n/a** (CONTACTS: no es la cifra de una consulta)
- Esperado: `intent` CONTACTS

**12.** «cuanto tengo» → `BALANCE` · 2,4 s

> Tienes 7.019,56 € entre tus cuentas: 614,56 € en Cuenta corriente y 6.405,00 € en Hucha. Aparte, tienes 3.150,00 € en Fondo indexado, que es lo que invertiste y no lo que vale hoy.

- Consulta: saldo de tus cuentas · Cifra: 7.019,56 € · **n/a** (BALANCE: no es la cifra de una consulta)
- Esperado: `intent` BALANCE

**13.** «compara mis gastos en restaurantes de junio y julio de 2026» → `PLANNED` · 4,1 s

> En junio de 2026: 202,44 €; en julio de 2026: 133,57 €. El gasto en restaurantes fue 68,87 € mayor en junio de 2026.
> Consultado en todas tus cuentas.

- Consulta: del 2026-06-01 al 2026-06-30, gastos, restaurantes frente a del 2026-07-01 al 2026-07-31, gastos, restaurantes · Cifra: 11 mov. · **n/a** (una comparación: la juzga la rúbrica)
- Grupos: en junio de 2026 202,44 € · en julio de 2026 133,57 €
- Esperado: `intent` QUERY — En el corpus UX: {ran: [{categories: [RESTAURANTS], window: 2026-06-01..2026-06-30}, {categories: [
        RESTAURANTS], window: 2026-07-01..2026-07-31}]}

**14.** «y en gasolina?» → `PLANNED` · 1,3 s

> En junio de 2026: 102,80 €; en julio de 2026: 159,46 €. El gasto en gasolina fue 56,66 € mayor en julio de 2026.
> Consultado en todas tus cuentas.

- Consulta: del 2026-06-01 al 2026-06-30, gastos, gasolina frente a del 2026-07-01 al 2026-07-31, gastos, gasolina · Cifra: 5 mov. · **n/a** (una comparación: la juzga la rúbrica)
- Grupos: en junio de 2026 102,80 € · en julio de 2026 159,46 €
- Esperado: `comparison` QUERY — En el corpus UX: {ran: [{categories: [TRANSPORT_FUEL], window: 2026-06-01..2026-06-30}, {categories: [
        TRANSPORT_FUEL], window: 2026-07-01..2026-07-31}]}

**15.** «ahora solo julio de 2026» → `PLANNED` · 3,0 s

> En junio de 2026: 102,80 €; en julio de 2026: 159,46 €. El gasto en gasolina fue 56,66 € mayor en julio de 2026.
> Consultado en todas tus cuentas.

- Consulta: del 2026-06-01 al 2026-06-30, gastos, gasolina frente a del 2026-07-01 al 2026-07-31, gastos, gasolina · Cifra: 5 mov. · **n/a** (una comparación: la juzga la rúbrica)
- Grupos: en junio de 2026 102,80 € · en julio de 2026 159,46 €
- Esperado: `comparison` QUERY — En el corpus UX: {ran: {categories: [TRANSPORT_FUEL], window: 2026-07-01..2026-07-31}}

| Turno | Esperado | Cifras | intent | context | pending | clarification | recovery | naturalness 1-5 | mechanism | notes | s |
|---|---|---|---|---|---|---|---|---|---|---|---:|
| 1 | intent | n/a |  |  |  |  |  |  | intent |  | 12,8 s |
| 2 | pending_resolution | n/a |  |  |  |  |  |  | pending_resolution |  | 10,2 s |
| 3 | pending_resolution | n/a |  |  |  |  |  |  | pending_resolution |  | 11,4 s |
| 4 | pending_resolution | n/a |  |  |  |  |  |  | pending_resolution |  | 0,1 s |
| 5 | intent | n/a |  |  |  |  |  |  | intent |  | 10,3 s |
| 6 | intent | n/a |  |  |  |  |  |  | intent |  | 24,6 s |
| 7 | intent | n/a |  |  |  |  |  |  | intent |  | 10,0 s |
| 8 | reference | n/a |  |  |  |  |  |  | reference |  | 21,3 s |
| 9 | interruption | n/a |  |  |  |  |  |  | interruption |  | 13,2 s |
| 10 | pending_resolution | n/a |  |  |  |  |  |  | pending_resolution |  | 0,1 s |
| 11 | intent | n/a |  |  |  |  |  |  | intent |  | 0,9 s |
| 12 | intent | n/a |  |  |  |  |  |  | intent |  | 2,4 s |
| 13 | intent | n/a |  |  |  |  |  |  | intent |  | 4,1 s |
| 14 | comparison | n/a |  |  |  |  |  |  | comparison |  | 1,3 s |
| 15 | comparison | n/a |  | fail |  |  |  |  | comparison | «ahora solo julio de 2026» repite la comparación junio/julio en vez de dar la gasolina de julio. | 3,0 s |

## sesion-4-graficos-y-recuperacion

Objetivo: Reutilizar resultados en gr?ficos, recuperarse y seguir consultando sin filtros heredados. · canal: API

**1.** «gastos de los ultimos 3 meses por categoria, top 1» → `PLANNED` · 13,2 s

> La categoría con mayor importe total en los últimos 3 meses es:
>   · alquiler: 2.760,00 € (3)
> Consultado en todas tus cuentas.

- Consulta: en los últimos 3 meses, gastos, por categoría, top 1 · Cifra: 2.760,00 € en 3 mov. · **n/a** (no queda un plan verificable en la respuesta)
- Grupos: HOUSING_RENT 2.760,00 €
- Esperado: `intent` QUERY — En el corpus UX: {groups: [[GROCERIES, 20000, 5]], ran: {group_by: CATEGORY, group_limit: 1,
    window: 'LAST_N_MONTHS:3'}}

**2.** «pero no el top 1, muestrame todas las categorias» → `PLANNED` · 9,6 s

> 5.818,61 € en los últimos 3 meses, en 86 movimientos.
>   · alquiler: 2.760,00 € (3)
>   · supermercado: 955,44 € (17)
>   · restaurantes: 668,70 € (21)
>   · gasolina: 422,82 € (9)
>   · bizum: 302,29 € (7)
>   · suscripciones: 243,73 € (11)
>   · salud: 236,79 € (6)
>   · transporte público: 194,60 € (11)
>   · ocio: 34,24 € (1)
> Consultado en todas tus cuentas.

- Consulta: en los últimos 3 meses, gastos, por categoría · Cifra: 5.818,61 € en 86 mov. · **n/a** (no queda un plan verificable en la respuesta)
- Grupos: HOUSING_RENT 2.760,00 € · GROCERIES 955,44 € · RESTAURANTS 668,70 € · TRANSPORT_FUEL 422,82 € · TRANSFER_BIZUM 302,29 € · SUBSCRIPTIONS 243,73 € …
- Esperado: `follow_up` QUERY — En el corpus UX: {groups: [[GROCERIES, 20000, 5], [RESTAURANTS, 10000, 3]], ran: {group_by:
    CATEGORY, window: 'LAST_N_MONTHS:3'}}

**3.** «por meses mejor» → `PLANNED` · 10,8 s

> 5.818,61 € en los últimos 3 meses, en 86 movimientos.
>   · 2026-07: 530,96 € (14)
>   · 2026-08: 2.159,87 € (33)
>   · 2026-09: 2.097,36 € (32)
>   · 2026-10: 1.030,42 € (7)
> Consultado en todas tus cuentas.

- Consulta: en los últimos 3 meses, gastos, por mes · Cifra: 5.818,61 € en 86 mov. · **n/a** (no queda un plan verificable en la respuesta)
- Grupos: 2026-07 530,96 € · 2026-08 2.159,87 € · 2026-09 2.097,36 € · 2026-10 1.030,42 €
- Esperado: `follow_up` QUERY — En el corpus UX: {groups: [[2026-06, 10000, 2], [2026-07, 30000, 6], [2026-08, 5000, 1]], ran: {group_by: MONTH,
    window: 'LAST_N_MONTHS:3'}}

**4.** «ponlo en lineas» → `INVALID_DOMAIN` · 11,6 s

> Todavía no hay un gráfico activo que pueda cambiar. Primero crea uno.

- **n/a** (INVALID_DOMAIN: no es la cifra de una consulta)
- Esperado: `follow_up` VISUAL — En el corpus UX: {banking_calls: 0}

**5.** «por que julio es tan alto?» → `NEEDS_CLARIFICATION` · 9,8 s

> ¿Qué dos periodos quieres comparar?

- **n/a** (NEEDS_CLARIFICATION: no es la cifra de una consulta)
- Esperado: `reference` QUERY — En el corpus UX: {ran: {window: 2026-07-01..2026-07-31}}

**6.** «y solo restaurantes en julio de 2026?» → `NEEDS_CLARIFICATION` · 10,9 s

> ¿De qué periodo quieres verlo?

- **n/a** (NEEDS_CLARIFICATION: no es la cifra de una consulta)
- Esperado: `slot_carryover` QUERY — En el corpus UX: {ran: {categories: [RESTAURANTS], window: 2026-07-01..2026-07-31}}

**7.** «cuanto gaste en supermercado el mes pasado» → `PLANNED` · 12,2 s

> 358,71 € en supermercado el mes pasado, en 6 movimientos.
> Consultado en todas tus cuentas.

- Consulta: el mes pasado, gastos, supermercado · Cifra: 358,71 € en 6 mov. · SQL: 358,71 € en 6 mov. · Consulta comprobada: `{"categories": ["GROCERIES"], "direction": "EXPENSE", "end": null, "group_by": "NONE", "group_limit": null, "merchant_terms": [], "merchants": [], "metric": "SUM", "n": null, "period": "LAST_MONTH", "rank": null, "start": null}` · **match** (comprobada)
- Grupos: total 358,71 €
- Esperado: `intent` QUERY — En el corpus UX: {ran: {categories: [GROCERIES], window: LAST_MONTH}}

**8.** «no era eso» → `NEEDS_CLARIFICATION` · 9,9 s

> ¿Qué parte de la consulta anterior querías corregir?

- **n/a** (NEEDS_CLARIFICATION: no es la cifra de una consulta)
- Esperado: `reset` GUIDANCE

**9.** «me referia a gasolina este mes» → `PLANNED` · 11,5 s

> No he encontrado movimientos en gasolina en lo que va de mes. Puedes buscar sin categoría o ampliar el periodo.
> Consultado en todas tus cuentas.

- Consulta: en lo que va de mes, gastos, gasolina · Cifra: 0,00 € en 0 mov. · SQL: 0,00 € en 0 mov. · Consulta comprobada: `{"categories": ["TRANSPORT_FUEL"], "direction": "EXPENSE", "end": null, "group_by": "NONE", "group_limit": null, "merchant_terms": [], "merchants": [], "metric": "SUM", "n": null, "period": "THIS_MONTH", "rank": null, "start": null}` · **match** (comprobada)
- Esperado: `pending_resolution` QUERY — En el corpus UX: {ran: {categories: [TRANSPORT_FUEL], window: THIS_MONTH}}

**10.** «olvidalo» → `GUIDANCE` · 8,8 s

> De acuerdo, empezamos de nuevo. ¿Qué quieres consultar?

- **n/a** (GUIDANCE: no es la cifra de una consulta)
- Esperado: `reset` GUIDANCE — En el corpus UX: {banking_calls: 0}

**11.** «cuanto he ingresado este ano» → `PLANNED` · 10,5 s

> 22.690,71 € en lo que va de año, en 24 movimientos.
> Consultado en todas tus cuentas.

- Consulta: en lo que va de año, ingresos · Cifra: 22.690,71 € en 24 mov. · SQL: 22.690,71 € en 24 mov. · Consulta comprobada: `{"categories": [], "direction": "INCOME", "end": null, "group_by": "NONE", "group_limit": null, "merchant_terms": [], "merchants": [], "metric": "SUM", "n": null, "period": "THIS_YEAR", "rank": null, "start": null}` · **match** (comprobada)
- Grupos: total 22.690,71 €
- Esperado: `intent` QUERY — En el corpus UX: {ran: {categories: [], direction: INCOME, merchants: [], window: THIS_YEAR}}

**12.** «y el mes pasado?» → `PLANNED` · 11,4 s

> 2.483,13 € el mes pasado, en 2 movimientos.
> Consultado en todas tus cuentas.

- Consulta: el mes pasado, ingresos · Cifra: 2.483,13 € en 2 mov. · SQL: 2.483,13 € en 2 mov. · Consulta comprobada: `{"categories": [], "direction": "INCOME", "end": null, "group_by": "NONE", "group_limit": null, "merchant_terms": [], "merchants": [], "metric": "SUM", "n": null, "period": "LAST_MONTH", "rank": null, "start": null}` · **match** (comprobada)
- Grupos: total 2.483,13 €
- Esperado: `follow_up` QUERY — En el corpus UX: {ran: {categories: [], direction: INCOME, merchants: [], window: LAST_MONTH}}

**13.** «Cu?nto he gastado en restaurantes este a?o?» → `PLANNED` · 9,2 s

> 2.086,68 € en restaurantes en lo que va de año, en 61 movimientos.
> Consultado en todas tus cuentas.

- Consulta: en lo que va de año, gastos, restaurantes · Cifra: 2.086,68 € en 61 mov. · SQL: 2.086,68 € en 61 mov. · Consulta comprobada: `{"categories": ["RESTAURANTS"], "direction": "EXPENSE", "end": null, "group_by": "NONE", "group_limit": null, "merchant_terms": [], "merchants": [], "metric": "SUM", "n": null, "period": "THIS_YEAR", "rank": null, "start": null}` · **match** (comprobada)
- Grupos: total 2.086,68 €
- Esperado: `intent` QUERY — Consulta gastos de restaurantes en el a?o actual.

**14.** «Y solo en octubre?» → `PLANNED` · 10,5 s

> 55,54 € en restaurantes en lo que va de mes, en 3 movimientos.
> Consultado en todas tus cuentas.

- Consulta: en lo que va de mes, gastos, restaurantes · Cifra: 55,54 € en 3 mov. · SQL: 55,54 € en 3 mov. · Consulta comprobada: `{"categories": ["RESTAURANTS"], "direction": "EXPENSE", "end": null, "group_by": "NONE", "group_limit": null, "merchant_terms": [], "merchants": [], "metric": "SUM", "n": null, "period": "THIS_MONTH", "rank": null, "start": null}` · **match** (comprobada)
- Grupos: total 55,54 €
- Esperado: `slot_carryover` QUERY — Mantiene restaurantes y cambia el periodo a octubre.

**15.** «Gracias» → `COURTESY` · 11,8 s

> De nada. Si necesitas algo más de tus cuentas, dímelo.

- **n/a** (COURTESY: no es la cifra de una consulta)
- Esperado: `intent` COURTESY — Cierra brevemente sin iniciar otra consulta.

| Turno | Esperado | Cifras | intent | context | pending | clarification | recovery | naturalness 1-5 | mechanism | notes | s |
|---|---|---|---|---|---|---|---|---|---|---|---:|
| 1 | intent | n/a |  |  |  |  |  |  | intent |  | 13,2 s |
| 2 | follow_up | n/a |  |  |  |  |  |  | follow_up |  | 9,6 s |
| 3 | follow_up | n/a |  |  |  |  |  |  | follow_up |  | 10,8 s |
| 4 | follow_up | n/a | fail |  |  |  |  |  | follow_up | «ponlo en líneas» con una serie por meses en pantalla: responde que no hay gráfico activo en vez de dibujarla en líneas sin leer el banco. | 11,6 s |
| 5 | reference | n/a | fail |  |  |  |  |  | reference | «¿por qué julio es tan alto?» pregunta qué dos periodos comparar en vez de examinar julio. | 9,8 s |
| 6 | slot_carryover | n/a |  |  |  | fail |  |  | slot_carryover | «y solo restaurantes en julio de 2026?» pregunta el periodo aunque la frase lo dice: el modelo emitió un ask sin ventana. | 10,9 s |
| 7 | intent | match |  |  |  |  |  |  | intent |  | 12,2 s |
| 8 | reset | n/a |  |  |  |  |  |  | reset |  | 9,9 s |
| 9 | pending_resolution | match |  |  |  |  |  |  | pending_resolution |  | 11,5 s |
| 10 | reset | n/a |  |  |  |  |  |  | reset |  | 8,8 s |
| 11 | intent | match |  |  |  |  |  |  | intent |  | 10,5 s |
| 12 | follow_up | match |  |  |  |  |  |  | follow_up |  | 11,4 s |
| 13 | intent | match |  |  |  |  |  |  | intent |  | 9,2 s |
| 14 | slot_carryover | match |  |  |  |  |  |  | slot_carryover |  | 10,5 s |
| 15 | intent | n/a |  |  |  |  |  |  | intent |  | 11,8 s |
