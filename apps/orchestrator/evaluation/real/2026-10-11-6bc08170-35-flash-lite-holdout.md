# Conversaciones reales — 2026-10-12, `6bc08170`

**Preflight** (2026-10-12T00:41:19+02:00)

- Servicios: orchestrator ok · banking ok · gateway skipped (not used by this evaluation)
- Orquestador: `6bc08170` (task/legacy-removal) · HEAD `6bc08170` · source `d95518552fb88cf497198cb4b824fefeeb29369ee81908ee6ddcc2134edaba40` · código del producto: igual que el código local
- Banking API: `6bc08170` (task/legacy-removal) · source `ca81ab863f670021662c0e93710024262c90c95228a4e61ad03e25f99c62212a`
- Libro: último movimiento 2026-10-12 (hoy 2026-10-12) · antigüedad aceptada a mano: 4 días
- GPU: P8, 22 MHz · Ollama en memoria: qwen2.5-coder:7b

## Resumen

Sin ejecución anterior con la que comparar.

| Mecanismo | Turnos | Fallidos | Sin juzgar | Acierto | Antes |
|---|---:|---:|---:|---:|---:|
| comparison | 1 | 0 | 1 | — | — |
| follow_up | 8 | 1 | 7 | 0 % | — |
| intent | 11 | 0 | 11 | — | — |
| interruption | 4 | 0 | 4 | — | — |
| pending_resolution | 2 | 0 | 2 | — | — |
| reference | 2 | 0 | 2 | — | — |
| reset | 2 | 0 | 2 | — | — |
| slot_carryover | 7 | 0 | 7 | — | — |

### Gestiones de las sesiones

| Gestión | Turnos | Estado | Fallidos | Sin juzgar | Invariantes |
|---|---:|---|---:|---:|---|
| holdout-bizum-ambiguo-suspendido-y-cancelado / bizum-marta | 4 | pendiente | 0 | 4 | — |
| holdout-bizum-ambiguo-suspendido-y-cancelado / enviado-marta | 1 | pendiente | 0 | 1 | — |
| holdout-bizum-ambiguo-suspendido-y-cancelado / saldo-corriente | 1 | pendiente | 0 | 1 | — |
| holdout-capacidades-y-traspaso-desistido / capacidades | 2 | pendiente | 0 | 2 | — |
| holdout-capacidades-y-traspaso-desistido / suscripciones | 1 | pendiente | 0 | 1 | — |
| holdout-capacidades-y-traspaso-desistido / traspaso | 3 | pendiente | 0 | 3 | — |
| holdout-compras-tarjeta-paginadas / compras-tarjeta | 3 | fallida | 1 | 2 | — |
| holdout-compras-tarjeta-paginadas / total-super | 3 | pendiente | 0 | 3 | — |
| holdout-correcciones-e-interrupcion / consulta-corregida | 5 | pendiente | 0 | 5 | — |
| holdout-correcciones-e-interrupcion / tarjetas | 1 | pendiente | 0 | 1 | — |
| holdout-gasolina-aclaracion-con-dato-repetido / gasolina | 5 | pendiente | 0 | 5 | — |
| holdout-gasolina-aclaracion-con-dato-repetido / repsol | 1 | pendiente | 0 | 1 | — |
| holdout-saldos-movimientos-nomina / cierre | 1 | pendiente | 0 | 1 | — |
| holdout-saldos-movimientos-nomina / movimientos-hucha | 2 | pendiente | 0 | 2 | — |
| holdout-saldos-movimientos-nomina / nomina | 2 | pendiente | 0 | 2 | — |
| holdout-saldos-movimientos-nomina / saldos | 2 | pendiente | 0 | 2 | — |

- Recuperación (tras un turno fallido, ¿el siguiente va bien?): 0 de 0 (—)
- Fallos de disponibilidad (también fallan la sesión): provider 0 · banking 0 · upstream_http 0 · transport 0
- Provider failure attempts (safe category): ninguno registrado
- Terminal provider-call failures: none recorded
- Turnos intentados / omitidos / previstos: 37 / 0 / 37
- Cifras contra el libro: 9 match · 0 mismatch · 0 sin poder comprobar (error) · 28 n/a
- Latencia por turno: mediana 10,0 s · p95 26,4 s · máx 26,4 s (37 turnos)
  - sin la espera del proveedor: mediana 2,2 s · p95 4,7 s · máx 5,1 s (36 turnos)
  - solo peticiones al modelo: mediana 1,1 s · p95 2,3 s · máx 2,4 s (36 turnos)
  - motor `model`: mediana 10,0 s · p95 26,4 s · máx 26,4 s (37 turnos)
- Conversaciones logradas (todos los turnos bien y objetivo cumplido): 0 de 1 juzgadas (0 %) · 5 sin juzgar
- Llamadas al modelo por turno: media 1,11 · p95 2 (37 turnos con dato)
- Peticiones HTTP reales al proveedor (incluye reintentos/fallback): 41 en 37 turnos · media 1,11 · p95 2
- Modelos intentados/usados: `gemini-3.5-flash-lite`: 41 intentos, 41 respuestas
- Tokens informados por proveedor: 169134 total en 36 turnos (166601 entrada, 2533 salida; 41 respuestas con usage)

## holdout-saldos-movimientos-nomina

Objetivo: Ver el dinero que tiene, repasar los últimos movimientos de la Hucha y luego comprobar la nómina sin que se cuele el filtro de la Hucha. · canal: API

**1.** «hola, ¿cuánto dinero tengo en total?» → `BALANCE` · 8,1 s

> Tienes 6.995,56 € entre tus cuentas: 571,56 € en Cuenta corriente y 6.424,00 € en Hucha. Aparte, tienes 3.150,00 € en Fondo indexado, que es lo que invertiste y no lo que vale hoy.

- Consulta: saldo de tus cuentas · Cifra: 6.995,56 € · **n/a** (BALANCE: no es la cifra de una consulta)
- Esperado: `intent` BALANCE — Saldo por cuenta y total, solo con cifras de la banca.

**2.** «¿y solo en la hucha?» → `NEEDS_CLARIFICATION` · 9,3 s

> ¿Qué quieres consultar? Dime qué gastos o ingresos y de qué periodo.

- **n/a** (NEEDS_CLARIFICATION: no es la cifra de una consulta)
- Esperado: `follow_up` BALANCE — El saldo de la Hucha; no vuelve a dar el resto de cuentas como respuesta principal.

**3.** «a ver los 5 movimientos más nuevos de la hucha» → `PLANNED` · 10,7 s

> Estos son tus últimos 5 movimientos. Dime "dame otros 5" para ver los siguientes.
> Consultado en Hucha.

- Consulta: movimientos recientes · **n/a** (no queda un plan verificable en la respuesta)
- Esperado: `intent` QUERY — Una lista de hasta cinco movimientos de la Hucha, los más recientes primero, con fecha, concepto e importe; no pide periodo.

**4.** «¿cuál de esos fue el más grande?» → `NEEDS_CLARIFICATION` · 8,5 s

> ¿Qué dos periodos quieres comparar?

- **n/a** (NEEDS_CLARIFICATION: no es la cifra de una consulta)
- Esperado: `reference` QUERY — Señala el movimiento de mayor importe entre los cinco listados; no amplía a otras cuentas ni a otro periodo.

**5.** «cambiando de tema, ¿cuándo me pagaron la nómina este mes?» → `PLANNED` · 10,0 s

> No he encontrado movimientos en nómina en lo que va de mes. Puedes buscar sin categoría o ampliar el periodo.
> Consultado en todas tus cuentas.

- Consulta: en lo que va de mes, ingresos, nómina · Cifra: 0,00 € en 0 mov. · SQL: 0,00 € en 0 mov. · Consulta comprobada: `{"categories": ["SALARY"], "direction": "INCOME", "end": null, "group_by": "NONE", "group_limit": null, "merchant_terms": [], "merchants": [], "metric": "SUM", "n": null, "period": "THIS_MONTH", "rank": null, "start": null}` · **match** (comprobada)
- Esperado: `interruption` QUERY — Fecha e importe del ingreso de nómina de este mes; no se limita a la Hucha ni a cinco filas.

**6.** «¿y el mes anterior cuánto fue?» → `PLANNED` · 11,0 s

> 2.473,13 € en nómina el mes pasado, en 1 movimiento.
> Consultado en todas tus cuentas.

- Consulta: el mes pasado, ingresos, nómina · Cifra: 2.473,13 € en 1 mov. · SQL: 2.473,13 € en 1 mov. · Consulta comprobada: `{"categories": ["SALARY"], "direction": "INCOME", "end": null, "group_by": "NONE", "group_limit": null, "merchant_terms": [], "merchants": [], "metric": "SUM", "n": null, "period": "LAST_MONTH", "rank": null, "start": null}` · **match** (comprobada)
- Grupos: total 2.473,13 €
- Esperado: `follow_up` QUERY — El importe de la nómina del mes anterior; conserva que se habla de la nómina.

**7.** «gracias, eso es todo» → `COURTESY` · 8,7 s

> De nada. Si necesitas algo más de tus cuentas, dímelo.

- **n/a** (COURTESY: no es la cifra de una consulta)
- Esperado: `intent` COURTESY — Una despedida breve; no repite cifras ni lanza consultas.

| Turno | Esperado | Cifras | intent | context | pending | clarification | recovery | naturalness 1-5 | mechanism | notes | s |
|---|---|---|---|---|---|---|---|---|---|---|---:|
| 1 | intent | n/a |  |  |  |  |  |  |  |  | 8,1 s |
| 2 | follow_up | n/a |  |  |  |  |  |  |  |  | 9,3 s |
| 3 | intent | n/a |  |  |  |  |  |  |  |  | 10,7 s |
| 4 | reference | n/a |  |  |  |  |  |  |  |  | 8,5 s |
| 5 | interruption | match |  |  |  |  |  |  |  |  | 10,0 s |
| 6 | follow_up | match |  |  |  |  |  |  |  |  | 11,0 s |
| 7 | intent | n/a |  |  |  |  |  |  |  |  | 8,7 s |

## holdout-gasolina-aclaracion-con-dato-repetido

Objetivo: Consultar la gasolina, responder la aclaración añadiendo un dato de más, comparar con el año anterior, pedir un gráfico y cambiar a una pregunta nueva sobre un comercio. · canal: API

**1.** «quiero saber lo que me he gastado en gasolina» → `NEEDS_CLARIFICATION` · 26,4 s

> ¿De qué periodo quieres ver tus gastos en gasolina?

- **n/a** (NEEDS_CLARIFICATION: no es la cifra de una consulta)
- Esperado: `intent` QUERY — Si pregunta algo, pregunta solo por el periodo; no pregunta por la categoría. Si asume un periodo, lo dice.

**2.** «el mes pasado, solo de la corriente» → `PLANNED` · 9,9 s

> 167,31 € en gasolina el mes pasado, en 3 movimientos.
> Consultado en Cuenta corriente.

- Consulta: el mes pasado, gastos, gasolina · Cifra: 167,31 € en 3 mov. · **n/a** (consulta de cuenta específica: el informe no conserva ids de cuenta)
- Grupos: total 167,31 €
- Esperado: `pending_resolution` QUERY — Gasto en gasolina del mes pasado en Cuenta corriente. Usa los dos datos sin volver a preguntar nada.

**3.** «¿en qué gasolinera gasté más?» → `PLANNED` · 11,2 s

> El comercio con mayor importe total en gasolina el mes pasado es:
>   · Shell: 74,17 € (1)
> Consultado en Cuenta corriente.

- Consulta: el mes pasado, gastos, gasolina, por comercio, top 1 · Cifra: 74,17 € en 1 mov. · **n/a** (consulta de cuenta específica: el informe no conserva ids de cuenta)
- Grupos: Shell 74,17 €
- Esperado: `follow_up` QUERY — Ranking por comercio dentro de gasolina, mes pasado y Cuenta corriente, con la primera gasolinera destacada.

**4.** «¿y en ese mismo mes del año pasado?» → `PLANNED` · 9,4 s

> El comercio con mayor importe total en gasolina del 2025-09-01 al 2025-09-30 es:
>   · Repsol: 168,79 € (3)
> Consultado en Cuenta corriente.

- Consulta: del 2025-09-01 al 2025-09-30, gastos, gasolina, por comercio, top 1 · Cifra: 168,79 € en 3 mov. · **n/a** (consulta de cuenta específica: el informe no conserva ids de cuenta)
- Grupos: Repsol 168,79 €
- Esperado: `follow_up` QUERY — Mismo ranking de gasolina en Cuenta corriente para ese mes un año antes; no pierde ninguno de los filtros.

**5.** «¿me lo pintas con barras?» → `INVALID_DOMAIN` · 10,3 s

> Todavía no hay un gráfico activo que pueda cambiar. Primero crea uno.

- **n/a** (INVALID_DOMAIN: no es la cifra de una consulta)
- Esperado: `reference` VISUAL — Gráfico de barras del último ranking con sus cifras verificadas; no consulta otro periodo.

**6.** «olvida el gráfico, ¿cuántas veces he ido a Repsol este año?» → `PLANNED` · 11,7 s

> 10 movimientos en Repsol en lo que va de año.
> Consultado en todas tus cuentas.

- Consulta: en lo que va de año, gastos, Repsol, número de movimientos · Cifra: 552,21 € en 10 mov. · SQL: 552,21 € en 10 mov. · Consulta comprobada: `{"categories": [], "direction": "EXPENSE", "end": null, "group_by": "NONE", "group_limit": null, "merchant_terms": [], "merchants": ["Repsol"], "metric": "COUNT", "n": null, "period": "THIS_YEAR", "rank": null, "start": null}` · **match** (comprobada)
- Grupos: total 552,21 €
- Esperado: `interruption` QUERY — Número de movimientos en Repsol en lo que va de año; no hereda el periodo del año pasado y deja claro qué cuentas incluye.

| Turno | Esperado | Cifras | intent | context | pending | clarification | recovery | naturalness 1-5 | mechanism | notes | s |
|---|---|---|---|---|---|---|---|---|---|---|---:|
| 1 | intent | n/a |  |  |  |  |  |  |  |  | 26,4 s |
| 2 | pending_resolution | n/a |  |  |  |  |  |  |  |  | 9,9 s |
| 3 | follow_up | n/a |  |  |  |  |  |  |  |  | 11,2 s |
| 4 | follow_up | n/a |  |  |  |  |  |  |  |  | 9,4 s |
| 5 | reference | n/a |  |  |  |  |  |  |  |  | 10,3 s |
| 6 | interruption | match |  |  |  |  |  |  |  |  | 11,7 s |

## holdout-bizum-ambiguo-suspendido-y-cancelado

Objetivo: Empezar un Bizum a una Marta sin decir cuál, interrumpirlo con una consulta de saldo, retomarlo, cambiar el importe y cancelarlo; después consultar lo enviado sin crear un pago. · canal: API

**1.** «mándale 30 euros a Marta por bizum» → `NEEDS_CLARIFICATION` · 25,4 s

> Tienes varios contactos con ese nombre: Marta Ruiz Gomez, Marta Solis Vega. ¿A quién se lo envío?

- **n/a** (NEEDS_CLARIFICATION: no es la cifra de una consulta)
- Esperado: `intent` PAYMENT — Pregunta a qué Marta (Ruiz Gomez o Solis Vega); no muestra todavía una previsualización ni envía dinero.

**2.** «espera, antes dime cuánto me queda en la corriente» → `BALANCE` · 8,7 s

> Tienes 571,56 € en Cuenta corriente.

- Consulta: saldo de Cuenta corriente · Cifra: 571,56 € · **n/a** (BALANCE: no es la cifra de una consulta)
- Esperado: `interruption` BALANCE — El saldo de Cuenta corriente. El Bizum queda en suspenso, no cancelado ni enviado.

**3.** «vale, la de Solis» → `NEEDS_CLARIFICATION` · 0,1 s

> El Bizum de 30,00 € está pausado. Para retomarlo, escribe «continuar con el Bizum»; para descartarlo, responde «no».

- **n/a** (NEEDS_CLARIFICATION: no es la cifra de una consulta)
- Esperado: `pending_resolution` PAYMENT — Retoma el Bizum con los 30 € y muestra la previsualización a Marta Solis Vega; no vuelve a pedir el importe y no envía nada.

**4.** «que sean 25 en vez de 30» → `NEEDS_CLARIFICATION` · 1,1 s

> Tienes varios contactos con ese nombre: Marta Ruiz Gomez, Marta Solis Vega. ¿A quién se lo envío?

- **n/a** (NEEDS_CLARIFICATION: no es la cifra de una consulta)
- Esperado: `slot_carryover` PAYMENT — Nueva previsualización de 25 € a la misma Marta; no envía nada.

**5.** «no, al final no lo mandes» → `NEEDS_CLARIFICATION` · 2,7 s

> ¿A quién le quieres enviar 25,00 €?

- **n/a** (NEEDS_CLARIFICATION: no es la cifra de una consulta)
- Esperado: `reset` — Cancela el Bizum y lo dice; nunca llama a la confirmación bancaria.

**6.** «¿cuánto le he enviado a Marta este año?» → `PLANNED` · 1,2 s

> 75,12 € a Marta en lo que va de año, en 1 movimiento.
> Consultado en todas tus cuentas.

- Consulta: en lo que va de año, gastos, Marta · Cifra: 75,12 € en 1 mov. · **n/a** (consulta con filtros privados omitidos del plan verificable)
- Grupos: total 75,12 €
- Esperado: `intent` QUERY — Total enviado por Bizum a Marta en lo que va de año, a partir de los movimientos; no abre un borrador de pago.

| Turno | Esperado | Cifras | intent | context | pending | clarification | recovery | naturalness 1-5 | mechanism | notes | s |
|---|---|---|---|---|---|---|---|---|---|---|---:|
| 1 | intent | n/a |  |  |  |  |  |  |  |  | 25,4 s |
| 2 | interruption | n/a |  |  |  |  |  |  |  |  | 8,7 s |
| 3 | pending_resolution | n/a |  |  |  |  |  |  |  |  | 0,1 s |
| 4 | slot_carryover | n/a |  |  |  |  |  |  |  |  | 1,1 s |
| 5 | reset | n/a |  |  |  |  |  |  |  |  | 2,7 s |
| 6 | intent | n/a |  |  |  |  |  |  |  |  | 1,2 s |

## holdout-compras-tarjeta-paginadas

Objetivo: Revisar las compras recientes con tarjeta, filtrarlas, pasar página, pasar a un total y compararlo con el año anterior. · canal: API

**1.** «qué compras he hecho con la tarjeta últimamente» → `PLANNED` · 13,3 s

> Estos son tus últimos 20 movimientos. Dime "dame otros 20" para ver los siguientes.
> Consultado en todas tus cuentas.

- Consulta: gastos recientes · **n/a** (no queda un plan verificable en la respuesta)
- Esperado: `intent` QUERY — Lista de las compras con tarjeta más recientes, ordenadas por fecha; no pide periodo ni da solo un total.

**2.** «solo las del super» → `PLANNED` · 13,4 s

> Estos son tus últimos 20 movimientos. Dime "dame otros 20" para ver los siguientes.
> Consultado en todas tus cuentas.

- Consulta: gastos recientes, supermercado · **n/a** (no queda un plan verificable en la respuesta)
- Esperado: `slot_carryover` QUERY — La misma lista limitada a supermercados; sigue siendo una lista, no un total.

**3.** «más» → `PROVIDER_ERROR` · 14,6 s

> No he podido leer tu mensaje ahora mismo. Vuelve a intentarlo.

- **n/a** (PROVIDER_ERROR: no es la cifra de una consulta)
- Esperado: `follow_up` QUERY — La página siguiente de compras de supermercado, sin repetir filas de la anterior.

**4.** «¿cuánto suman las del super de este mes?» → `NEEDS_CLARIFICATION` · 11,6 s

> ¿Qué quieres consultar? Dime qué gastos o ingresos y de qué periodo.

- **n/a** (NEEDS_CLARIFICATION: no es la cifra de una consulta)
- Esperado: `follow_up` QUERY — Total gastado en supermercados en el mes en curso; pasa de lista a total.

**5.** «compáralo con el mismo mes del año pasado» → `PLANNED` · 26,1 s

> En octubre de 2025: 558,43 €; en lo que va de mes: 0,00 €. El gasto en supermercado fue 558,43 € mayor en octubre de 2025.
> Consultado en todas tus cuentas.

- Consulta: del 2025-10-01 al 2025-10-31, gastos, supermercado frente a en lo que va de mes, gastos, supermercado · Cifra: 8 mov. · **n/a** (una comparación: la juzga la rúbrica)
- Grupos: en octubre de 2025 558,43 € · en lo que va de mes 0,00 €
- Esperado: `comparison` QUERY — Compara supermercados entre este mes y el mismo mes del año anterior, con los dos periodos explícitos; avisa si el mes actual aún no ha terminado.

**6.** «¿y sin contar Mercadona?» → `PLANNED` · 9,6 s

> En octubre de 2025: 418,14 €; en lo que va de mes: 0,00 €. El gasto en supermercado, sin Mercadona, fue 418,14 € mayor en octubre de 2025.
> Consultado en todas tus cuentas.

- Consulta: del 2025-10-01 al 2025-10-31, gastos, supermercado, sin Mercadona frente a en lo que va de mes, gastos, supermercado, sin Mercadona · Cifra: 6 mov. · **n/a** (una comparación: la juzga la rúbrica)
- Grupos: en octubre de 2025 418,14 € · en lo que va de mes 0,00 €
- Esperado: `slot_carryover` QUERY — Si puede excluir un comercio, repite la comparación sin Mercadona; si no puede, lo dice y ofrece una alternativa. Nunca inventa una cifra.

| Turno | Esperado | Cifras | intent | context | pending | clarification | recovery | naturalness 1-5 | mechanism | notes | s |
|---|---|---|---|---|---|---|---|---|---|---|---:|
| 1 | intent | n/a |  |  |  |  |  |  |  |  | 13,3 s |
| 2 | slot_carryover | n/a |  |  |  |  |  |  |  |  | 13,4 s |
| 3 | follow_up | n/a |  |  |  |  |  |  |  |  | 14,6 s |
| 4 | follow_up | n/a |  |  |  |  |  |  |  |  | 11,6 s |
| 5 | comparison | n/a |  |  |  |  |  |  |  |  | 26,1 s |
| 6 | slot_carryover | n/a |  |  |  |  |  |  |  |  | 9,6 s |

## holdout-correcciones-e-interrupcion

Objetivo: Corregir dos veces una consulta, acotarla a un comercio, interrumpirla con otra gestión y volver a ella. · canal: API

**1.** «cuanto me gaste en ocio en agosto» → `PLANNED` · 13,8 s

> No he encontrado movimientos en ocio del 2026-08-01 al 2026-08-31. Puedes buscar sin categoría o ampliar el periodo.
> Consultado en todas tus cuentas.

- Consulta: del 2026-08-01 al 2026-08-31, gastos, ocio · Cifra: 0,00 € en 0 mov. · SQL: 0,00 € en 0 mov. · Consulta comprobada: `{"categories": ["LEISURE"], "direction": "EXPENSE", "end": "2026-08-31", "group_by": "NONE", "group_limit": null, "merchant_terms": [], "merchants": [], "metric": "SUM", "n": null, "period": null, "rank": null, "start": "2026-08-01"}` · **match** (comprobada)
- Esperado: `intent` QUERY — Gasto en ocio en agosto, con el periodo explícito.

**2.** «no, en julio» → `PLANNED` · 26,4 s

> 44,59 € en ocio del 2026-07-01 al 2026-07-31, en 2 movimientos.
> Consultado en todas tus cuentas.

- Consulta: del 2026-07-01 al 2026-07-31, gastos, ocio · Cifra: 44,59 € en 2 mov. · SQL: 44,59 € en 2 mov. · Consulta comprobada: `{"categories": ["LEISURE"], "direction": "EXPENSE", "end": "2026-07-31", "group_by": "NONE", "group_limit": null, "merchant_terms": [], "merchants": [], "metric": "SUM", "n": null, "period": null, "rank": null, "start": "2026-07-01"}` · **match** (comprobada)
- Grupos: total 44,59 €
- Esperado: `slot_carryover` QUERY — Gasto en ocio en julio; conserva la categoría.

**3.** «no me referia a eso, lo de restaurantes» → `PLANNED` · 9,3 s

> 133,57 € en restaurantes del 2026-07-01 al 2026-07-31, en 6 movimientos.
> Consultado en todas tus cuentas.

- Consulta: del 2026-07-01 al 2026-07-31, gastos, restaurantes · Cifra: 133,57 € en 6 mov. · SQL: 133,57 € en 6 mov. · Consulta comprobada: `{"categories": ["RESTAURANTS"], "direction": "EXPENSE", "end": "2026-07-31", "group_by": "NONE", "group_limit": null, "merchant_terms": [], "merchants": [], "metric": "SUM", "n": null, "period": null, "rank": null, "start": "2026-07-01"}` · **match** (comprobada)
- Grupos: total 133,57 €
- Esperado: `slot_carryover` QUERY — Gasto en restaurantes en julio; cambia la categoría y conserva el mes.

**4.** «¿y en el Goiko solo?» → `PLANNED` · 7,1 s

> 18,55 € en Goiko del 2026-07-01 al 2026-07-31, en 1 movimiento.
> Consultado en todas tus cuentas.

- Consulta: del 2026-07-01 al 2026-07-31, gastos, restaurantes, Goiko, comercios con «Goiko» en el nombre · Cifra: 18,55 € en 1 mov. · SQL: 18,55 € en 1 mov. · Consulta comprobada: `{"categories": ["RESTAURANTS"], "direction": "EXPENSE", "end": "2026-07-31", "group_by": "NONE", "group_limit": null, "merchant_terms": ["Goiko"], "merchants": ["Goiko"], "metric": "SUM", "n": null, "period": null, "rank": null, "start": "2026-07-01"}` · **match** (comprobada)
- Grupos: total 18,55 €
- Esperado: `slot_carryover` QUERY — Gasto en Goiko en julio.

**5.** «por cierto, ¿qué tarjetas tengo?» → `BALANCE` · 8,3 s

> Tienes 6.995,56 € entre tus cuentas: 571,56 € en Cuenta corriente y 6.424,00 € en Hucha. Aparte, tienes 3.150,00 € en Fondo indexado, que es lo que invertiste y no lo que vale hoy.

- Consulta: saldo de tus cuentas · Cifra: 6.995,56 € · **n/a** (BALANCE: no es la cifra de una consulta)
- Esperado: `interruption` — Las tarjetas del titular con su estado; no mezcla la consulta de Goiko.

**6.** «volviendo a lo del Goiko, ¿y en todo el año?» → `PLANNED` · 9,5 s

> 497,04 € en Goiko en lo que va de año, en 16 movimientos.
> Consultado en todas tus cuentas.

- Consulta: en lo que va de año, gastos, restaurantes, Goiko, comercios con «Goiko» en el nombre · Cifra: 497,04 € en 16 mov. · SQL: 497,04 € en 16 mov. · Consulta comprobada: `{"categories": ["RESTAURANTS"], "direction": "EXPENSE", "end": null, "group_by": "NONE", "group_limit": null, "merchant_terms": ["Goiko"], "merchants": ["Goiko"], "metric": "SUM", "n": null, "period": "THIS_YEAR", "rank": null, "start": null}` · **match** (comprobada)
- Grupos: total 497,04 €
- Esperado: `follow_up` QUERY — Gasto en Goiko en lo que va de año; recupera el comercio tras la interrupción.

| Turno | Esperado | Cifras | intent | context | pending | clarification | recovery | naturalness 1-5 | mechanism | notes | s |
|---|---|---|---|---|---|---|---|---|---|---|---:|
| 1 | intent | match |  |  |  |  |  |  |  |  | 13,8 s |
| 2 | slot_carryover | match |  |  |  |  |  |  |  |  | 26,4 s |
| 3 | slot_carryover | match |  |  |  |  |  |  |  |  | 9,3 s |
| 4 | slot_carryover | match |  |  |  |  |  |  |  |  | 7,1 s |
| 5 | interruption | n/a |  |  |  |  |  |  |  |  | 8,3 s |
| 6 | follow_up | match |  |  |  |  |  |  |  |  | 9,5 s |

## holdout-capacidades-y-traspaso-desistido

Objetivo: Preguntar qué puede hacer el asistente, preparar un traspaso a la Hucha, cambiar el importe y desistir, y acabar con una consulta normal. · canal: API

**1.** «¿qué cosas puedes hacer por mí?» → `IDENTITY` · 9,7 s

> Puedo decirte cuánto gastaste o ingresaste en un periodo, por categoría, comercio o persona; tus mayores gastos; comparar dos periodos, también en porcentaje; qué parte de tus gastos se fue en algo; y tu saldo. También puedo preparar un Bizum para uno de tus contactos guardados y enseñarte la vista previa. No se envía hasta que confirmes.

- **n/a** (IDENTITY: no es la cifra de una consulta)
- Esperado: `intent` — Explica sus capacidades en pocas frases; no consulta ni prepara nada.

**2.** «¿puedes hacer una transferencia a otro banco?» → `IDENTITY` · 11,0 s

> Puedo decirte cuánto gastaste o ingresaste en un periodo, por categoría, comercio o persona; tus mayores gastos; comparar dos periodos, también en porcentaje; qué parte de tus gastos se fue en algo; y tu saldo. También puedo preparar un Bizum para uno de tus contactos guardados y enseñarte la vista previa. No se envía hasta que confirmes.

- **n/a** (IDENTITY: no es la cifra de una consulta)
- Esperado: `follow_up` — Dice con honestidad si puede o no y qué alternativa tiene; no crea un borrador.

**3.** «pásame 100 euros de la corriente a la hucha» → `AWAITING_CONFIRMATION` · 22,0 s

> Vas a mover 100,00 € a Hucha. Te quedarían 471,56 €. ¿Confirmas? Responde sí o no.

- **n/a** (AWAITING_CONFIRMATION: no es la cifra de una consulta)
- Estado: workflow PAYMENT_CONFIRMATION, awaiting CONFIRMATION
- Esperado: `intent` PAYMENT — Previsualización del traspaso de 100 € de Cuenta corriente a Hucha; no lo ejecuta.

**4.** «mejor 50» → `NEEDS_CLARIFICATION` · 13,2 s

> ¿A cuál de tus cuentas lo muevo? Tienes Cuenta corriente, Hucha y Fondo indexado.

- **n/a** (NEEDS_CLARIFICATION: no es la cifra de una consulta)
- Estado: workflow PAYMENT_CONFIRMATION, awaiting CONFIRMATION
- Esperado: `slot_carryover` PAYMENT — Nueva previsualización de 50 € entre las mismas cuentas; no lo ejecuta.

**5.** «déjalo, no hagas nada» → `GUIDANCE` · 13,3 s

> De acuerdo, empezamos de nuevo. ¿Qué quieres consultar?

- **n/a** (GUIDANCE: no es la cifra de una consulta)
- Estado: workflow PAYMENT_CONFIRMATION, awaiting CONFIRMATION
- Esperado: `reset` — Cancela el traspaso y lo dice; nunca llama a la confirmación bancaria.

**6.** «¿cuánto llevo gastado en suscripciones este año?» → `PLANNED` · 10,0 s

> 665,52 € en suscripciones en lo que va de año, en 34 movimientos.
> Consultado en todas tus cuentas.

- Consulta: en lo que va de año, gastos, suscripciones · Cifra: 665,52 € en 34 mov. · SQL: 665,52 € en 34 mov. · Consulta comprobada: `{"categories": ["SUBSCRIPTIONS"], "direction": "EXPENSE", "end": null, "group_by": "NONE", "group_limit": null, "merchant_terms": [], "merchants": [], "metric": "SUM", "n": null, "period": "THIS_YEAR", "rank": null, "start": null}` · **match** (comprobada)
- Grupos: total 665,52 €
- Estado: workflow PAYMENT_CONFIRMATION, awaiting CONFIRMATION
- Esperado: `intent` QUERY — Total de suscripciones en lo que va de año; no arrastra cuentas ni importes del traspaso cancelado.

| Turno | Esperado | Cifras | intent | context | pending | clarification | recovery | naturalness 1-5 | mechanism | notes | s |
|---|---|---|---|---|---|---|---|---|---|---|---:|
| 1 | intent | n/a |  |  |  |  |  |  |  |  | 9,7 s |
| 2 | follow_up | n/a |  |  |  |  |  |  |  |  | 11,0 s |
| 3 | intent | n/a |  |  |  |  |  |  |  |  | 22,0 s |
| 4 | slot_carryover | n/a |  |  |  |  |  |  |  |  | 13,2 s |
| 5 | reset | n/a |  |  |  |  |  |  |  |  | 13,3 s |
| 6 | intent | match |  |  |  |  |  |  |  |  | 10,0 s |
