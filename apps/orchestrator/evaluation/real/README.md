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

## Archivos

| Archivo | Qué es |
|---|---|
| [`conversations.yaml`](conversations.yaml) | Las conversaciones de la persona (6 y 7 de octubre de 2026), tal como las escribió: erratas y botones incluidos. `expect` dice lo que **debería** pasar, no lo que pasa hoy. |
| `<fecha>-<sha>.json` | Una ejecución: el preflight, cada turno (lo dicho, la respuesta, el plan, la cifra, la cifra en SQL, el veredicto) y los hallazgos. Es el registro. |
| `<fecha>-<sha>.md` | El informe que se lee, generado a partir del `.json` con `summarise`. No se edita a mano. |

## Cómo se ejecuta

Desde `apps/orchestrator`, con la pila levantada y los datos cargados hasta ayer:

```bash
uv run python scripts/real_conversation.py preflight
uv run python scripts/real_conversation.py replay --out evaluation/real/<fecha>-<sha>
uv run python scripts/real_conversation.py say --state <tmp>/<nombre>.json --goal "<objetivo>" "<frase>"
uv run python scripts/real_conversation.py observe --state <tmp>/web-<nombre>.json --said "<frase>" --response <respuesta.json>
uv run python scripts/real_conversation.py summarise evaluation/real/<fecha>-<sha>.json <tmp>/*.json --previous evaluation/real/<anterior>.json
```

`preflight` termina con 1 si un servicio o el libro no responden y con 2 si el último movimiento es
anterior a ayer: entonces hay que cargar los datos con `just seed`, que **vacía el libro** y es un
paso de la persona, nunca del script ni de la skill. `--max-age-days N` acepta datos más antiguos a
propósito, y el informe lo dice.

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
dice la frase es `mismatch`, y un `mismatch` es un turno fallido sin excepciones. Lo demás es `n/a`
con el motivo: comparaciones, agrupaciones, rankings, saldos, pagos, varias preguntas en una, un
«ponme un ejemplo» o una respuesta cuya consulta no es la recordada. Esas cifras las lee el juez.

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

## Reglas

- Una expectativa de `conversations.yaml` no se edita nunca para que un turno pase.
- Una conversación improvisada que encuentra un fallo se añade aquí como una conversación nueva, en
  su propio commit.
- La primera ejecución, antes de cambiar nada, es la línea base con la que se compara el resto.
