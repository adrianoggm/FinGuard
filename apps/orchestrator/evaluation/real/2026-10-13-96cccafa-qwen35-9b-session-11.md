# Conversaciones reales — 2026-10-13, `unknown`

**Preflight** (2026-10-13T21:04:32+02:00)

- Servicios: orchestrator ok · banking ok · gateway skipped (not used by this evaluation)
- Orquestador: `unknown` (unknown) · HEAD `96cccafa` · source `bf236dd9542898fef6a5420ae83867889cef42bbd4a91fb1057c1fc17f2cd231` · código del producto: igual que el código local
- Banking API: `unknown` (unknown) · source `bf0592a5ee7e448eeba77a7c0494f3be45e7b0d7924db3ad3b015a1666bbec27`
- Libro: último movimiento 2026-10-12 (hoy 2026-10-13)
- GPU: P4, 187 MHz · Ollama en memoria: qwen3.5:9b

## Resumen

Sin ejecución anterior con la que comparar.

| Mecanismo | Turnos | Fallidos | Sin juzgar | Acierto | Antes |
|---|---:|---:|---:|---:|---:|
| comparison | 1 | 0 | 1 | — | — |
| follow_up | 2 | 1 | 1 | 0 % | — |
| intent | 3 | 0 | 3 | — | — |
| interruption | 2 | 0 | 2 | — | — |
| reference | 2 | 0 | 2 | — | — |
| reset | 1 | 0 | 1 | — | — |

### Gestiones de las sesiones

| Gestión | Turnos | Estado | Fallidos | Sin juzgar | Invariantes |
|---|---:|---|---:|---:|---|
| sesion-bancaria-grafica-de-pagina-y-bizum-nombrado / bizum-cancelacion | 2 | pendiente | 0 | 2 | — |
| sesion-bancaria-grafica-de-pagina-y-bizum-nombrado / capacidad-bizum | 1 | pendiente | 0 | 1 | — |
| sesion-bancaria-grafica-de-pagina-y-bizum-nombrado / movimientos-paginacion | 4 | fallida | 1 | 3 | failed; passed |
| sesion-bancaria-grafica-de-pagina-y-bizum-nombrado / restaurantes-comparacion-visualizacion | 3 | pendiente | 0 | 3 | — |
| sesion-bancaria-grafica-de-pagina-y-bizum-nombrado / saldos | 1 | pendiente | 0 | 1 | — |

- Recuperación (tras un turno fallido, ¿el siguiente va bien?): 0 de 0 (—)
- Fallos de disponibilidad (también fallan la sesión): provider 0 · banking 0 · upstream_http 0 · transport 0
- Provider failure attempts (safe category): ninguno registrado
- Terminal provider-call failures: none recorded
- Turnos intentados / omitidos / previstos: 11 / 0 / 11
- Cifras contra el libro: 0 match · 0 mismatch · 0 sin poder comprobar (error) · 11 n/a
- Latencia por turno: mediana 4,6 s · p95 7,2 s · máx 7,2 s (11 turnos)
  - sin la espera del proveedor: mediana 4,6 s · p95 7,2 s · máx 7,2 s (11 turnos)
  - solo peticiones al modelo: mediana 0,0 s · p95 0,0 s · máx 0,0 s (11 turnos)
  - motor `model`: mediana 4,6 s · p95 7,2 s · máx 7,2 s (11 turnos)
- Conversaciones logradas (todos los turnos bien y objetivo cumplido): 0 de 1 juzgadas (0 %) · 0 sin juzgar
- Llamadas al modelo por turno: media 1,27 · p95 2 (11 turnos con dato)
- Peticiones HTTP reales al proveedor (incluye reintentos/fallback): 14 en 11 turnos · media 1,27 · p95 2
- Modelos intentados/usados: `qwen3.5:9b`: 14 intentos, 14 respuestas
- Tokens informados por proveedor: 55542 total en 11 turnos (54990 entrada, 552 salida; 14 respuestas con usage)

## sesion-bancaria-grafica-de-pagina-y-bizum-nombrado

Objetivo: Completar varias gestiones en una conversación: consultar saldos, listar movimientos y paginar sin duplicados, graficar la página de movimientos ya mostrada, cambiar de tema, comparar restaurantes, visualizar el resultado y cancelar un Bizum antes de ejecutarlo. · canal: API

**1.** «¿Me dices cómo están mis cuentas?» → `PLANNED` · 7,2 s

> -1.079,42 € del 1 de octubre al 13 de octubre de 2026, en 9 movimientos.
>   · bizum: -49,00 € (2)
>   · suscripciones: -54,88 € (3)
>   · restaurantes: -55,54 € (3)
>   · alquiler: -920,00 € (1)
> Consultado en todas tus cuentas.

- Consulta: del 1 de octubre al 13 de octubre de 2026, movimientos, por categoría · Cifra: -1.079,42 € en 9 mov. · **n/a** (no queda un plan verificable en la respuesta)
- Grupos: TRANSFER_BIZUM -49,00 € · SUBSCRIPTIONS -54,88 € · RESTAURANTS -55,54 € · HOUSING_RENT -920,00 €
- Esperado: `intent` BALANCE — Saldo actual por cuenta y total basado únicamente en la banca; no confunde saldo de inversión con valoración actual.

**2.** «Enséñame los últimos 20 gastos de la corriente.» → `PLANNED` · 4,6 s

> Estos son tus últimos 20 movimientos. Dime "dame otros 20" para ver los siguientes.
> Consultado en Cuenta corriente.

- Consulta: gastos recientes · **n/a** (no queda un plan verificable en la respuesta)
- Esperado: `intent` QUERY — Una lista ordenada de hasta 20 movimientos de gasto de Cuenta corriente, con fechas, comercios e importes; no solicita periodo.

**3.** «Dame otros 10.» → `PLANNED` · 4,8 s

> Estos son tus últimos 10 movimientos. Dime "dame otros 10" para ver los siguientes.
> Consultado en Cuenta corriente.

- Consulta: gastos recientes · **n/a** (no queda un plan verificable en la respuesta) · Invariante de sesión: **failed** (10 movimiento(s) repetido(s) entre páginas)
- Esperado: `follow_up` QUERY — Devuelve la página siguiente de hasta 10 gastos de Cuenta corriente, ordenada igual y sin repetir ninguna fila de la página anterior.

**4.** «¿Me los enseñas en una gráfica?» → `PLANNED` · 6,9 s

> Aquí tienes un gráfico de los movimientos de esta página.

- Consulta: Importe por categoría en esta página (10 movimientos) · Cifra: 10 mov. · **n/a** (no queda un plan verificable en la respuesta) · Reutilizacion del resultado: **passed** (reutilizo la pagina anterior sin repetir lecturas financieras; lecturas financieras: 0)
- Sugerencias: «Ordena de menor a mayor» · «Ponlo en vertical»
- Esperado: `reference` VISUAL — Dibuja los movimientos de la página que acaba de mostrar, dejando claro que no representa el total de una consulta mayor y reutilizando el resultado ya obtenido.

**5.** «¿Y los de la Hucha?» → `PLANNED` · 4,5 s

> No he encontrado movimientos recientes en tus cuentas.
> Consultado en Hucha.

- Consulta: gastos recientes · **n/a** (no queda un plan verificable en la respuesta)
- Esperado: `follow_up` QUERY — Conserva el tipo de lista y la dirección de gastos, cambia solo la cuenta a Hucha y muestra que no hay gastos en esa cuenta; no convierte traspasos internos ni intereses en gastos.

**6.** «¿Cuánto gasté en restaurantes el mes pasado?» → `PLANNED` · 4,4 s

> 187,94 € en restaurantes el mes pasado, en 6 movimientos.
>   · restaurantes: 187,94 € (6)
> Consultado en todas tus cuentas.

- Consulta: el mes pasado, gastos, restaurantes, por categoría · Cifra: 187,94 € en 6 mov. · **n/a** (no queda un plan verificable en la respuesta)
- Grupos: RESTAURANTS 187,94 €
- Esperado: `interruption` QUERY — Inicia una consulta independiente de restaurantes para el mes pasado en todas las cuentas; no hereda Hucha ni el límite de movimientos.

**7.** «Compáralo con agosto.» → `NEEDS_CLARIFICATION` · 5,9 s

> ¿Qué dos periodos quieres comparar?

- **n/a** (NEEDS_CLARIFICATION: no es la cifra de una consulta)
- Esperado: `comparison` QUERY — Compara el mismo gasto en restaurantes entre el mes pasado y agosto, con ambos periodos explícitos y resultados bancarios.

**8.** «Ponme una gráfica.» → `PLANNED` · 3,5 s

> Con un solo resultado no hay nada que comparar en un gráfico; aquí tienes la cifra.

- Consulta: el mes pasado, gastos, restaurantes, por categoría · Cifra: 187,94 € en 6 mov. · **n/a** (no queda un plan verificable en la respuesta)
- Esperado: `reference` VISUAL — Dibuja la comparación anterior usando sus resultados verificados, sin una nueva lectura bancaria ni un desglose distinto.

**9.** «¿Puedes hacer Bizums?» → `IDENTITY` · 3,4 s

> Puedo decirte cuánto gastaste o ingresaste en un periodo, por categoría, comercio o persona; tus mayores gastos; comparar dos periodos, también en porcentaje; qué parte de tus gastos se fue en algo; y tu saldo. También puedo preparar un Bizum para uno de tus contactos guardados y enseñarte la vista previa. No se envía hasta que confirmes.

- **n/a** (IDENTITY: no es la cifra de una consulta)
- Esperado: `interruption` — Explica la capacidad sin crear un borrador de pago ni iniciar una confirmación.

**10.** «Vale, quiero enviar 15 euros a Marta Ruiz Gomez.» → `INVALID_DOMAIN` · 5,1 s

> No he podido verificar el importe, el destinatario y el tipo de pago en tu mensaje y tus contactos. Indica el importe, un contacto reconocible y si quieres hacer un Bizum o una transferencia.

- **n/a** (INVALID_DOMAIN: no es la cifra de una consulta)
- Esperado: `intent` PAYMENT — Resuelve Marta contra los contactos y muestra la previsualización bancaria exacta; todavía no envía dinero.

**11.** «No, mejor déjalo.» → `PLANNED` · 4,0 s

> 187,94 € en restaurantes el mes pasado, en 6 movimientos.
>   · restaurantes: 187,94 € (6)
> Consultado en todas tus cuentas.

- Consulta: el mes pasado, gastos, restaurantes, por categoría · Cifra: 187,94 € en 6 mov. · **n/a** (no queda un plan verificable en la respuesta)
- Grupos: RESTAURANTS 187,94 €
- Esperado: `reset` — Cancela el borrador o la previsualización y no ejecuta el pago.

| Turno | Esperado | Cifras | intent | context | pending | clarification | recovery | naturalness 1-5 | mechanism | notes | s |
|---|---|---|---|---|---|---|---|---|---|---|---:|
| 1 | intent | n/a |  |  |  |  |  |  |  |  | 7,2 s |
| 2 | intent | n/a |  |  |  |  |  |  |  |  | 4,6 s |
| 3 | follow_up | n/a |  |  |  |  |  |  |  |  | 4,8 s |
| 4 | reference | n/a |  |  |  |  |  |  |  |  | 6,9 s |
| 5 | follow_up | n/a |  |  |  |  |  |  |  |  | 4,5 s |
| 6 | interruption | n/a |  |  |  |  |  |  |  |  | 4,4 s |
| 7 | comparison | n/a |  |  |  |  |  |  |  |  | 5,9 s |
| 8 | reference | n/a |  |  |  |  |  |  |  |  | 3,5 s |
| 9 | interruption | n/a |  |  |  |  |  |  |  |  | 3,4 s |
| 10 | intent | n/a |  |  |  |  |  |  |  |  | 5,1 s |
| 11 | reset | n/a |  |  |  |  |  |  |  |  | 4,0 s |
