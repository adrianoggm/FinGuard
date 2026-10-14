# Conversaciones reales — 2026-10-10, `unknown`

**Preflight** (2026-10-10T17:49:36+02:00)

- Servicios: orchestrator ok · banking ok · gateway skipped (not used by this evaluation)
- Orquestador: `unknown` (unknown) · HEAD `d0252493` · código del producto: desconocido
- Libro: último movimiento 2026-10-07 (hoy 2026-10-10) · antigüedad aceptada a mano: 3 días
- GPU: P8, 22 MHz · Ollama en memoria: ninguno

## Hallazgos

- Partial execution: 4 of 60 turns. All four returned PROVIDER_ERROR with Gemini 3.8 Flash although this build enforced at least five seconds between provider calls (theoretical maximum 12 RPM).
- Replay stopped after repeated 429 responses. This is not a continuity evaluation and does not identify whether the exhausted limit was RPM; the provider supplied no quota identifier.
- Read-only ledger with 3-day age explicitly accepted; no bank writes and no payment confirmations.

## Resumen

Sin ejecución anterior con la que comparar.

| Mecanismo | Turnos | Fallidos | Sin juzgar | Acierto | Antes |
|---|---:|---:|---:|---:|---:|
| follow_up | 1 | 1 | 0 | 0 % | — |
| intent | 1 | 1 | 0 | 0 % | — |
| slot_carryover | 2 | 2 | 0 | 0 % | — |

- Recuperación (tras un turno fallido, ¿el siguiente va bien?): 0 de 3 (0 %)
- Cifras contra el libro: 0 match · 0 mismatch · 0 sin poder comprobar (error) · 4 n/a
- Latencia por turno: mediana 0,9 s · p95 1,4 s · máx 1,4 s (4 turnos)
  - motor `model`: mediana 0,9 s · p95 1,4 s · máx 1,4 s (4 turnos)
- Conversaciones logradas (todos los turnos bien y objetivo cumplido): 0 de 1 juzgadas (0 %) · 0 sin juzgar
- Llamadas al modelo por turno: media 1 · p95 1 (4 turnos con dato)

## sesion-1-consultas-y-correcciones

Objetivo: Mantener consultas y filtros a trav?s de cambios de tema y correcciones. · canal: API

**1.** «q llevo gastado en suscripciones estos ultimos 9 meses» → `PROVIDER_ERROR` · 0,4 s

> No he podido leer tu mensaje ahora mismo. Vuelve a intentarlo.

- **n/a** (PROVIDER_ERROR: no es la cifra de una consulta)
- Esperado: `intent` QUERY — En el corpus UX: {ran: {categories: [SUBSCRIPTIONS], window: 'LAST_N_MONTHS:9'}}

**2.** «solo Netflix» → `PROVIDER_ERROR` · 0,8 s

> No he podido leer tu mensaje ahora mismo. Vuelve a intentarlo.

- **n/a** (PROVIDER_ERROR: no es la cifra de una consulta)
- Esperado: `slot_carryover` QUERY — En el corpus UX: {ran: {merchants: [Netflix], window: 'LAST_N_MONTHS:9'}}

**3.** «y este mes?» → `PROVIDER_ERROR` · 1,1 s

> No he podido leer tu mensaje ahora mismo. Vuelve a intentarlo.

- **n/a** (PROVIDER_ERROR: no es la cifra de una consulta)
- Esperado: `follow_up` QUERY — En el corpus UX: {ran: {merchants: [Netflix], window: THIS_MONTH}}

**4.** «no, Spotify» → `PROVIDER_ERROR` · 1,4 s

> No he podido leer tu mensaje ahora mismo. Vuelve a intentarlo.

- **n/a** (PROVIDER_ERROR: no es la cifra de una consulta)
- Esperado: `slot_carryover` QUERY — En el corpus UX: {ran: {merchants: [Spotify], window: THIS_MONTH}}

| Turno | Esperado | Cifras | intent | context | pending | clarification | recovery | naturalness 1-5 | mechanism | notes | s |
|---|---|---|---|---|---|---|---|---|---|---|---:|
| 1 | intent | n/a | fallo t?cnico del turno | no evaluable por indisponibilidad del proveedor | no evaluable | no evaluable | fallo |  | intent | Gemini devolvi? PROVIDER_ERROR pese al intervalo de 5 s por llamada; se detuvo la repetici?n tras 4 fallos consecutivos para no seguir gastando cuota. | 0,4 s |
| 2 | slot_carryover | n/a | fallo t?cnico del turno | no evaluable por indisponibilidad del proveedor | no evaluable | no evaluable | fallo |  | slot_carryover | Gemini devolvi? PROVIDER_ERROR pese al intervalo de 5 s por llamada; se detuvo la repetici?n tras 4 fallos consecutivos para no seguir gastando cuota. | 0,8 s |
| 3 | follow_up | n/a | fallo t?cnico del turno | no evaluable por indisponibilidad del proveedor | no evaluable | no evaluable | fallo |  | follow_up | Gemini devolvi? PROVIDER_ERROR pese al intervalo de 5 s por llamada; se detuvo la repetici?n tras 4 fallos consecutivos para no seguir gastando cuota. | 1,1 s |
| 4 | slot_carryover | n/a | fallo t?cnico del turno | no evaluable por indisponibilidad del proveedor | no evaluable | no evaluable | fallo |  | slot_carryover | Gemini devolvi? PROVIDER_ERROR pese al intervalo de 5 s por llamada; se detuvo la repetici?n tras 4 fallos consecutivos para no seguir gastando cuota. | 1,4 s |
