# Conversaciones reales — 2026-10-09, `task/langgraph-experiment`

**Preflight** (2026-10-09T13:42:42+02:00)

- Servicios: orchestrator ok · banking ok · gateway ok
- Orquestador: `task/langgraph-experiment` (task/langgraph-experiment) · HEAD `aaa96c01` · código del producto: igual que HEAD
- Libro: último movimiento 2026-10-07 (hoy 2026-10-09) · antigüedad aceptada a mano: 3 días
- GPU: P5, 210 MHz · Ollama en memoria: ninguno

## Resumen

Sin ejecución anterior con la que comparar.

| Mecanismo | Turnos | Fallidos | Sin juzgar | Acierto | Antes |
|---|---:|---:|---:|---:|---:|
| comparison | 1 | 0 | 1 | — | — |
| investigation | 3 | 0 | 3 | — | — |
| smalltalk | 1 | 0 | 1 | — | — |

- Recuperación (tras un turno fallido, ¿el siguiente va bien?): 0 de 0 (—)
- Cifras contra el libro: 0 match · 0 mismatch · 0 sin poder comprobar (error) · 5 n/a
- Latencia por turno: mediana 0,4 s · p95 5,0 s · máx 5,0 s (5 turnos)
  - motor `model`: mediana 0,4 s · p95 5,0 s · máx 5,0 s (5 turnos)
- Conversaciones logradas (todos los turnos bien y objetivo cumplido): 0 de 0 juzgadas (—) · 2 sin juzgar
- Llamadas al modelo por turno: media 1 · p95 1 (5 turnos con dato)

## comercios-crecen-mes

Objetivo: Saber en qué comercios gasta más este mes que el anterior y por qué. · canal: API

**1.** «¿En qué comercios gasto más que antes y se debe a más compras o a importes mayores?» → `NEEDS_CLARIFICATION` · 1,6 s

> ¿De qué periodo quieres verlo?

- **n/a** (NEEDS_CLARIFICATION: no es la cifra de una consulta)
- Esperado: `investigation` QUERY — Asume una ventana razonable y la dice (p. ej. el mes pasado frente al anterior, o los últimos 30 días frente a los 30 previos). Nombra los comercios cuyo gasto subió, con el gasto de cada ventana, y para cada uno dice si subió por más compras (más movimientos), por importes mayores (media por movimiento más alta) o por ambas. No inventa comercios ni cifras; no explica el motivo de fondo.

**2.** «¿y en cuál ha sido por una compra puntual grande?» → `NEEDS_CLARIFICATION` · 5,0 s

> ¿Qué quieres consultar? Dime qué gastos o ingresos y de qué periodo.

- **n/a** (NEEDS_CLARIFICATION: no es la cifra de una consulta)
- Esperado: `investigation` QUERY — Mira los movimientos de los comercios que subieron por importe y dice si la subida se concentra en una sola compra grande (con su fecha e importe, sacados del banco) o no. Si ningún comercio lo cumple, lo dice. Misma ventana que el turno anterior.

| Turno | Esperado | Cifras | intent | context | pending | clarification | recovery | naturalness 1-5 | mechanism | notes | s |
|---|---|---|---|---|---|---|---|---|---|---|---:|
| 1 | investigation | n/a |  |  |  |  |  |  |  |  | 1,6 s |
| 2 | investigation | n/a |  |  |  |  |  |  |  |  | 5,0 s |

## supermercados-trimestre

Objetivo: Entender si gasta más en supermercados por ir más veces o por gastar más cada vez. · canal: API

**1.** «Compara mis compras en supermercados de este trimestre con el anterior» → `PROVIDER_ERROR` · 0,4 s

> No he podido leer tu mensaje ahora mismo. Vuelve a intentarlo.

- **n/a** (PROVIDER_ERROR: no es la cifra de una consulta)
- Esperado: `comparison` QUERY — Supermercados, este trimestre frente al anterior, diciendo ambas ventanas y el gasto de cada una.

**2.** «¿qué comercios explican la diferencia? ¿voy más veces o gasto más por visita?» → `PROVIDER_ERROR` · 0,2 s

> No he podido leer tu mensaje ahora mismo. Vuelve a intentarlo.

- **n/a** (PROVIDER_ERROR: no es la cifra de una consulta)
- Esperado: `investigation` QUERY — Conserva supermercados y las dos ventanas. Por comercio: número de compras y media por compra en cada trimestre, y si la diferencia viene de ir más veces, de gastar más por visita o de ambas. Un comercio que no aparecía en el trimestre anterior se dice como nuevo, sin atribuirle una causa.

**3.** «gracias» → `PROVIDER_ERROR` · 0,2 s

> No he podido leer tu mensaje ahora mismo. Vuelve a intentarlo.

- **n/a** (PROVIDER_ERROR: no es la cifra de una consulta)
- Esperado: `smalltalk` — Un agradecimiento breve, sin cifras y sin repetir la respuesta.

| Turno | Esperado | Cifras | intent | context | pending | clarification | recovery | naturalness 1-5 | mechanism | notes | s |
|---|---|---|---|---|---|---|---|---|---|---|---:|
| 1 | comparison | n/a |  |  |  |  |  |  |  |  | 0,4 s |
| 2 | investigation | n/a |  |  |  |  |  |  |  |  | 0,2 s |
| 3 | smalltalk | n/a |  |  |  |  |  |  |  |  | 0,2 s |
