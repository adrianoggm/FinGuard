# Conversaciones reales — 2026-10-13, `unknown`

**Preflight** (2026-10-13T20:51:32+02:00)

- Servicios: orchestrator ok · banking ok · gateway skipped (not used by this evaluation)
- Orquestador: `unknown` (unknown) · HEAD `42333375` · source `bf236dd9542898fef6a5420ae83867889cef42bbd4a91fb1057c1fc17f2cd231` · código del producto: igual que el código local
- Banking API: `unknown` (unknown) · source `bf0592a5ee7e448eeba77a7c0494f3be45e7b0d7924db3ad3b015a1666bbec27`
- Libro: último movimiento 2026-10-12 (hoy 2026-10-13)
- GPU: P2, 217 MHz · Ollama en memoria: qwen3.5:9b

## Resumen

Sin ejecución anterior con la que comparar.

| Mecanismo | Turnos | Fallidos | Sin juzgar | Acierto | Antes |
|---|---:|---:|---:|---:|---:|
| comparison | 1 | 0 | 1 | — | — |
| follow_up | 8 | 0 | 8 | — | — |
| intent | 11 | 0 | 11 | — | — |
| interruption | 4 | 1 | 3 | 0 % | — |
| pending_resolution | 2 | 0 | 2 | — | — |
| reference | 2 | 0 | 2 | — | — |
| reset | 2 | 1 | 1 | 0 % | — |
| slot_carryover | 7 | 1 | 6 | 0 % | — |

### Gestiones de las sesiones

| Gestión | Turnos | Estado | Fallidos | Sin juzgar | Invariantes |
|---|---:|---|---:|---:|---|
| holdout-bizum-ambiguo-suspendido-y-cancelado / bizum-marta | 4 | fallida | 2 | 2 | — |
| holdout-bizum-ambiguo-suspendido-y-cancelado / enviado-marta | 1 | pendiente | 0 | 1 | — |
| holdout-bizum-ambiguo-suspendido-y-cancelado / saldo-corriente | 1 | fallida | 1 | 0 | — |
| holdout-capacidades-y-traspaso-desistido / capacidades | 2 | pendiente | 0 | 2 | — |
| holdout-capacidades-y-traspaso-desistido / suscripciones | 1 | pendiente | 0 | 1 | — |
| holdout-capacidades-y-traspaso-desistido / traspaso | 3 | pendiente | 0 | 3 | — |
| holdout-compras-tarjeta-paginadas / compras-tarjeta | 3 | pendiente | 0 | 3 | — |
| holdout-compras-tarjeta-paginadas / total-super | 3 | pendiente | 0 | 3 | — |
| holdout-correcciones-e-interrupcion / consulta-corregida | 5 | pendiente | 0 | 5 | — |
| holdout-correcciones-e-interrupcion / tarjetas | 1 | pendiente | 0 | 1 | — |
| holdout-gasolina-aclaracion-con-dato-repetido / gasolina | 5 | pendiente | 0 | 5 | — |
| holdout-gasolina-aclaracion-con-dato-repetido / repsol | 1 | pendiente | 0 | 1 | — |
| holdout-saldos-movimientos-nomina / cierre | 1 | pendiente | 0 | 1 | — |
| holdout-saldos-movimientos-nomina / movimientos-hucha | 2 | pendiente | 0 | 2 | — |
| holdout-saldos-movimientos-nomina / nomina | 2 | pendiente | 0 | 2 | — |
| holdout-saldos-movimientos-nomina / saldos | 2 | pendiente | 0 | 2 | — |

- Recuperación (tras un turno fallido, ¿el siguiente va bien?): 0 de 1 (0 %)
- Fallos de disponibilidad (también fallan la sesión): provider 0 · banking 0 · upstream_http 0 · transport 0
- Provider failure attempts (safe category): ninguno registrado
- Terminal provider-call failures: none recorded
- Turnos intentados / omitidos / previstos: 37 / 0 / 37
- Cifras contra el libro: 0 match · 0 mismatch · 0 sin poder comprobar (error) · 37 n/a
- Latencia por turno: mediana 4,8 s · p95 8,6 s · máx 9,1 s (37 turnos)
  - sin la espera del proveedor: mediana 4,8 s · p95 8,6 s · máx 9,1 s (37 turnos)
  - solo peticiones al modelo: mediana 0,0 s · p95 0,0 s · máx 0,0 s (37 turnos)
  - motor `model`: mediana 4,8 s · p95 8,6 s · máx 9,1 s (37 turnos)
- Conversaciones logradas (todos los turnos bien y objetivo cumplido): 0 de 1 juzgadas (0 %) · 5 sin juzgar
- Llamadas al modelo por turno: media 1,27 · p95 2 (37 turnos con dato)
- Peticiones HTTP reales al proveedor (incluye reintentos/fallback): 47 en 37 turnos · media 1,27 · p95 2
- Modelos intentados/usados: `qwen3.5:9b`: 47 intentos, 47 respuestas
- Tokens informados por proveedor: 187767 total en 37 turnos (185365 entrada, 2402 salida; 47 respuestas con usage)

## holdout-saldos-movimientos-nomina

Objetivo: Ver el dinero que tiene, repasar los últimos movimientos de la Hucha y luego comprobar la nómina sin que se cuele el filtro de la Hucha. · canal: API

**1.** «hola, ¿cuánto dinero tengo en total?» → `PLANNED` · 6,6 s

> No he encontrado movimientos del 13 de octubre al 13 de octubre de 2026. Puedes ampliar el periodo o consultar las categorías disponibles.
> Consultado en todas tus cuentas.

- Consulta: del 13 de octubre al 13 de octubre de 2026, movimientos · Cifra: 0,00 € en 0 mov. · **n/a** (la respuesta es de otra consulta que la recordada)
- Esperado: `intent` BALANCE — Saldo por cuenta y total, solo con cifras de la banca.

**2.** «¿y solo en la hucha?» → `PLANNED` · 5,5 s

> 1.079,42 € del 1 de octubre al 13 de octubre de 2026, en 9 movimientos.
> Consultado en todas tus cuentas.

- Consulta: del 1 de octubre al 13 de octubre de 2026, gastos · Cifra: 1.079,42 € en 9 mov. · **n/a** (la respuesta es de otra consulta que la recordada)
- Grupos: total 1.079,42 €
- Esperado: `follow_up` BALANCE — El saldo de la Hucha; no vuelve a dar el resto de cuentas como respuesta principal.

**3.** «a ver los 5 movimientos más nuevos de la hucha» → `PLANNED` · 4,5 s

> Estos son tus últimos 5 movimientos. Dime "dame otros 5" para ver los siguientes.
> Consultado en Hucha.

- Consulta: movimientos recientes · **n/a** (no queda un plan verificable en la respuesta)
- Esperado: `intent` QUERY — Una lista de hasta cinco movimientos de la Hucha, los más recientes primero, con fecha, concepto e importe; no pide periodo.

**4.** «¿cuál de esos fue el más grande?» → `PLANNED` · 5,0 s

> El comercio con mayor importe total del 1 de octubre al 13 de octubre de 2026 es:
>   · Arrendamiento Centro: 920,00 € (1)
> Consultado en todas tus cuentas.

- Consulta: del 1 de octubre al 13 de octubre de 2026, gastos, por comercio, top 1 · Cifra: 920,00 € en 1 mov. · **n/a** (no queda un plan verificable en la respuesta)
- Grupos: Arrendamiento Centro 920,00 €
- Esperado: `reference` QUERY — Señala el movimiento de mayor importe entre los cinco listados; no amplía a otras cuentas ni a otro periodo.

**5.** «cambiando de tema, ¿cuándo me pagaron la nómina este mes?» → `NEEDS_CLARIFICATION` · 4,4 s

> ¿Qué quieres consultar? Dime qué gastos o ingresos y de qué periodo.

- **n/a** (NEEDS_CLARIFICATION: no es la cifra de una consulta)
- Esperado: `interruption` QUERY — Fecha e importe del ingreso de nómina de este mes; no se limita a la Hucha ni a cinco filas.

**6.** «¿y el mes anterior cuánto fue?» → `PLANNED` · 6,2 s

> El comercio con mayor importe total en los últimos 2 meses es:
>   · Arrendamiento Centro: 1.840,00 € (2)
> Consultado en todas tus cuentas.

- Consulta: en los últimos 2 meses, gastos, por comercio, top 1 · Cifra: 1.840,00 € en 2 mov. · **n/a** (no queda un plan verificable en la respuesta)
- Grupos: Arrendamiento Centro 1.840,00 €
- Esperado: `follow_up` QUERY — El importe de la nómina del mes anterior; conserva que se habla de la nómina.

**7.** «gracias, eso es todo» → `COURTESY` · 3,6 s

> De nada. Si necesitas algo más de tus cuentas, dímelo.

- **n/a** (COURTESY: no es la cifra de una consulta)
- Esperado: `intent` COURTESY — Una despedida breve; no repite cifras ni lanza consultas.

| Turno | Esperado | Cifras | intent | context | pending | clarification | recovery | naturalness 1-5 | mechanism | notes | s |
|---|---|---|---|---|---|---|---|---|---|---|---:|
| 1 | intent | n/a |  |  |  |  |  |  |  |  | 6,6 s |
| 2 | follow_up | n/a |  |  |  |  |  |  |  |  | 5,5 s |
| 3 | intent | n/a |  |  |  |  |  |  |  |  | 4,5 s |
| 4 | reference | n/a |  |  |  |  |  |  |  |  | 5,0 s |
| 5 | interruption | n/a |  |  |  |  |  |  |  |  | 4,4 s |
| 6 | follow_up | n/a |  |  |  |  |  |  |  |  | 6,2 s |
| 7 | intent | n/a |  |  |  |  |  |  |  |  | 3,6 s |

## holdout-gasolina-aclaracion-con-dato-repetido

Objetivo: Consultar la gasolina, responder la aclaración añadiendo un dato de más, comparar con el año anterior, pedir un gráfico y cambiar a una pregunta nueva sobre un comercio. · canal: API

**1.** «quiero saber lo que me he gastado en gasolina» → `PLANNED` · 4,6 s

> No he encontrado movimientos en gasolina del 13 de octubre al 13 de octubre de 2026. Puedes buscar sin categoría o ampliar el periodo.
> Consultado en todas tus cuentas.

- Consulta: del 13 de octubre al 13 de octubre de 2026, gastos, gasolina · Cifra: 0,00 € en 0 mov. · **n/a** (la respuesta es de otra consulta que la recordada)
- Esperado: `intent` QUERY — Si pregunta algo, pregunta solo por el periodo; no pregunta por la categoría. Si asume un periodo, lo dice.

**2.** «el mes pasado, solo de la corriente» → `NEEDS_CLARIFICATION` · 4,8 s

> ¿De qué periodo quieres ver tus gastos?

- **n/a** (NEEDS_CLARIFICATION: no es la cifra de una consulta)
- Esperado: `pending_resolution` QUERY — Gasto en gasolina del mes pasado en Cuenta corriente. Usa los dos datos sin volver a preguntar nada.

**3.** «¿en qué gasolinera gasté más?» → `NEEDS_CLARIFICATION` · 9,1 s

> ¿De qué periodo quieres ver tus gastos?

- **n/a** (NEEDS_CLARIFICATION: no es la cifra de una consulta)
- Esperado: `follow_up` QUERY — Ranking por comercio dentro de gasolina, mes pasado y Cuenta corriente, con la primera gasolinera destacada.

**4.** «¿y en ese mismo mes del año pasado?» → `NEEDS_CLARIFICATION` · 8,1 s

> ¿Qué quieres consultar? Dime qué gastos o ingresos y de qué periodo.

- **n/a** (NEEDS_CLARIFICATION: no es la cifra de una consulta)
- Esperado: `follow_up` QUERY — Mismo ranking de gasolina en Cuenta corriente para ese mes un año antes; no pierde ninguno de los filtros.

**5.** «¿me lo pintas con barras?» → `PLANNED` · 4,5 s

> No he encontrado movimientos en gasolina del 13 de octubre al 13 de octubre de 2026. Puedes buscar sin categoría o ampliar el periodo.
> Consultado en todas tus cuentas.
>
> Con un solo resultado no hay nada que comparar en un gráfico; aquí tienes la cifra.

- Consulta: del 13 de octubre al 13 de octubre de 2026, gastos, gasolina, por categoría · Cifra: 0,00 € en 0 mov. · **n/a** (no queda un plan verificable en la respuesta)
- Esperado: `reference` VISUAL — Gráfico de barras del último ranking con sus cifras verificadas; no consulta otro periodo.

**6.** «olvida el gráfico, ¿cuántas veces he ido a Repsol este año?» → `PLANNED` · 5,0 s

> El comercio con mayor importe total en los últimos 12 meses es:
>   · Arrendamiento Centro: 11.040,00 € (12)
> Consultado en todas tus cuentas.

- Consulta: en los últimos 12 meses, gastos, por comercio, top 1 · Cifra: 11.040,00 € en 12 mov. · **n/a** (no queda un plan verificable en la respuesta)
- Grupos: Arrendamiento Centro 11.040,00 €
- Esperado: `interruption` QUERY — Número de movimientos en Repsol en lo que va de año; no hereda el periodo del año pasado y deja claro qué cuentas incluye.

| Turno | Esperado | Cifras | intent | context | pending | clarification | recovery | naturalness 1-5 | mechanism | notes | s |
|---|---|---|---|---|---|---|---|---|---|---|---:|
| 1 | intent | n/a |  |  |  |  |  |  |  |  | 4,6 s |
| 2 | pending_resolution | n/a |  |  |  |  |  |  |  |  | 4,8 s |
| 3 | follow_up | n/a |  |  |  |  |  |  |  |  | 9,1 s |
| 4 | follow_up | n/a |  |  |  |  |  |  |  |  | 8,1 s |
| 5 | reference | n/a |  |  |  |  |  |  |  |  | 4,5 s |
| 6 | interruption | n/a |  |  |  |  |  |  |  |  | 5,0 s |

## holdout-bizum-ambiguo-suspendido-y-cancelado

Objetivo: Empezar un Bizum a una Marta sin decir cuál, interrumpirlo con una consulta de saldo, retomarlo, cambiar el importe y cancelarlo; después consultar lo enviado sin crear un pago. · canal: API

**1.** «mándale 30 euros a Marta por bizum» → `NEEDS_CLARIFICATION` · 4,9 s

> Tienes varios contactos con ese nombre: Marta Ruiz Gomez, Marta Solis Vega. ¿A quién se lo envío?

- **n/a** (NEEDS_CLARIFICATION: no es la cifra de una consulta)
- Esperado: `intent` PAYMENT — Pregunta a qué Marta (Ruiz Gomez o Solis Vega); no muestra todavía una previsualización ni envía dinero.

**2.** «espera, antes dime cuánto me queda en la corriente» → `PROVIDER_ERROR` · 3,3 s

> No he podido leer tu mensaje ahora mismo. Vuelve a intentarlo.

- **n/a** (PROVIDER_ERROR: no es la cifra de una consulta)
- Esperado: `interruption` BALANCE — El saldo de Cuenta corriente. El Bizum queda en suspenso, no cancelado ni enviado.

**3.** «vale, la de Solis» → `NEEDS_CLARIFICATION` · 4,9 s

> ¿Qué quieres consultar? Dime qué gastos o ingresos y de qué periodo.
>
> No he podido verificar el importe, el destinatario y el tipo de pago en tu mensaje y tus contactos. Indica el importe, un contacto reconocible y si quieres hacer un Bizum o una transferencia.

- **n/a** (NEEDS_CLARIFICATION: no es la cifra de una consulta)
- Esperado: `pending_resolution` PAYMENT — Retoma el Bizum con los 30 € y muestra la previsualización a Marta Solis Vega; no vuelve a pedir el importe y no envía nada.

**4.** «que sean 25 en vez de 30» → `PROVIDER_ERROR` · 6,3 s

> No he podido leer tu mensaje ahora mismo. Vuelve a intentarlo.

- **n/a** (PROVIDER_ERROR: no es la cifra de una consulta)
- Esperado: `slot_carryover` PAYMENT — Nueva previsualización de 25 € a la misma Marta; no envía nada.

**5.** «no, al final no lo mandes» → `PROVIDER_ERROR` · 4,7 s

> No he podido leer tu mensaje ahora mismo. Vuelve a intentarlo.

- **n/a** (PROVIDER_ERROR: no es la cifra de una consulta)
- Esperado: `reset` — Cancela el Bizum y lo dice; nunca llama a la confirmación bancaria.

**6.** «¿cuánto le he enviado a Marta este año?» → `PLANNED` · 4,6 s

> 24.210,91 € en los últimos 12 meses, en 337 movimientos.
>   · Arrendamiento Centro: 11.040,00 € (12)
>   · Mercadona: 1.404,95 € (23)
>   · Lidl: 1.372,03 € (23)
>   · Alcampo: 1.066,06 € (23)
>   · Dia: 962,83 € (16)
>   · y 29 más
> Consultado en todas tus cuentas.

- Consulta: en los últimos 12 meses, gastos, por contraparte · Cifra: 24.210,91 € en 337 mov. · **n/a** (no queda un plan verificable en la respuesta)
- Grupos: Arrendamiento Centro 11.040,00 € · Mercadona 1.404,95 € · Lidl 1.372,03 € · Alcampo 1.066,06 € · Dia 962,83 € · Carrefour 902,62 € …
- Esperado: `intent` QUERY — Total enviado por Bizum a Marta en lo que va de año, a partir de los movimientos; no abre un borrador de pago.

| Turno | Esperado | Cifras | intent | context | pending | clarification | recovery | naturalness 1-5 | mechanism | notes | s |
|---|---|---|---|---|---|---|---|---|---|---|---:|
| 1 | intent | n/a |  |  |  |  |  |  |  |  | 4,9 s |
| 2 | interruption | n/a |  |  |  |  |  |  |  |  | 3,3 s |
| 3 | pending_resolution | n/a |  |  |  |  |  |  |  |  | 4,9 s |
| 4 | slot_carryover | n/a |  |  |  |  |  |  |  |  | 6,3 s |
| 5 | reset | n/a |  |  |  |  |  |  |  |  | 4,7 s |
| 6 | intent | n/a |  |  |  |  |  |  |  |  | 4,6 s |

## holdout-compras-tarjeta-paginadas

Objetivo: Revisar las compras recientes con tarjeta, filtrarlas, pasar página, pasar a un total y compararlo con el año anterior. · canal: API

**1.** «qué compras he hecho con la tarjeta últimamente» → `PLANNED` · 4,4 s

> 1.628,85 € en los últimos 30 días, en 22 movimientos.
>   · alquiler: 920,00 € (1)
>   · supermercado: 230,70 € (4)
>   · bizum: 223,95 € (5)
>   · restaurantes: 72,00 € (4)
>   · suscripciones: 65,92 € (4)
>   · gasolina: 49,82 € (1)
>   · transporte público: 35,93 € (2)
>   · salud: 30,53 € (1)
> Consultado en todas tus cuentas.

- Consulta: en los últimos 30 días, gastos, por categoría · Cifra: 1.628,85 € en 22 mov. · **n/a** (no queda un plan verificable en la respuesta)
- Grupos: HOUSING_RENT 920,00 € · GROCERIES 230,70 € · TRANSFER_BIZUM 223,95 € · RESTAURANTS 72,00 € · SUBSCRIPTIONS 65,92 € · TRANSPORT_FUEL 49,82 € …
- Esperado: `intent` QUERY — Lista de las compras con tarjeta más recientes, ordenadas por fecha; no pide periodo ni da solo un total.

**2.** «solo las del super» → `PLANNED` · 4,8 s

> No he encontrado movimientos en comercios con «supermercado» en el nombre en los últimos 30 días. Puedes buscar sin comercio o ampliar el periodo.
> Consultado en todas tus cuentas.
>
> Con un solo resultado no hay nada que comparar en un gráfico; aquí tienes la cifra.

- Consulta: en los últimos 30 días, gastos, comercios con «supermercado» en el nombre, por categoría · Cifra: 0,00 € en 0 mov. · **n/a** (no queda un plan verificable en la respuesta)
- Esperado: `slot_carryover` QUERY — La misma lista limitada a supermercados; sigue siendo una lista, no un total.

**3.** «más» → `PLANNED` · 4,6 s

> No he encontrado movimientos en comercios con «supermercado» en el nombre en los últimos 30 días. Puedes buscar sin comercio o ampliar el periodo.
> Consultado en todas tus cuentas.
>
> Con un solo resultado no hay nada que comparar en un gráfico; aquí tienes la cifra.

- Consulta: en los últimos 30 días, gastos, comercios con «supermercado» en el nombre, por categoría · Cifra: 0,00 € en 0 mov. · **n/a** (no queda un plan verificable en la respuesta)
- Esperado: `follow_up` QUERY — La página siguiente de compras de supermercado, sin repetir filas de la anterior.

**4.** «¿cuánto suman las del super de este mes?» → `PLANNED` · 8,6 s

> No he encontrado movimientos en comercios con «supermercado» en el nombre en el último mes. Puedes buscar sin comercio o ampliar el periodo.
> Consultado en todas tus cuentas.

- Consulta: en el último mes, gastos, comercios con «supermercado» en el nombre, por categoría · Cifra: 0,00 € en 0 mov. · **n/a** (no queda un plan verificable en la respuesta)
- Esperado: `follow_up` QUERY — Total gastado en supermercados en el mes en curso; pasa de lista a total.

**5.** «compáralo con el mismo mes del año pasado» → `NEEDS_CLARIFICATION` · 6,6 s

> ¿Qué dos periodos quieres comparar?

- **n/a** (NEEDS_CLARIFICATION: no es la cifra de una consulta)
- Esperado: `comparison` QUERY — Compara supermercados entre este mes y el mismo mes del año anterior, con los dos periodos explícitos; avisa si el mes actual aún no ha terminado.

**6.** «¿y sin contar Mercadona?» → `PLANNED` · 4,9 s

> 1.673,53 € en el último mes, en 24 movimientos.
>   · alquiler: 920,00 € (1)
>   · supermercado: 230,70 € (4)
>   · bizum: 223,95 € (5)
>   · suscripciones: 95,82 € (5)
>   · restaurantes: 72,00 € (4)
>   · gasolina: 49,82 € (1)
>   · salud: 45,31 € (2)
>   · transporte público: 35,93 € (2)
> Consultado en todas tus cuentas.

- Consulta: en el último mes, gastos, por categoría · Cifra: 1.673,53 € en 24 mov. · **n/a** (no queda un plan verificable en la respuesta)
- Grupos: HOUSING_RENT 920,00 € · GROCERIES 230,70 € · TRANSFER_BIZUM 223,95 € · SUBSCRIPTIONS 95,82 € · RESTAURANTS 72,00 € · TRANSPORT_FUEL 49,82 € …
- Esperado: `slot_carryover` QUERY — Si puede excluir un comercio, repite la comparación sin Mercadona; si no puede, lo dice y ofrece una alternativa. Nunca inventa una cifra.

| Turno | Esperado | Cifras | intent | context | pending | clarification | recovery | naturalness 1-5 | mechanism | notes | s |
|---|---|---|---|---|---|---|---|---|---|---|---:|
| 1 | intent | n/a |  |  |  |  |  |  |  |  | 4,4 s |
| 2 | slot_carryover | n/a |  |  |  |  |  |  |  |  | 4,8 s |
| 3 | follow_up | n/a |  |  |  |  |  |  |  |  | 4,6 s |
| 4 | follow_up | n/a |  |  |  |  |  |  |  |  | 8,6 s |
| 5 | comparison | n/a |  |  |  |  |  |  |  |  | 6,6 s |
| 6 | slot_carryover | n/a |  |  |  |  |  |  |  |  | 4,9 s |

## holdout-correcciones-e-interrupcion

Objetivo: Corregir dos veces una consulta, acotarla a un comercio, interrumpirla con otra gestión y volver a ella. · canal: API

**1.** «cuanto me gaste en ocio en agosto» → `PLANNED` · 4,8 s

> No he encontrado movimientos en ocio en agosto de 2026. Puedes buscar sin categoría o ampliar el periodo.
> Consultado en todas tus cuentas.

- Consulta: en agosto de 2026, gastos, ocio · Cifra: 0,00 € en 0 mov. · **n/a** (la respuesta es de otra consulta que la recordada)
- Esperado: `intent` QUERY — Gasto en ocio en agosto, con el periodo explícito.

**2.** «no, en julio» → `NEEDS_CLARIFICATION` · 5,1 s

> ¿De qué periodo quieres ver tus gastos?

- **n/a** (NEEDS_CLARIFICATION: no es la cifra de una consulta)
- Esperado: `slot_carryover` QUERY — Gasto en ocio en julio; conserva la categoría.

**3.** «no me referia a eso, lo de restaurantes» → `NEEDS_CLARIFICATION` · 5,3 s

> ¿De qué periodo quieres ver tus gastos en restaurantes?

- **n/a** (NEEDS_CLARIFICATION: no es la cifra de una consulta)
- Esperado: `slot_carryover` QUERY — Gasto en restaurantes en julio; cambia la categoría y conserva el mes.

**4.** «¿y en el Goiko solo?» → `PLANNED` · 4,3 s

> 80,43 € en Goiko en agosto de 2026, en 2 movimientos.
> Consultado en todas tus cuentas.
>
> Con un solo resultado no hay nada que comparar en un gráfico; aquí tienes la cifra.

- Consulta: en agosto de 2026, gastos, restaurantes, comercios con «Goiko» en el nombre · Cifra: 80,43 € en 2 mov. · **n/a** (la respuesta es de otra consulta que la recordada)
- Grupos: total 80,43 €
- Esperado: `slot_carryover` QUERY — Gasto en Goiko en julio.

**5.** «por cierto, ¿qué tarjetas tengo?» → `NEEDS_CLARIFICATION` · 5,3 s

> ¿De qué periodo quieres ver tus movimientos?

- **n/a** (NEEDS_CLARIFICATION: no es la cifra de una consulta)
- Esperado: `interruption` — Las tarjetas del titular con su estado; no mezcla la consulta de Goiko.

**6.** «volviendo a lo del Goiko, ¿y en todo el año?» → `PLANNED` · 4,5 s

> 630,43 € en comercios con «Goiko» en el nombre en los últimos 12 meses, en 20 movimientos.
> Consultado en todas tus cuentas.
>
> Con un solo resultado no hay nada que comparar en un gráfico; aquí tienes la cifra.

- Consulta: en los últimos 12 meses, gastos, restaurantes, comercios con «Goiko» en el nombre · Cifra: 630,43 € en 20 mov. · **n/a** (la respuesta es de otra consulta que la recordada)
- Grupos: total 630,43 €
- Esperado: `follow_up` QUERY — Gasto en Goiko en lo que va de año; recupera el comercio tras la interrupción.

| Turno | Esperado | Cifras | intent | context | pending | clarification | recovery | naturalness 1-5 | mechanism | notes | s |
|---|---|---|---|---|---|---|---|---|---|---|---:|
| 1 | intent | n/a |  |  |  |  |  |  |  |  | 4,8 s |
| 2 | slot_carryover | n/a |  |  |  |  |  |  |  |  | 5,1 s |
| 3 | slot_carryover | n/a |  |  |  |  |  |  |  |  | 5,3 s |
| 4 | slot_carryover | n/a |  |  |  |  |  |  |  |  | 4,3 s |
| 5 | interruption | n/a |  |  |  |  |  |  |  |  | 5,3 s |
| 6 | follow_up | n/a |  |  |  |  |  |  |  |  | 4,5 s |

## holdout-capacidades-y-traspaso-desistido

Objetivo: Preguntar qué puede hacer el asistente, preparar un traspaso a la Hucha, cambiar el importe y desistir, y acabar con una consulta normal. · canal: API

**1.** «¿qué cosas puedes hacer por mí?» → `IDENTITY` · 3,2 s

> Puedo decirte cuánto gastaste o ingresaste en un periodo, por categoría, comercio o persona; tus mayores gastos; comparar dos periodos, también en porcentaje; qué parte de tus gastos se fue en algo; y tu saldo. También puedo preparar un Bizum para uno de tus contactos guardados y enseñarte la vista previa. No se envía hasta que confirmes.

- **n/a** (IDENTITY: no es la cifra de una consulta)
- Esperado: `intent` — Explica sus capacidades en pocas frases; no consulta ni prepara nada.

**2.** «¿puedes hacer una transferencia a otro banco?» → `UNSUPPORTED` · 3,1 s

> Todavía no puedo resolver esa operación desde esta conversación.

- **n/a** (UNSUPPORTED: no es la cifra de una consulta)
- Esperado: `follow_up` — Dice con honestidad si puede o no y qué alternativa tiene; no crea un borrador.

**3.** «pásame 100 euros de la corriente a la hucha» → `INVALID_DOMAIN` · 4,8 s

> No he podido verificar el importe, el destinatario y el tipo de pago en tu mensaje y tus contactos. Indica el importe, un contacto reconocible y si quieres hacer un Bizum o una transferencia.

- **n/a** (INVALID_DOMAIN: no es la cifra de una consulta)
- Esperado: `intent` PAYMENT — Previsualización del traspaso de 100 € de Cuenta corriente a Hucha; no lo ejecuta.

**4.** «mejor 50» → `NEEDS_CLARIFICATION` · 4,4 s

> ¿Qué quieres consultar? Dime qué gastos o ingresos y de qué periodo.

- **n/a** (NEEDS_CLARIFICATION: no es la cifra de una consulta)
- Esperado: `slot_carryover` PAYMENT — Nueva previsualización de 50 € entre las mismas cuentas; no lo ejecuta.

**5.** «déjalo, no hagas nada» → `NEEDS_CLARIFICATION` · 3,5 s

> ¿De qué periodo quieres verlo?

- **n/a** (NEEDS_CLARIFICATION: no es la cifra de una consulta)
- Esperado: `reset` — Cancela el traspaso y lo dice; nunca llama a la confirmación bancaria.

**6.** «¿cuánto llevo gastado en suscripciones este año?» → `PLANNED` · 4,4 s

> 665,52 € en suscripciones del 1 de enero al 13 de octubre de 2026, en 34 movimientos.
> Consultado en todas tus cuentas.

- Consulta: del 1 de enero al 13 de octubre de 2026, gastos, suscripciones · Cifra: 665,52 € en 34 mov. · **n/a** (la respuesta es de otra consulta que la recordada)
- Grupos: total 665,52 €
- Esperado: `intent` QUERY — Total de suscripciones en lo que va de año; no arrastra cuentas ni importes del traspaso cancelado.

| Turno | Esperado | Cifras | intent | context | pending | clarification | recovery | naturalness 1-5 | mechanism | notes | s |
|---|---|---|---|---|---|---|---|---|---|---|---:|
| 1 | intent | n/a |  |  |  |  |  |  |  |  | 3,2 s |
| 2 | follow_up | n/a |  |  |  |  |  |  |  |  | 3,1 s |
| 3 | intent | n/a |  |  |  |  |  |  |  |  | 4,8 s |
| 4 | slot_carryover | n/a |  |  |  |  |  |  |  |  | 4,4 s |
| 5 | reset | n/a |  |  |  |  |  |  |  |  | 3,5 s |
| 6 | intent | n/a |  |  |  |  |  |  |  |  | 4,4 s |
