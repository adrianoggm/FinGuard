# Conversaciones reales, de principio a fin

La batería de [`../ux`](../ux/README.md) reproduce sesenta turnos contra una banca simulada y una
fecha fija. Aquí se habla con FinGuard **tal y como lo ejecuta la demo**: el orquestador desplegado
por HTTP, con su modelo, el token de desarrollo real y el libro de movimientos cargado. Es la
prueba que decide si un cambio en la conversación ha funcionado (§4.1 de
[`2026-10-07-conversation-state-slice-1-design.md`](../../../../docs/superpowers/specs/2026-10-07-conversation-state-slice-1-design.md)).

La conduce la skill del repositorio
[`finguard-real-conversation`](../../../../.claude/skills/finguard-real-conversation/SKILL.md):
reproduce estas conversaciones, improvisa otras con un objetivo, lleva dos por la web real y juzga
cada turno. El instrumento es [`scripts/real_conversation.py`](../../scripts/real_conversation.py).
El preflight exige que `/ready` identifique `active_runtime=langgraph`, `engine=model`, `graph=on`
y `authoritative=true`; una ejecucion servida por legacy o un runtime inesperado se rechaza antes
de puntuar conversaciones.

## Archivos

Las sesiones largas declaran sus gestiones en `tasks` con rangos inclusivos de turnos. El runner
valida que esos rangos no se solapen y puntua cada gestion por separado, identificada junto a su
sesion para no mezclar etiquetas repetidas.

| Archivo | Qué es |
|---|---|
| [`conversations.yaml`](conversations.yaml) | Las conversaciones de la persona (6 y 7 de octubre de 2026), tal como las escribió: erratas y botones incluidos, y la de suscripciones de la batería. `expect` dice lo que **debería** pasar, no lo que pasa hoy. |
| [`../ux/conversations.yaml`](../ux/conversations.yaml) | Con `replay --ux`, las diez conversaciones de la batería se reproducen también contra la pila real (`ux-<nombre>`), con su `route` como expectativa. |
| [`holdout-sessions.yaml`](holdout-sessions.yaml) | Seis sesiones bancarias reservadas (37 turnos, 2026-10-10) para medir generalización por sesión y por tarea. **No se usan para desarrollar**: ninguna frase suya entra en prompts, ejemplos, tests ni arreglos; la que se use pasa a `conversations.yaml` y se escribe otra nueva. |
| `<fecha>-<sha>.json` | Una ejecución: el preflight, cada turno (lo dicho, la respuesta, el plan, la cifra, la cifra en SQL, el veredicto) y los hallazgos. Es el registro. |
| `<fecha>-<sha>.md` | El informe que se lee, generado a partir del `.json` con `summarise`. No se edita a mano. |

## Continuidad en sesiones largas

`conversations.yaml` also contains an eleven-turn integrated banking acceptance session. It now
charts the displayed movement page between pagination and the account switch, so the run can check
that a visual request reuses the retained page summary and does not present it as the full ledger
query. Its expectation is a product target, not a claim that the current build has passed it.

[`long-sessions-4x15.yaml`](long-sessions-4x15.yaml) contiene cuatro conversaciones completas de quince turnos cada una (60 turnos). Agrupa cambios de tema, correcciones, aclaraciones, comparaciones, el paso de un top-N a todas las categorias, graficos y cancelacion de un Bizum. Cada sesi??n conserva el estado devuelto entre turnos. El informe cuenta una sesi??n como correcta solo si pasan sus quince turnos y se alcanza el objetivo; la tasa de turnos aislados es secundaria.

El informe persiste el numero esperado de turnos y gestiones por sesion. Si el replay se interrumpe, una sesion incompleta queda sin juzgar aunque todos los turnos ejecutados hayan pasado; no se presenta como una conversacion completa.

```bash
uv run python scripts/real_conversation.py replay \
  --file evaluation/real/long-sessions-4x15.yaml \
  --skip-gateway-check \
  --min-request-interval 12 \
  --out evaluation/real/<fecha>-<sha>-long-sessions-4x15
```

El gateway A2A se omite porque estas sesiones bancarias no lo usan. El corpus congelado sigue como regresion complementaria, pero no sustituye estas sesiones para juzgar continuidad. `--min-request-interval` solo espacia los turnos HTTP del runner; no limita por sí mismo las peticiones que un turno puede hacer al modelo. El Bizum de prueba se cancela: el runner nunca confirma operaciones pendientes. Para verificar sumas y recuentos, el runner usa el plan estructurado de la respuesta (`plan_context`) si el grafo no devuelve `last_plan`; solo comprueba consultas escalares si la línea coincide y el alcance/filtros están completos. Las consultas de cuenta específica o con filtros privados omitidos se dejan como `n/a`, nunca se amplían a todas las cuentas. Las expectativas pueden declarar invariantes entre turnos; la sesión de paginación compara ids de movimientos con hashes (sin escribir ids bancarios en el informe) y deja como `unverified` una página sin filas suficientes para comprobar el cruce. La sesión integrada también comprueba que una gráfica de movimientos tenga una página anterior identificable y que `X-FinGuard-Financial-Reads` informe cero; si falta el contador se marca `unverified`. El contador incluye consultas financieras y saldos, no las lecturas de cuentas o contactos usadas para preparar el turno.

El adaptador Gemini espacia **cada petición HTTP al modelo** al menos doce segundos. Con
`DATABASE_URL`, todas las réplicas comparten el programador de PostgreSQL; sin él, el ritmo es por
proceso. La configuración de runtime no permite reducir ese mínimo. El ritmo incluye reintentos y
cambios de modelo, y pone temporalmente en cooldown un modelo tras un 429. Esta protección limita
ráfagas de este orquestador, pero no garantiza evitar límites diarios, de tokens ni consumo de otros
clientes que compartan el proyecto. Las respuestas
incluyen contadores separados para llamadas lógicas y peticiones reales, además de los modelos
intentados y usados; así se puede detectar si el fallback amplió el consumo de cuota. Si el endpoint
devuelve `usage`, el runner suma tokens de entrada/salida; si no, el informe dice «sin dato» y no
los sustituye por cero. El orquestador también devuelve y el informe agrupa etiquetas seguras de
eventos fallidos por intento HTTP (`gemini:rate_limited`, `provider:timeout`, etc.) y la causa
terminal de llamadas fallidas, en campos separados. Solo 429, timeout, transporte y 5xx se
reintentan automáticamente; rechazos de esquema y errores de configuración permanecen como fallos
sin repetición. No incluye cuerpos HTTP, prompts, claves ni respuestas del proveedor.

Si un turno termina con indisponibilidad del proveedor, el runner guarda ese fallo y marca el resto de turnos del replay como omitidos; no los envía repetidamente ni los cuenta como errores semánticos. Las conversaciones no alcanzadas permanecen en el informe como sesiones sin juzgar. El intervalo recomendado de 12 segundos coincide con el ritmo conservador del adaptador Gemini; cada reintento o failover dentro de un turno también consume una petición y tiene su propio pacing.

## Cómo se ejecuta

Desde `apps/orchestrator`, con la pila levantada y los datos cargados hasta ayer:

```bash
uv run python scripts/real_conversation.py preflight
uv run python scripts/real_conversation.py replay --ux evaluation/ux/conversations.yaml --out evaluation/real/<fecha>-<sha>
uv run python scripts/real_conversation.py replay --file evaluation/real/conversations.yaml --only sesion-bancaria-completa-paginacion-comparacion-bizum --out evaluation/real/<fecha>-<sha>-sesion
uv run python scripts/real_conversation.py replay --file evaluation/real/holdout-sessions.yaml --out evaluation/real/<fecha>-<sha>-holdout
uv run python scripts/real_conversation.py say --state <tmp>/impro-<nombre>.json --goal "<objetivo>" "<frase>"
uv run python scripts/real_conversation.py observe --state <tmp>/web-<nombre>.json --said "<frase>" --response <tmp>/captures/<respuesta>.json
uv run python scripts/real_conversation.py summarise evaluation/real/<fecha>-<sha>.json <tmp>/*.json --previous evaluation/real/<anterior>.json
```

`replay` guarda cada turno en cuanto termina: si algo se rompe a mitad, lo ya hecho queda en el
`.json`. Las conversaciones improvisadas se llaman `impro-…` y las de la web `web-…` (el script
pone el prefijo), y `summarise` se niega a juntar una que se llame como una reproducida; un
archivo que no es una conversación (una respuesta capturada en la web, por ejemplo) se salta y lo
dice.

`preflight` registra `/ready` y `/version` para identificar motor, grafo, proveedor, modelo, commit y
huella SHA-256 del código de orquestador y banca. También calcula esa huella desde el árbol local y
se niega a ejecutar si falta o difiere; `just up` la estampa en la imagen incluso con cambios sin
commit. Ejecutar Compose directamente deja la huella como `unknown`, por lo que el preflight no
permite atribuir esa sesión. Termina con 1 si un servicio, la identidad del código o el libro no
están listos y con 2 si el último movimiento es anterior a ayer: entonces hay que cargar los datos
con `just seed`, que **vacía el libro** y es un paso de la persona, nunca del script ni de la skill.
`--max-age-days N` acepta datos más antiguos a propósito, y el informe lo dice.

## Qué se comprueba solo

**Las cifras.** Cuando una respuesta es una suma o un recuento sin agrupar del plan que ejecutó, el
script la vuelve a calcular en SQL sobre `assistant_read.transactions_v1`:

- con el rol de solo lectura `finguard_query_reader`, declarando la cuenta y su titular en la
  transacción (`app.account_id`, `app.customer_id`), como hace banca;
- sin cuenta nombrada en el plan, en todas las cuentas del titular (el plan se recuerda como se
  preguntó, y `across_accounts` puede ser falso aunque las cifras salgan de todas);
- con la ventana resuelta por el propio calendario de banca (`relative_period.resolve`) y el día
  de cada movimiento en Europe/Madrid;
- con las reglas de banca escritas de otra manera: el signo del importe en lugar de la etiqueta de
  dirección, el tipo de movimiento en lugar de `is_internal`, `ILIKE` en lugar de `strpos`.

Coincidir es `match`; cualquier diferencia en el importe, el número de movimientos o la cifra que
dice la frase es `mismatch`, y un `mismatch` es un turno fallido sin excepciones. Si la comprobación
debía hacerse y no pudo (la base de datos no responde, el calendario rechaza la ventana…), es
`error`, con la clase y el mensaje, y también es un turno fallido: una cifra sin comprobar no puede
pasar como una que no había que comprobar. Lo demás es `n/a` con el motivo: comparaciones,
agrupaciones, rankings, saldos, pagos, varias preguntas en una, un «ponme un ejemplo» o una
respuesta cuya consulta no es la recordada. Esas cifras las lee el juez.

**El calendario es el de banca, leído de su código.** El orquestador no calcula fechas: envía el
nombre del periodo y el día de referencia, y banca lo convierte en días
(`apps/banking-api/src/finguard_banking/domain/value_objects/relative_period.py`). El script carga
esa misma función desde el código de banca (solo usa la biblioteca estándar), porque un segundo
calendario aquí sería una segunda respuesta a «qué es el mes pasado», y un desacuerdo entre los dos
se leería como una cifra equivocada. Es una lectura solo de evaluación, no un paquete compartido
entre servicios. Lo que **no puede** detectar, por eso mismo: un error en las cuentas de ventanas de
banca (dónde empieza «los últimos 3 meses», qué es la quincena pasada) da la misma ventana en los
dos lados, y la cifra coincide. Para eso están los casos con fechas escritas a mano de
`apps/banking-api/evaluation/expected.sql`. Sin `apps/banking-api/src` en el checkout, la prueba de
ese calendario se salta.

**Lo que nunca hace.** No escribe en la base de datos y no envía un «sí» a un pago, traspaso o
reserva pendiente: el libro es el de la demo (sale con código 3 si se le pide).

## Lo que juzga la skill

Cada turno lleva un `judgement` con el veredicto y la rúbrica: intención, contexto conservado,
pregunta pendiente resuelta o abandonada bien, aclaración solo cuando hace falta y útil,
recuperación en un turno y naturalidad (1–5, con el motivo). La etiqueta `mechanism` dice qué
mecanismo prueba el turno (`intent`, `slot_carryover`, `pending_resolution`, `interruption`,
`follow_up`, `comparison`, `reference`, `reset`, `unnecessary_clarification`, `execution`), para
que un fallo nombre lo que se rompió. `summarise` cuenta los fallos por mecanismo, la tasa de
recuperación (tras un turno fallido, ¿el siguiente va bien?), las cifras y la latencia, y lo compara
con la ejecución anterior.

Las expectativas pueden incluir `task`. El informe calcula también el éxito de cada gestión como
unidad, además del éxito de la conversación completa. `sesion-bancaria-completa-paginacion-comparacion-bizum`
es el acceptance test de diez turnos para saldos, listado y paginación, comparación y gráfico, y
preparación y cancelación segura de Bizum. Sus turnos siguen pendientes hasta que se juzgan; añadir
el caso al corpus no cuenta como evidencia de que el producto ya lo resuelve.

## Reglas

- Una expectativa de `conversations.yaml` no se edita nunca para que un turno pase.
- Una conversación improvisada que encuentra un fallo se añade aquí como una conversación nueva, en
  su propio commit.
- La primera ejecución, antes de cambiar nada, es la línea base con la que se compara el resto.
