# Conversaciones reales — 2026-10-11, `a6eafc40`

**Preflight** (2026-10-11T12:51:31+02:00)

- Servicios: orchestrator ok · banking ok · gateway skipped (not used by this evaluation)
- Orquestador: `a6eafc40` (task/legacy-removal) · HEAD `a6eafc40` · source `1f04c049701ce9a8fdf459336cc1f61ca8040aa374731534ee4816ea61d29ced` · código del producto: igual que el código local
- Banking API: `a6eafc40` (task/legacy-removal) · source `9cd9a8ca27c731603fb5eb407e13930e2e9bef6f38e9e82d577ba384169e9f43`
- Libro: último movimiento 2026-10-07 (hoy 2026-10-11) · antigüedad aceptada a mano: 4 días
- GPU: P8, 180 MHz · Ollama en memoria: qwen2.5-coder:7b

## Resumen

Sin ejecución anterior con la que comparar.

| Mecanismo | Turnos | Fallidos | Sin juzgar | Acierto | Antes |
|---|---:|---:|---:|---:|---:|
| comparison | 1 | 1 | 0 | 0 % | — |
| follow_up | 2 | 0 | 1 | 100 % | — |
| intent | 2 | 0 | 0 | 100 % | — |
| interruption | 2 | 1 | 0 | 50 % | — |
| reference | 2 | 0 | 0 | 100 % | — |

### Gestiones de las sesiones

| Gestión | Turnos | Estado | Fallidos | Sin juzgar | Invariantes |
|---|---:|---|---:|---:|---|
| sesion-bancaria-grafica-de-pagina-y-bizum-nombrado / bizum-cancelacion | 0 | pendiente | 0 | 2 | — |
| sesion-bancaria-grafica-de-pagina-y-bizum-nombrado / capacidad-bizum | 1 | fallida | 1 | 0 | — |
| sesion-bancaria-grafica-de-pagina-y-bizum-nombrado / movimientos-paginacion | 4 | pendiente | 0 | 1 | unverified; passed |
| sesion-bancaria-grafica-de-pagina-y-bizum-nombrado / restaurantes-comparacion-visualizacion | 3 | fallida | 2 | 0 | — |
| sesion-bancaria-grafica-de-pagina-y-bizum-nombrado / saldos | 1 | completa | 0 | 0 | — |

- Recuperación (tras un turno fallido, ¿el siguiente va bien?): 1 de 1 (100 %)
- Fallos de disponibilidad (también fallan la sesión): provider 1 · banking 0 · upstream_http 0 · transport 0
- Provider failure attempts (safe category): gemini:transport 1
- Terminal provider-call failures: gemini:transport 1
- Turnos intentados / omitidos / previstos: 9 / 2 / 11
- Cifras contra el libro: 1 match · 0 mismatch · 0 sin poder comprobar (error) · 8 n/a
- Latencia por turno: mediana 16,7 s · p95 23,2 s · máx 23,2 s (9 turnos)
  - motor `model`: mediana 16,7 s · p95 23,2 s · máx 23,2 s (9 turnos)
- Conversaciones logradas (todos los turnos bien y objetivo cumplido): 0 de 1 juzgadas (0 %) · 0 sin juzgar
- Llamadas al modelo por turno: media 1,44 · p95 2 (9 turnos con dato)
- Peticiones HTTP reales al proveedor (incluye reintentos/fallback): 11 en 9 turnos · media 1,22 · p95 2
- Modelos intentados/usados: `gemini-3.5-flash-lite`: 11 intentos, 10 respuestas
- Tokens informados por proveedor: 39521 total en 8 turnos (38840 entrada, 681 salida; 10 respuestas con usage)

## sesion-bancaria-grafica-de-pagina-y-bizum-nombrado

Objetivo: Completar varias gestiones en una conversación: consultar saldos, listar movimientos y paginar sin duplicados, graficar la página de movimientos ya mostrada, cambiar de tema, comparar restaurantes, visualizar el resultado y cancelar un Bizum antes de ejecutarlo. · canal: API

**1.** «¿Me dices cómo están mis cuentas?» → `BALANCE` · 8,3 s

> Tienes 7.019,56 € entre tus cuentas: 614,56 € en Cuenta corriente y 6.405,00 € en Hucha. Aparte, tienes 3.150,00 € en Fondo indexado, que es lo que invertiste y no lo que vale hoy.

- Consulta: saldo de tus cuentas · Cifra: 7.019,56 € · **n/a** (BALANCE: no es la cifra de una consulta)
- Esperado: `intent` BALANCE — Saldo actual por cuenta y total basado únicamente en la banca; no confunde saldo de inversión con valoración actual.

**2.** «Enséñame los últimos 20 gastos de la corriente.» → `PLANNED` · 9,7 s

> Estos son tus últimos 20 movimientos. Dime "dame otros 20" para ver los siguientes.
> Consultado en Cuenta corriente.

- Consulta: gastos recientes · **n/a** (no queda un plan verificable en la respuesta)
- Esperado: `intent` QUERY — Una lista ordenada de hasta 20 movimientos de gasto de Cuenta corriente, con fechas, comercios e importes; no solicita periodo.

**3.** «Dame otros 10.» → `NEEDS_CLARIFICATION` · 20,4 s

> ¿Qué quieres consultar? Dime qué gastos o ingresos y de qué periodo.

- **n/a** (NEEDS_CLARIFICATION: no es la cifra de una consulta) · Invariante de sesión: **unverified** (no hay filas identificables en ambas páginas para comprobar duplicados)
- Esperado: `follow_up` QUERY — Devuelve la página siguiente de hasta 10 gastos de Cuenta corriente, ordenada igual y sin repetir ninguna fila de la página anterior.

**4.** «¿Me los enseñas en una gráfica?» → `PLANNED` · 19,6 s

> Aquí tienes un gráfico de los movimientos de esta página.

- Consulta: Importe por categoría en esta página (20 movimientos) · Cifra: 20 mov. · **n/a** (no queda un plan verificable en la respuesta) · Reutilizacion del resultado: **passed** (reutilizo la pagina anterior sin repetir lecturas financieras; lecturas financieras: 0)
- Sugerencias: «Ordena de menor a mayor» · «Ponlo en vertical»
- Esperado: `reference` VISUAL — Dibuja los movimientos de la página que acaba de mostrar, dejando claro que no representa el total de una consulta mayor y reutilizando el resultado ya obtenido.

**5.** «¿Y los de la Hucha?» → `PLANNED` · 16,7 s

> No he encontrado movimientos recientes en tus cuentas.
> Consultado en Hucha.

- Consulta: gastos recientes · **n/a** (no queda un plan verificable en la respuesta)
- Esperado: `follow_up` QUERY — Conserva el tipo de lista y la dirección de gastos, cambia solo la cuenta a Hucha y muestra que no hay gastos en esa cuenta; no convierte traspasos internos ni intereses en gastos.

**6.** «¿Cuánto gasté en restaurantes el mes pasado?» → `PLANNED` · 13,2 s

> 187,94 € en restaurantes el mes pasado, en 6 movimientos.
> Consultado en todas tus cuentas.

- Consulta: el mes pasado, gastos, restaurantes · Cifra: 187,94 € en 6 mov. · SQL: 187,94 € en 6 mov. · Consulta comprobada: `{"categories": ["RESTAURANTS"], "direction": "EXPENSE", "end": null, "group_by": "NONE", "group_limit": null, "merchant_terms": [], "merchants": [], "metric": "SUM", "n": null, "period": "LAST_MONTH", "rank": null, "start": null}` · **match** (comprobada)
- Grupos: total 187,94 €
- Esperado: `interruption` QUERY — Inicia una consulta independiente de restaurantes para el mes pasado en todas las cuentas; no hereda Hucha ni el límite de movimientos.

**7.** «Compáralo con agosto.» → `PLANNED` · 23,2 s

> En agosto de 2026: 2.159,87 €; en septiembre de 2026: 2.097,36 €. El gasto fue 62,51 € mayor en agosto de 2026.
> Consultado en todas tus cuentas.

- Consulta: del 2026-08-01 al 2026-08-31, gastos frente a del 2026-09-01 al 2026-09-30, gastos · Cifra: 65 mov. · **n/a** (una comparación: la juzga la rúbrica)
- Grupos: en agosto de 2026 2.159,87 € · en septiembre de 2026 2.097,36 €
- Esperado: `comparison` QUERY — Compara el mismo gasto en restaurantes entre el mes pasado y agosto, con ambos periodos explícitos y resultados bancarios.

**8.** «Ponme una gráfica.» → `PLANNED` · 19,4 s

> Aquí tienes la comparación en un gráfico: del 2026-08-01 al 2026-08-31, gastos frente a del 2026-09-01 al 2026-09-30, gastos, por resultado.

- Consulta: del 2026-08-01 al 2026-08-31, gastos frente a del 2026-09-01 al 2026-09-30, gastos, por resultado · Cifra: 2.097,36 € en 32 mov. · **n/a** (plan_context incompleto: faltan filtros o alcance)
- Sugerencias: «Ordena de menor a mayor» · «Ponlo en vertical»
- Esperado: `reference` VISUAL — Dibuja la comparación anterior usando sus resultados verificados, sin una nueva lectura bancaria ni un desglose distinto.

**9.** «¿Puedes hacer Bizums?» → `PROVIDER_ERROR` · 0,2 s

> No he podido leer tu mensaje ahora mismo. Vuelve a intentarlo.

- **n/a** (PROVIDER_ERROR: no es la cifra de una consulta)
- Esperado: `interruption` — Explica la capacidad sin crear un borrador de pago ni iniciar una confirmación.

**10.** «Vale, quiero enviar 15 euros a Marta Ruiz Gomez.» — no enviado: replay detenido tras indisponibilidad del proveedor en sesion-bancaria-grafica-de-pagina-y-bizum-nombrado turno 9; no es un fallo semántico evaluado

**11.** «No, mejor déjalo.» — no enviado: replay detenido tras indisponibilidad del proveedor en sesion-bancaria-grafica-de-pagina-y-bizum-nombrado turno 9; no es un fallo semántico evaluado

| Turno | Esperado | Cifras | intent | context | pending | clarification | recovery | naturalness 1-5 | mechanism | notes | s |
|---|---|---|---|---|---|---|---|---|---|---|---:|
| 1 | intent | n/a |  |  |  |  |  |  | intent |  | 8,3 s |
| 2 | intent | n/a |  |  |  |  |  |  | intent |  | 9,7 s |
| 3 | follow_up | n/a |  |  |  |  | fail |  | follow_up | «Dame otros 10»: dos respuestas del modelo que el dominio rechaza acaban en «¿Qué quieres consultar?», un fallo técnico presentado como aclaración genérica. | 20,4 s |
| 4 | reference | n/a |  |  |  |  |  |  | reference |  | 19,6 s |
| 5 | follow_up | n/a |  |  |  |  |  |  | follow_up |  | 16,7 s |
| 6 | interruption | match |  |  |  |  |  |  | interruption |  | 13,2 s |
| 7 | comparison | n/a |  | fail |  |  |  |  | comparison | «Compáralo con agosto» tras restaurantes del mes pasado compara el gasto total agosto/septiembre: pierde el filtro de restaurantes. | 23,2 s |
| 8 | reference | n/a |  |  |  |  |  |  | reference |  | 19,4 s |
| 9 | interruption | n/a |  |  |  |  |  |  |  |  | 0,2 s |
| 10 | intent | skipped |  |  |  |  |  |  |  |  | — |
| 11 | reset | skipped |  |  |  |  |  |  |  |  | — |
