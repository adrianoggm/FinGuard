# Conversaciones reales — 2026-10-09, `task/langgraph-experiment`

**Preflight** (2026-10-09T15:25:02+02:00)

- Servicios: orchestrator ok · banking ok · gateway ok
- Orquestador: `task/langgraph-experiment` (task/langgraph-experiment) · HEAD `69ed4f5c` · código del producto: igual que HEAD
- Libro: último movimiento 2026-10-07 (hoy 2026-10-09) · antigüedad aceptada a mano: 3 días
- GPU: P8, 22 MHz · Ollama en memoria: ninguno

## Resumen

Sin ejecución anterior con la que comparar.

| Mecanismo | Turnos | Fallidos | Sin juzgar | Acierto | Antes |
|---|---:|---:|---:|---:|---:|
| comparison | 1 | 0 | 1 | — | — |
| follow_up | 1 | 0 | 1 | — | — |
| intent | 1 | 0 | 1 | — | — |
| investigation | 5 | 0 | 5 | — | — |
| smalltalk | 1 | 0 | 1 | — | — |
| unknown_cause | 1 | 0 | 1 | — | — |

- Recuperación (tras un turno fallido, ¿el siguiente va bien?): 0 de 0 (—)
- Cifras contra el libro: 0 match · 0 mismatch · 0 sin poder comprobar (error) · 10 n/a
- Latencia por turno: mediana 0,3 s · p95 20,6 s · máx 20,6 s (10 turnos)
  - motor `model`: mediana 0,3 s · p95 20,6 s · máx 20,6 s (10 turnos)
- Conversaciones logradas (todos los turnos bien y objetivo cumplido): 0 de 0 juzgadas (—) · 4 sin juzgar
- Llamadas al modelo por turno: media 1 · p95 1 (10 turnos con dato)

## comercios-crecen-mes

Objetivo: Saber en qué comercios gasta más este mes que el anterior y por qué. · canal: API

**1.** «¿En qué comercios gasto más que antes y se debe a más compras o a importes mayores?» → `PROVIDER_ERROR` · 0,5 s

> No he podido leer tu mensaje ahora mismo. Vuelve a intentarlo.

- **n/a** (PROVIDER_ERROR: no es la cifra de una consulta)
- Esperado: `investigation` QUERY — Asume una ventana razonable y la dice (p. ej. el mes pasado frente al anterior, o los últimos 30 días frente a los 30 previos). Nombra los comercios cuyo gasto subió, con el gasto de cada ventana, y para cada uno dice si subió por más compras (más movimientos), por importes mayores (media por movimiento más alta) o por ambas. No inventa comercios ni cifras; no explica el motivo de fondo.

**2.** «¿y en cuál ha sido por una compra puntual grande?» → `PROVIDER_ERROR` · 0,3 s

> No he podido leer tu mensaje ahora mismo. Vuelve a intentarlo.

- **n/a** (PROVIDER_ERROR: no es la cifra de una consulta)
- Esperado: `investigation` QUERY — Mira los movimientos de los comercios que subieron por importe y dice si la subida se concentra en una sola compra grande (con su fecha e importe, sacados del banco) o no. Si ningún comercio lo cumple, lo dice. Misma ventana que el turno anterior.

| Turno | Esperado | Cifras | intent | context | pending | clarification | recovery | naturalness 1-5 | mechanism | notes | s |
|---|---|---|---|---|---|---|---|---|---|---|---:|
| 1 | investigation | n/a |  |  |  |  |  |  |  |  | 0,5 s |
| 2 | investigation | n/a |  |  |  |  |  |  |  |  | 0,3 s |

## supermercados-trimestre

Objetivo: Entender si gasta más en supermercados por ir más veces o por gastar más cada vez. · canal: API

**1.** «Compara mis compras en supermercados de este trimestre con el anterior» → `PROVIDER_ERROR` · 0,3 s

> No he podido leer tu mensaje ahora mismo. Vuelve a intentarlo.

- **n/a** (PROVIDER_ERROR: no es la cifra de una consulta)
- Esperado: `comparison` QUERY — Supermercados, este trimestre frente al anterior, diciendo ambas ventanas y el gasto de cada una.

**2.** «¿qué comercios explican la diferencia? ¿voy más veces o gasto más por visita?» → `PROVIDER_ERROR` · 0,2 s

> No he podido leer tu mensaje ahora mismo. Vuelve a intentarlo.

- **n/a** (PROVIDER_ERROR: no es la cifra de una consulta)
- Esperado: `investigation` QUERY — Conserva supermercados y las dos ventanas. Por comercio: número de compras y media por compra en cada trimestre, y si la diferencia viene de ir más veces, de gastar más por visita o de ambas. Un comercio que no aparecía en el trimestre anterior se dice como nuevo, sin atribuirle una causa.

**3.** «gracias» → `PROVIDER_ERROR` · 20,6 s

> No he podido leer tu mensaje ahora mismo. Vuelve a intentarlo.

- **n/a** (PROVIDER_ERROR: no es la cifra de una consulta)
- Esperado: `smalltalk` — Un agradecimiento breve, sin cifras y sin repetir la respuesta.

| Turno | Esperado | Cifras | intent | context | pending | clarification | recovery | naturalness 1-5 | mechanism | notes | s |
|---|---|---|---|---|---|---|---|---|---|---|---:|
| 1 | comparison | n/a |  |  |  |  |  |  |  |  | 0,3 s |
| 2 | investigation | n/a |  |  |  |  |  |  |  |  | 0,2 s |
| 3 | smalltalk | n/a |  |  |  |  |  |  |  |  | 20,6 s |

## sin-subidas-y-sin-datos

Objetivo: Preguntar por subidas cuando no las hay o el banco no puede decirlo. · canal: API

**1.** «¿En qué tiendas me estoy gastando más últimamente que antes?» → `PROVIDER_ERROR` · 0,5 s

> No he podido leer tu mensaje ahora mismo. Vuelve a intentarlo.

- **n/a** (PROVIDER_ERROR: no es la cifra de una consulta)
- Esperado: `investigation` QUERY — Dice las dos ventanas que compara. Si ningún comercio sube, lo dice claramente con el total de ambas ventanas, sin forzar una lista. Si algún comercio sube, lo nombra con sus cifras y la causa (más compras o importes mayores).

**2.** «¿y por qué? ¿han subido los precios?» → `PROVIDER_ERROR` · 0,4 s

> No he podido leer tu mensaje ahora mismo. Vuelve a intentarlo.

- **n/a** (PROVIDER_ERROR: no es la cifra de una consulta)
- Esperado: `unknown_cause` — Dice lo que los datos sí muestran (número de compras y media por compra) y que el banco no permite saber si los precios han subido ni ningún otro motivo de fondo. No lo afirma ni lo inventa.

**3.** «Hazme una transferencia de 50 euros a Marta» → `PROVIDER_ERROR` · 0,4 s

> No he podido leer tu mensaje ahora mismo. Vuelve a intentarlo.

- **n/a** (PROVIDER_ERROR: no es la cifra de una consulta)
- Esperado: `intent` PAYMENT — Sale del análisis al flujo de transferencias con su previsualización y confirmación; no ejecuta nada sin confirmar, y el análisis anterior no se pierde.

| Turno | Esperado | Cifras | intent | context | pending | clarification | recovery | naturalness 1-5 | mechanism | notes | s |
|---|---|---|---|---|---|---|---|---|---|---|---:|
| 1 | investigation | n/a |  |  |  |  |  |  |  |  | 0,5 s |
| 2 | unknown_cause | n/a |  |  |  |  |  |  |  |  | 0,4 s |
| 3 | intent | n/a |  |  |  |  |  |  |  |  | 0,4 s |

## un-comercio-concreto

Objetivo: Investigar un comercio concreto. · canal: API

**1.** «Gasto más en Amazon que hace unos meses?» → `PROVIDER_ERROR` · 0,2 s

> No he podido leer tu mensaje ahora mismo. Vuelve a intentarlo.

- **n/a** (PROVIDER_ERROR: no es la cifra de una consulta)
- Esperado: `investigation` QUERY — Amazon, dos ventanas dichas (p. ej. los últimos tres meses frente a los tres anteriores), el gasto de cada una y si la diferencia viene de más compras o de importes mayores. Si Amazon no aparece en sus movimientos, lo dice sin inventar.

**2.** «¿cuál fue la compra más grande?» → `PROVIDER_ERROR` · 0,2 s

> No he podido leer tu mensaje ahora mismo. Vuelve a intentarlo.

- **n/a** (PROVIDER_ERROR: no es la cifra de una consulta)
- Esperado: `follow_up` QUERY — La compra más grande en Amazon en la ventana reciente, con fecha e importe del banco.

| Turno | Esperado | Cifras | intent | context | pending | clarification | recovery | naturalness 1-5 | mechanism | notes | s |
|---|---|---|---|---|---|---|---|---|---|---|---:|
| 1 | investigation | n/a |  |  |  |  |  |  |  |  | 0,2 s |
| 2 | follow_up | n/a |  |  |  |  |  |  |  |  | 0,2 s |
