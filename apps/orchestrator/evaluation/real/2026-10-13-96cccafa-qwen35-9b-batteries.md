# Conversaciones reales — 2026-10-13, `unknown`

**Preflight** (2026-10-13T21:05:28+02:00)

- Servicios: orchestrator ok · banking ok · gateway skipped (not used by this evaluation)
- Orquestador: `unknown` (unknown) · HEAD `96cccafa` · source `bf236dd9542898fef6a5420ae83867889cef42bbd4a91fb1057c1fc17f2cd231` · código del producto: igual que el código local
- Banking API: `unknown` (unknown) · source `bf0592a5ee7e448eeba77a7c0494f3be45e7b0d7924db3ad3b015a1666bbec27`
- Libro: último movimiento 2026-10-12 (hoy 2026-10-13)
- GPU: P2, 217 MHz · Ollama en memoria: qwen3.5:9b

## Resumen

Sin ejecución anterior con la que comparar.

| Mecanismo | Turnos | Fallidos | Sin juzgar | Acierto | Antes |
|---|---:|---:|---:|---:|---:|
| follow_up | 4 | 0 | 4 | — | — |
| intent | 3 | 0 | 3 | — | — |
| interruption | 1 | 0 | 1 | — | — |
| pending_resolution | 1 | 0 | 1 | — | — |
| reference | 3 | 0 | 3 | — | — |
| reset | 1 | 0 | 1 | — | — |

### Gestiones de las sesiones

| Gestión | Turnos | Estado | Fallidos | Sin juzgar | Invariantes |
|---|---:|---|---:|---:|---|
| bizum-interrumpido-y-reanudado / bizum-a-marta | 3 | pendiente | 0 | 3 | — |
| bizum-interrumpido-y-reanudado / saldo-intercalado | 1 | pendiente | 0 | 1 | — |
| edicion-continua-de-grafico / grafico-por-categoria | 6 | pendiente | 0 | 6 | — |
| referencias-ranking-comparacion-categoria / mes-de-mayor-gasto | 3 | pendiente | 0 | 3 | — |

- Recuperación (tras un turno fallido, ¿el siguiente va bien?): 0 de 0 (—)
- Fallos de disponibilidad (también fallan la sesión): provider 0 · banking 0 · upstream_http 0 · transport 0
- Provider failure attempts (safe category): ninguno registrado
- Terminal provider-call failures: none recorded
- Turnos intentados / omitidos / previstos: 13 / 0 / 13
- Cifras contra el libro: 0 match · 0 mismatch · 0 sin poder comprobar (error) · 13 n/a
- Latencia por turno: mediana 5,4 s · p95 10,8 s · máx 10,8 s (13 turnos)
  - sin la espera del proveedor: mediana 5,4 s · p95 10,8 s · máx 10,8 s (13 turnos)
  - solo peticiones al modelo: mediana 0,0 s · p95 0,0 s · máx 0,0 s (13 turnos)
  - motor `model`: mediana 5,4 s · p95 10,8 s · máx 10,8 s (13 turnos)
- Conversaciones logradas (todos los turnos bien y objetivo cumplido): 0 de 0 juzgadas (—) · 3 sin juzgar
- Llamadas al modelo por turno: media 1,77 · p95 3 (13 turnos con dato)
- Peticiones HTTP reales al proveedor (incluye reintentos/fallback): 23 en 13 turnos · media 1,77 · p95 3
- Modelos intentados/usados: `qwen3.5:9b`: 23 intentos, 23 respuestas
- Tokens informados por proveedor: 82097 total en 13 turnos (81070 entrada, 1027 salida; 23 respuestas con usage)

## referencias-ranking-comparacion-categoria

Objetivo: Encontrar el mes de mayor gasto, compararlo con el mes actual y desglosar ese mismo mes. · canal: API

**1.** «¿Cuál fue el mes de mayor gasto este año?» → `PLANNED` · 4,5 s

> El mes con mayor importe total en los últimos 12 meses es:
>   · abril de 2026: 2.294,13 € (32)
> Consultado en todas tus cuentas.

- Consulta: en los últimos 12 meses, gastos, por mes, top 1 · Cifra: 2.294,13 € en 32 mov. · **n/a** (no queda un plan verificable en la respuesta)
- Grupos: 2026-04 2.294,13 €
- Esperado: `intent` QUERY — Nombra el mes de mayor gasto del año (abril de 2026 en el seed) con su importe.

**2.** «Compáralo con el actual» → `NEEDS_CLARIFICATION` · 4,3 s

> ¿Qué dos periodos quieres comparar?

- **n/a** (NEEDS_CLARIFICATION: no es la cifra de una consulta)
- Esperado: `reference` QUERY — Compara ese mes (abril) con el mes en curso (octubre), con ambos importes del banco.

**3.** «¿Y en qué categoría gasté más ese mes?» → `PLANNED` · 5,2 s

> La categoría con mayor importe total en los últimos 12 meses es:
>   · alquiler: 11.040,00 € (12)
> Consultado en todas tus cuentas.
>
> Aquí tienes el gráfico de los resultados de la consulta.

- Consulta: en los últimos 12 meses, gastos, por categoría, top 1 · Cifra: 11.040,00 € en 12 mov. · **n/a** (no queda un plan verificable en la respuesta)
- Grupos: HOUSING_RENT 11.040,00 €
- Sugerencias: «Ordena de menor a mayor» · «Ponlo en vertical»
- Esperado: `reference` QUERY — La categoría de mayor gasto de abril de 2026, no del año ni del mes en curso.

| Turno | Esperado | Cifras | intent | context | pending | clarification | recovery | naturalness 1-5 | mechanism | notes | s |
|---|---|---|---|---|---|---|---|---|---|---|---:|
| 1 | intent | n/a |  |  |  |  |  |  |  |  | 4,5 s |
| 2 | reference | n/a |  |  |  |  |  |  |  |  | 4,3 s |
| 3 | reference | n/a |  |  |  |  |  |  |  |  | 5,2 s |

## edicion-continua-de-grafico

Objetivo: Crear un gráfico de gastos por categoría y editarlo varias veces sin perder cambios ni leer el banco. · canal: API

**1.** «Muéstrame mis gastos de este año por categoría» → `PLANNED` · 4,2 s

> 24.210,91 € en los últimos 12 meses, en 337 movimientos.
>   · alquiler: 11.040,00 € (12)
>   · supermercado: 5.708,49 € (104)
>   · restaurantes: 2.521,49 € (73)
>   · gasolina: 1.947,39 € (37)
>   · suscripciones: 775,28 € (40)
>   · bizum: 759,46 € (18)
>   · transporte público: 666,14 € (32)
>   · salud: 633,92 € (15)
>   · ocio: 158,74 € (6)
> Consultado en todas tus cuentas.

- Consulta: en los últimos 12 meses, gastos, por categoría · Cifra: 24.210,91 € en 337 mov. · **n/a** (no queda un plan verificable en la respuesta)
- Grupos: HOUSING_RENT 11.040,00 € · GROCERIES 5.708,49 € · RESTAURANTS 2.521,49 € · TRANSPORT_FUEL 1.947,39 € · SUBSCRIPTIONS 775,28 € · TRANSFER_BIZUM 759,46 € …
- Esperado: `intent` QUERY — Desglose de gastos del año por categoría.

**2.** «Ponlo en gráfico» → `PLANNED` · 8,6 s

> Aquí tienes el gráfico de los resultados de la consulta.

- Consulta: en los últimos 12 meses, gastos, por categoría · Cifra: 24.210,91 € en 337 mov. · **n/a** (no queda un plan verificable en la respuesta)
- Sugerencias: «Ordena de menor a mayor» · «Ponlo en vertical»
- Esperado: `reference` VISUAL — Un gráfico de esas categorías, sin nueva lectura bancaria.

**3.** «Vertical» → `INVALID_DOMAIN` · 6,8 s

> Ese cambio no encaja con los datos de este gráfico; lo dejo como estaba.

- Consulta: en los últimos 12 meses, gastos, por categoría · Cifra: 24.210,91 € en 337 mov. · **n/a** (INVALID_DOMAIN: no es la cifra de una consulta)
- Esperado: `follow_up` VISUAL — Las mismas barras en vertical; mismo total, sin nueva lectura bancaria.

**4.** «Ordena ascendente» → `PLANNED` · 10,8 s

> 24.210,91 € en los últimos 12 meses, en 337 movimientos.
>   · alquiler: 11.040,00 € (12)
>   · supermercado: 5.708,49 € (104)
>   · restaurantes: 2.521,49 € (73)
>   · gasolina: 1.947,39 € (37)
>   · suscripciones: 775,28 € (40)
>   · bizum: 759,46 € (18)
>   · transporte público: 666,14 € (32)
>   · salud: 633,92 € (15)
>   · ocio: 158,74 € (6)
> Consultado en todas tus cuentas.
>
> Aquí tienes el gráfico de los resultados de la consulta.

- Consulta: en los últimos 12 meses, gastos, por categoría · Cifra: 24.210,91 € en 337 mov. · **n/a** (no queda un plan verificable en la respuesta)
- Grupos: HOUSING_RENT 11.040,00 € · GROCERIES 5.708,49 € · RESTAURANTS 2.521,49 € · TRANSPORT_FUEL 1.947,39 € · SUBSCRIPTIONS 775,28 € · TRANSFER_BIZUM 759,46 € …
- Sugerencias: «Ordena de menor a mayor» · «Ponlo en vertical»
- Esperado: `follow_up` VISUAL — Barras verticales ordenadas de menor a mayor; mismo total.

**5.** «Solo las cinco mayores» → `NEEDS_CLARIFICATION` · 6,5 s

> ¿Qué quieres consultar? Dime qué gastos o ingresos y de qué periodo.

- **n/a** (NEEDS_CLARIFICATION: no es la cifra de una consulta)
- Esperado: `follow_up` VISUAL — Las cinco categorías mayores, verticales y ascendentes; el total de la consulta no cambia.

**6.** «Vuelve a mostrar todas» → `PLANNED` · 6,9 s

> 24.210,91 € en los últimos 12 meses, en 337 movimientos.
>   · alquiler: 11.040,00 € (12)
>   · supermercado: 5.708,49 € (104)
>   · restaurantes: 2.521,49 € (73)
>   · gasolina: 1.947,39 € (37)
>   · suscripciones: 775,28 € (40)
>   · bizum: 759,46 € (18)
>   · transporte público: 666,14 € (32)
>   · salud: 633,92 € (15)
>   · ocio: 158,74 € (6)
> Consultado en todas tus cuentas.
>
> Aquí tienes el gráfico de los resultados de la consulta.

- Consulta: en los últimos 12 meses, gastos, por categoría · Cifra: 24.210,91 € en 337 mov. · **n/a** (no queda un plan verificable en la respuesta)
- Grupos: HOUSING_RENT 11.040,00 € · GROCERIES 5.708,49 € · RESTAURANTS 2.521,49 € · TRANSPORT_FUEL 1.947,39 € · SUBSCRIPTIONS 775,28 € · TRANSFER_BIZUM 759,46 € …
- Sugerencias: «Ordena de menor a mayor» · «Ponlo en vertical»
- Esperado: `follow_up` VISUAL — Todas las categorías otra vez, verticales y ascendentes.

| Turno | Esperado | Cifras | intent | context | pending | clarification | recovery | naturalness 1-5 | mechanism | notes | s |
|---|---|---|---|---|---|---|---|---|---|---|---:|
| 1 | intent | n/a |  |  |  |  |  |  |  |  | 4,2 s |
| 2 | reference | n/a |  |  |  |  |  |  |  |  | 8,6 s |
| 3 | follow_up | n/a |  |  |  |  |  |  |  |  | 6,8 s |
| 4 | follow_up | n/a |  |  |  |  |  |  |  |  | 10,8 s |
| 5 | follow_up | n/a |  |  |  |  |  |  |  |  | 6,5 s |
| 6 | follow_up | n/a |  |  |  |  |  |  |  |  | 6,9 s |

## bizum-interrumpido-y-reanudado

Objetivo: Empezar un Bizum, interrumpirlo con una consulta de saldo, retomarlo y cancelarlo en la vista previa. · canal: API

**1.** «Quiero hacerle un bizum a Marta Solis Vega» → `PLANNED` · 6,0 s

> No he encontrado movimientos a Marta Solis Vega del 13 de octubre al 13 de octubre de 2026. Puedes buscar sin comercio o ampliar el periodo.
> Consultado en todas tus cuentas.

- Consulta: del 13 de octubre al 13 de octubre de 2026, gastos, Marta Solis Vega · Cifra: 0,00 € en 0 mov. · **n/a** (consulta con filtros privados omitidos del plan verificable)
- Esperado: `intent` PAYMENT — Pide solo el importe; destinatario Marta Solis Vega.

**2.** «Espera, ¿cuánto tengo en la cuenta corriente?» → `PLANNED` · 4,6 s

> No he encontrado movimientos del 13 de octubre al 13 de octubre de 2026. Puedes ampliar el periodo o consultar las categorías disponibles.
> Consultado en todas tus cuentas.

- Consulta: del 13 de octubre al 13 de octubre de 2026, movimientos · Cifra: 0,00 € en 0 mov. · **n/a** (la respuesta es de otra consulta que la recordada)
- Esperado: `interruption` BALANCE — Saldo de la cuenta corriente, sin crear ni perder el borrador del Bizum.

**3.** «Vale, sigamos con el bizum, que sean 15 euros» → `INVALID_DOMAIN` · 5,4 s

> No puedo combinar ese Bizum con las otras operaciones de este turno.

- **n/a** (INVALID_DOMAIN: no es la cifra de una consulta)
- Esperado: `pending_resolution` PAYMENT — Vista previa de 15 € a Marta Solis Vega con botones de confirmación; no envía dinero.

**4.** «No, mejor no» → `PLANNED` · 5,3 s

> No he encontrado movimientos del 13 de octubre al 13 de octubre de 2026. Puedes ampliar el periodo o consultar las categorías disponibles.
> Consultado en todas tus cuentas.

- Consulta: del 13 de octubre al 13 de octubre de 2026, movimientos · Cifra: 0,00 € en 0 mov. · **n/a** (la respuesta es de otra consulta que la recordada)
- Esperado: `reset` — Cancela la vista previa; no se envía nada.

| Turno | Esperado | Cifras | intent | context | pending | clarification | recovery | naturalness 1-5 | mechanism | notes | s |
|---|---|---|---|---|---|---|---|---|---|---|---:|
| 1 | intent | n/a |  |  |  |  |  |  |  |  | 6,0 s |
| 2 | interruption | n/a |  |  |  |  |  |  |  |  | 4,6 s |
| 3 | pending_resolution | n/a |  |  |  |  |  |  |  |  | 5,4 s |
| 4 | reset | n/a |  |  |  |  |  |  |  |  | 5,3 s |
