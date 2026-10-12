# Conversaciones reales — 2026-10-11, `bd52f69b`

**Preflight** (2026-10-11T23:32:45+02:00)

- Servicios: orchestrator ok · banking ok · gateway skipped (not used by this evaluation)
- Orquestador: `bd52f69b` (task/legacy-removal) · HEAD `bd52f69b` · source `1b770438bbd0cbeb50326545ceb81e965d6090fc8c78b224a0eb1a8f34180fd5` · código del producto: igual que el código local
- Banking API: `bd52f69b` (task/legacy-removal) · source `ca81ab863f670021662c0e93710024262c90c95228a4e61ad03e25f99c62212a`
- Libro: último movimiento 2026-10-11 (hoy 2026-10-11) · antigüedad aceptada a mano: 4 días
- GPU: P8, 22 MHz · Ollama en memoria: qwen2.5-coder:7b

## Resumen

Sin ejecución anterior con la que comparar.

| Mecanismo | Turnos | Fallidos | Sin juzgar | Acierto | Antes |
|---|---:|---:|---:|---:|---:|
| follow_up | 1 | 1 | 0 | 0 % | — |
| intent | 2 | 0 | 2 | — | — |

### Gestiones de las sesiones

| Gestión | Turnos | Estado | Fallidos | Sin juzgar | Invariantes |
|---|---:|---|---:|---:|---|
| sesion-bancaria-grafica-de-pagina-y-bizum-nombrado / bizum-cancelacion | 0 | pendiente | 0 | 2 | — |
| sesion-bancaria-grafica-de-pagina-y-bizum-nombrado / capacidad-bizum | 0 | pendiente | 0 | 1 | — |
| sesion-bancaria-grafica-de-pagina-y-bizum-nombrado / movimientos-paginacion | 2 | fallida | 1 | 3 | unverified |
| sesion-bancaria-grafica-de-pagina-y-bizum-nombrado / restaurantes-comparacion-visualizacion | 0 | pendiente | 0 | 3 | — |
| sesion-bancaria-grafica-de-pagina-y-bizum-nombrado / saldos | 1 | pendiente | 0 | 1 | — |

- Recuperación (tras un turno fallido, ¿el siguiente va bien?): 0 de 0 (—)
- Fallos de disponibilidad (también fallan la sesión): provider 1 · banking 0 · upstream_http 0 · transport 0
- Provider failure attempts (safe category): ninguno registrado
- Terminal provider-call failures: none recorded
- Turnos intentados / omitidos / previstos: 3 / 8 / 11
- Cifras contra el libro: 0 match · 0 mismatch · 0 sin poder comprobar (error) · 3 n/a
- Latencia por turno: mediana 11,3 s · p95 11,8 s · máx 11,8 s (3 turnos)
  - motor `model`: mediana 11,3 s · p95 11,8 s · máx 11,8 s (3 turnos)
- Conversaciones logradas (todos los turnos bien y objetivo cumplido): 0 de 1 juzgadas (0 %) · 0 sin juzgar
- Llamadas al modelo por turno: media 1,33 · p95 2 (3 turnos con dato)
- Peticiones HTTP reales al proveedor (incluye reintentos/fallback): 4 en 3 turnos · media 1,33 · p95 2
- Modelos intentados/usados: `gemini-3.5-flash-lite`: 4 intentos, 4 respuestas
- Tokens informados por proveedor: 15995 total en 3 turnos (15767 entrada, 228 salida; 4 respuestas con usage)

## sesion-bancaria-grafica-de-pagina-y-bizum-nombrado

Objetivo: Completar varias gestiones en una conversación: consultar saldos, listar movimientos y paginar sin duplicados, graficar la página de movimientos ya mostrada, cambiar de tema, comparar restaurantes, visualizar el resultado y cancelar un Bizum antes de ejecutarlo. · canal: API

**1.** «¿Me dices cómo están mis cuentas?» → `BALANCE` · 11,8 s

> Tienes 7.019,56 € entre tus cuentas: 595,56 € en Cuenta corriente y 6.424,00 € en Hucha. Aparte, tienes 3.150,00 € en Fondo indexado, que es lo que invertiste y no lo que vale hoy.

- Consulta: saldo de tus cuentas · Cifra: 7.019,56 € · **n/a** (BALANCE: no es la cifra de una consulta)
- Esperado: `intent` BALANCE — Saldo actual por cuenta y total basado únicamente en la banca; no confunde saldo de inversión con valoración actual.

**2.** «Enséñame los últimos 20 gastos de la corriente.» → `PLANNED` · 9,3 s

> Estos son tus últimos 20 movimientos. Dime "dame otros 20" para ver los siguientes.
> Consultado en Cuenta corriente.

- Consulta: gastos recientes · **n/a** (no queda un plan verificable en la respuesta)
- Esperado: `intent` QUERY — Una lista ordenada de hasta 20 movimientos de gasto de Cuenta corriente, con fechas, comercios e importes; no solicita periodo.

**3.** «Dame otros 10.» → `PROVIDER_ERROR` · 11,3 s

> No he podido leer tu mensaje ahora mismo. Vuelve a intentarlo.

- **n/a** (PROVIDER_ERROR: no es la cifra de una consulta) · Invariante de sesión: **unverified** (no hay filas identificables en ambas páginas para comprobar duplicados)
- Esperado: `follow_up` QUERY — Devuelve la página siguiente de hasta 10 gastos de Cuenta corriente, ordenada igual y sin repetir ninguna fila de la página anterior.

**4.** «¿Me los enseñas en una gráfica?» — no enviado: replay detenido tras indisponibilidad del proveedor en sesion-bancaria-grafica-de-pagina-y-bizum-nombrado turno 3; no es un fallo semántico evaluado

**5.** «¿Y los de la Hucha?» — no enviado: replay detenido tras indisponibilidad del proveedor en sesion-bancaria-grafica-de-pagina-y-bizum-nombrado turno 3; no es un fallo semántico evaluado

**6.** «¿Cuánto gasté en restaurantes el mes pasado?» — no enviado: replay detenido tras indisponibilidad del proveedor en sesion-bancaria-grafica-de-pagina-y-bizum-nombrado turno 3; no es un fallo semántico evaluado

**7.** «Compáralo con agosto.» — no enviado: replay detenido tras indisponibilidad del proveedor en sesion-bancaria-grafica-de-pagina-y-bizum-nombrado turno 3; no es un fallo semántico evaluado

**8.** «Ponme una gráfica.» — no enviado: replay detenido tras indisponibilidad del proveedor en sesion-bancaria-grafica-de-pagina-y-bizum-nombrado turno 3; no es un fallo semántico evaluado

**9.** «¿Puedes hacer Bizums?» — no enviado: replay detenido tras indisponibilidad del proveedor en sesion-bancaria-grafica-de-pagina-y-bizum-nombrado turno 3; no es un fallo semántico evaluado

**10.** «Vale, quiero enviar 15 euros a Marta Ruiz Gomez.» — no enviado: replay detenido tras indisponibilidad del proveedor en sesion-bancaria-grafica-de-pagina-y-bizum-nombrado turno 3; no es un fallo semántico evaluado

**11.** «No, mejor déjalo.» — no enviado: replay detenido tras indisponibilidad del proveedor en sesion-bancaria-grafica-de-pagina-y-bizum-nombrado turno 3; no es un fallo semántico evaluado

| Turno | Esperado | Cifras | intent | context | pending | clarification | recovery | naturalness 1-5 | mechanism | notes | s |
|---|---|---|---|---|---|---|---|---|---|---|---:|
| 1 | intent | n/a |  |  |  |  |  |  |  |  | 11,8 s |
| 2 | intent | n/a |  |  |  |  |  |  |  |  | 9,3 s |
| 3 | follow_up | n/a |  |  |  |  |  |  |  |  | 11,3 s |
| 4 | reference | skipped |  |  |  |  |  |  |  |  | — |
| 5 | follow_up | skipped |  |  |  |  |  |  |  |  | — |
| 6 | interruption | skipped |  |  |  |  |  |  |  |  | — |
| 7 | comparison | skipped |  |  |  |  |  |  |  |  | — |
| 8 | reference | skipped |  |  |  |  |  |  |  |  | — |
| 9 | interruption | skipped |  |  |  |  |  |  |  |  | — |
| 10 | intent | skipped |  |  |  |  |  |  |  |  | — |
| 11 | reset | skipped |  |  |  |  |  |  |  |  | — |
