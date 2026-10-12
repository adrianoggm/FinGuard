# Conversaciones reales — 2026-10-11, `bd52f69b`

**Preflight** (2026-10-11T23:34:46+02:00)

- Servicios: orchestrator ok · banking ok · gateway skipped (not used by this evaluation)
- Orquestador: `bd52f69b` (task/legacy-removal) · HEAD `bd52f69b` · source `1b770438bbd0cbeb50326545ceb81e965d6090fc8c78b224a0eb1a8f34180fd5` · código del producto: igual que el código local
- Banking API: `bd52f69b` (task/legacy-removal) · source `ca81ab863f670021662c0e93710024262c90c95228a4e61ad03e25f99c62212a`
- Libro: último movimiento 2026-10-11 (hoy 2026-10-11) · antigüedad aceptada a mano: 4 días
- GPU: P8, 22 MHz · Ollama en memoria: qwen2.5-coder:7b

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
- Latencia por turno: mediana 13,4 s · p95 36,2 s · máx 36,2 s (13 turnos)
  - motor `model`: mediana 13,4 s · p95 36,2 s · máx 36,2 s (13 turnos)
- Conversaciones logradas (todos los turnos bien y objetivo cumplido): 0 de 0 juzgadas (—) · 3 sin juzgar
- Llamadas al modelo por turno: media 1,38 · p95 3 (13 turnos con dato)
- Peticiones HTTP reales al proveedor (incluye reintentos/fallback): 15 en 13 turnos · media 1,15 · p95 2
- Modelos intentados/usados: `gemini-3.5-flash-lite`: 15 intentos, 15 respuestas
- Tokens informados por proveedor: 63943 total en 12 turnos (62872 entrada, 1071 salida; 15 respuestas con usage)

## referencias-ranking-comparacion-categoria

Objetivo: Encontrar el mes de mayor gasto, compararlo con el mes actual y desglosar ese mismo mes. · canal: API

**1.** «¿Cuál fue el mes de mayor gasto este año?» → `PLANNED` · 11,1 s

> El mes con mayor importe total en lo que va de año es:
>   · abril de 2026: 2.294,13 € (32)
> Consultado en todas tus cuentas.

- Consulta: en lo que va de año, gastos, por mes, top 1 · Cifra: 2.294,13 € en 32 mov. · **n/a** (no queda un plan verificable en la respuesta)
- Grupos: 2026-04 2.294,13 €
- Esperado: `intent` QUERY — Nombra el mes de mayor gasto del año (abril de 2026 en el seed) con su importe.

**2.** «Compáralo con el actual» → `NEEDS_CLARIFICATION` · 12,5 s

> ¿Qué quieres consultar? Dime qué gastos o ingresos y de qué periodo.

- **n/a** (NEEDS_CLARIFICATION: no es la cifra de una consulta)
- Esperado: `reference` QUERY — Compara ese mes (abril) con el mes en curso (octubre), con ambos importes del banco.

**3.** «¿Y en qué categoría gasté más ese mes?» → `PLANNED` · 9,6 s

> La categoría con mayor importe total del 2026-04-01 al 2026-04-30 es:
>   · alquiler: 920,00 € (1)
> Consultado en todas tus cuentas.

- Consulta: del 2026-04-01 al 2026-04-30, gastos, por categoría, top 1 · Cifra: 920,00 € en 1 mov. · **n/a** (no queda un plan verificable en la respuesta)
- Grupos: HOUSING_RENT 920,00 €
- Esperado: `reference` QUERY — La categoría de mayor gasto de abril de 2026, no del año ni del mes en curso.

| Turno | Esperado | Cifras | intent | context | pending | clarification | recovery | naturalness 1-5 | mechanism | notes | s |
|---|---|---|---|---|---|---|---|---|---|---|---:|
| 1 | intent | n/a |  |  |  |  |  |  |  |  | 11,1 s |
| 2 | reference | n/a |  |  |  |  |  |  |  |  | 12,5 s |
| 3 | reference | n/a |  |  |  |  |  |  |  |  | 9,6 s |

## edicion-continua-de-grafico

Objetivo: Crear un gráfico de gastos por categoría y editarlo varias veces sin perder cambios ni leer el banco. · canal: API

**1.** «Muéstrame mis gastos de este año por categoría» → `PLANNED` · 13,4 s

> 19.306,70 € en lo que va de año, en 266 movimientos.
>   · alquiler: 9.200,00 € (10)
>   · supermercado: 4.219,38 € (76)
>   · restaurantes: 2.086,68 € (61)
>   · gasolina: 1.447,21 € (28)
>   · suscripciones: 665,52 € (34)
>   · transporte público: 659,38 € (31)
>   · bizum: 587,72 € (13)
>   · salud: 377,91 € (9)
>   · ocio: 62,90 € (4)
> Consultado en todas tus cuentas.

- Consulta: en lo que va de año, gastos, por categoría · Cifra: 19.306,70 € en 266 mov. · **n/a** (no queda un plan verificable en la respuesta)
- Grupos: HOUSING_RENT 9.200,00 € · GROCERIES 4.219,38 € · RESTAURANTS 2.086,68 € · TRANSPORT_FUEL 1.447,21 € · SUBSCRIPTIONS 665,52 € · TRANSPORT_PUBLIC 659,38 € …
- Esperado: `intent` QUERY — Desglose de gastos del año por categoría.

**2.** «Ponlo en gráfico» → `PLANNED` · 20,0 s

> Aquí tienes el gráfico de los resultados de la consulta.

- Consulta: en lo que va de año, gastos, por categoría · Cifra: 19.306,70 € en 266 mov. · **n/a** (no queda un plan verificable en la respuesta)
- Sugerencias: «Ordena de menor a mayor» · «Ponlo en vertical»
- Esperado: `reference` VISUAL — Un gráfico de esas categorías, sin nueva lectura bancaria.

**3.** «Vertical» → `PLANNED` · 16,4 s

> He actualizado el gráfico con la misma consulta.

- Consulta: en lo que va de año, gastos, por categoría · Cifra: 19.306,70 € en 266 mov. · **n/a** (no queda un plan verificable en la respuesta)
- Sugerencias: «Ordena de menor a mayor» · «Ponlo en horizontal»
- Esperado: `follow_up` VISUAL — Las mismas barras en vertical; mismo total, sin nueva lectura bancaria.

**4.** «Ordena ascendente» → `PLANNED` · 29,5 s

> 19.306,70 € en lo que va de año, en 266 movimientos.
>   · alquiler: 9.200,00 € (10)
>   · supermercado: 4.219,38 € (76)
>   · restaurantes: 2.086,68 € (61)
>   · gasolina: 1.447,21 € (28)
>   · suscripciones: 665,52 € (34)
>   · transporte público: 659,38 € (31)
>   · bizum: 587,72 € (13)
>   · salud: 377,91 € (9)
>   · ocio: 62,90 € (4)
> Consultado en todas tus cuentas.
>
> Aquí tienes el gráfico de los resultados de la consulta.

- Consulta: en lo que va de año, gastos, por categoría · Cifra: 19.306,70 € en 266 mov. · **n/a** (no queda un plan verificable en la respuesta)
- Grupos: HOUSING_RENT 9.200,00 € · GROCERIES 4.219,38 € · RESTAURANTS 2.086,68 € · TRANSPORT_FUEL 1.447,21 € · SUBSCRIPTIONS 665,52 € · TRANSPORT_PUBLIC 659,38 € …
- Sugerencias: «Ordena de menor a mayor» · «Ponlo en vertical»
- Esperado: `follow_up` VISUAL — Barras verticales ordenadas de menor a mayor; mismo total.

**5.** «Solo las cinco mayores» → `PLANNED` · 36,2 s

> Tus 5 categorías con mayor importe total en lo que va de año son:
>   · alquiler: 9.200,00 € (10)
>   · supermercado: 4.219,38 € (76)
>   · restaurantes: 2.086,68 € (61)
>   · gasolina: 1.447,21 € (28)
>   · suscripciones: 665,52 € (34)
> Consultado en todas tus cuentas.
>
> Aquí tienes el gráfico de los resultados de la consulta.

- Consulta: en lo que va de año, gastos, por categoría, top 5 · Cifra: 17.618,79 € en 209 mov. · **n/a** (no queda un plan verificable en la respuesta)
- Grupos: HOUSING_RENT 9.200,00 € · GROCERIES 4.219,38 € · RESTAURANTS 2.086,68 € · TRANSPORT_FUEL 1.447,21 € · SUBSCRIPTIONS 665,52 €
- Sugerencias: «Ordena de menor a mayor» · «Ponlo en vertical»
- Esperado: `follow_up` VISUAL — Las cinco categorías mayores, verticales y ascendentes; el total de la consulta no cambia.

**6.** «Vuelve a mostrar todas» → `PLANNED` · 20,1 s

> 19.306,70 € en lo que va de año, en 266 movimientos.
>   · alquiler: 9.200,00 € (10)
>   · supermercado: 4.219,38 € (76)
>   · restaurantes: 2.086,68 € (61)
>   · gasolina: 1.447,21 € (28)
>   · suscripciones: 665,52 € (34)
>   · transporte público: 659,38 € (31)
>   · bizum: 587,72 € (13)
>   · salud: 377,91 € (9)
>   · ocio: 62,90 € (4)
> Consultado en todas tus cuentas.

- Consulta: en lo que va de año, gastos, por categoría · Cifra: 19.306,70 € en 266 mov. · **n/a** (no queda un plan verificable en la respuesta)
- Grupos: HOUSING_RENT 9.200,00 € · GROCERIES 4.219,38 € · RESTAURANTS 2.086,68 € · TRANSPORT_FUEL 1.447,21 € · SUBSCRIPTIONS 665,52 € · TRANSPORT_PUBLIC 659,38 € …
- Esperado: `follow_up` VISUAL — Todas las categorías otra vez, verticales y ascendentes.

| Turno | Esperado | Cifras | intent | context | pending | clarification | recovery | naturalness 1-5 | mechanism | notes | s |
|---|---|---|---|---|---|---|---|---|---|---|---:|
| 1 | intent | n/a |  |  |  |  |  |  |  |  | 13,4 s |
| 2 | reference | n/a |  |  |  |  |  |  |  |  | 20,0 s |
| 3 | follow_up | n/a |  |  |  |  |  |  |  |  | 16,4 s |
| 4 | follow_up | n/a |  |  |  |  |  |  |  |  | 29,5 s |
| 5 | follow_up | n/a |  |  |  |  |  |  |  |  | 36,2 s |
| 6 | follow_up | n/a |  |  |  |  |  |  |  |  | 20,1 s |

## bizum-interrumpido-y-reanudado

Objetivo: Empezar un Bizum, interrumpirlo con una consulta de saldo, retomarlo y cancelarlo en la vista previa. · canal: API

**1.** «Quiero hacerle un bizum a Marta Solis Vega» → `NEEDS_CLARIFICATION` · 23,1 s

> ¿De cuánto quieres hacerle el bizum a Marta Solis Vega?

- **n/a** (NEEDS_CLARIFICATION: no es la cifra de una consulta)
- Esperado: `intent` PAYMENT — Pide solo el importe; destinatario Marta Solis Vega.

**2.** «Espera, ¿cuánto tengo en la cuenta corriente?» → `BALANCE` · 9,6 s

> Tienes 595,56 € en Cuenta corriente.

- Consulta: saldo de Cuenta corriente · Cifra: 595,56 € · **n/a** (BALANCE: no es la cifra de una consulta)
- Esperado: `interruption` BALANCE — Saldo de la cuenta corriente, sin crear ni perder el borrador del Bizum.

**3.** «Vale, sigamos con el bizum, que sean 15 euros» → `AWAITING_CONFIRMATION` · 10,9 s

> Vas a enviar 15,00 € a Marta Solis Vega (+34600333444). Te quedarían 580,56 €. ¿Confirmas? Responde sí o no.

- **n/a** (AWAITING_CONFIRMATION: no es la cifra de una consulta)
- Estado: workflow PAYMENT_CONFIRMATION, awaiting CONFIRMATION
- Esperado: `pending_resolution` PAYMENT — Vista previa de 15 € a Marta Solis Vega con botones de confirmación; no envía dinero.

**4.** «No, mejor no» → `CANCELLED` · 0,1 s

> Vale, no he enviado nada.

- **n/a** (CANCELLED: no es la cifra de una consulta)
- Esperado: `reset` — Cancela la vista previa; no se envía nada.

| Turno | Esperado | Cifras | intent | context | pending | clarification | recovery | naturalness 1-5 | mechanism | notes | s |
|---|---|---|---|---|---|---|---|---|---|---|---:|
| 1 | intent | n/a |  |  |  |  |  |  |  |  | 23,1 s |
| 2 | interruption | n/a |  |  |  |  |  |  |  |  | 9,6 s |
| 3 | pending_resolution | n/a |  |  |  |  |  |  |  |  | 10,9 s |
| 4 | reset | n/a |  |  |  |  |  |  |  |  | 0,1 s |
