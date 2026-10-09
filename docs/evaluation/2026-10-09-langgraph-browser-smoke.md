# LangGraph browser smoke run

**Date:** 2026-10-09
**Status:** passed with the local model; hosted-model run was rate-limited
**Build:** source at `7df19360` with the graph category-label fix

## Setup

The shared demo stack was left untouched. An isolated Compose project, `finguard-graph-test`,
used its own PostgreSQL volume and seeded banking database. It ran the current orchestrator with
`ORCHESTRATOR_CONVERSATION_GRAPH=on` and the web client in Chrome through Playwright. The web
requests were routed to the isolated API on port 18000.

The intended Groq GPT-OSS 120B check reached Groq, which returned HTTP 429 for its token-per-minute
limit. The graph answered with its provider-error response and did not query or alter financial
state. The rest of the browser run used the local `qwen3.5:9b` model; this verifies graph routing,
PostgreSQL persistence, bank execution and rendering, but does not establish GPT-OSS 120B quality or
availability.

## Conversation and observed evidence

1. Asked for expenses by category over the last three months. The graph returned `5.937,68 €` in
   88 movements and all nine category totals from the seeded bank data.
2. Asked to show the result as a bar chart. The browser rendered the Vega chart with eight visible
   bars (the chart's configured display limit) and Spanish labels such as `alquiler` and
   `supermercado`.
3. Restarted only the isolated orchestrator container. The same browser conversation then asked to
   change the chart to lines. The graph resumed from PostgreSQL, returned version 3 after version 2,
   and the rendered response contained a Vega `mark-line`.

4. A later browser run exercised two of the reported continuity failures on the same isolated stack:
   - `¿Cuánto he gastado este mes en mi cuenta corriente?` followed by `¿Y en octubre?` kept the
     account and expense direction and returned the seven October movements (1.030,42 €).
   - `Desglosa mis gastos por categorías de los últimos 3 meses` returned all nine categories;
     `Muéstrame todas las categorías, no solo las principales` kept that result and its full
     category list. Asking for the graph rendered the chart; a separate DOM check observed Vega's
     `mark-bar` before `Cámbialo a líneas` and `mark-line` afterwards. No JavaScript page errors
     occurred.

The browser reported no JavaScript page errors. Restyling retained the same active result and did
not require a bank read; the graph unit test also asserts that both chart creation and restyling
reuse the prior banking result.

## Limits of this run

These are local-model browser smoke runs, not a Groq quality or promotion result. They cover query
continuity, a category breakdown, chart creation and restyling, and one restart. They do not verify
mixed payment and analytics turns, payment confirmation inside the main conversation graph,
booking, or the full regression corpus on LangGraph. The payment pause/resume adapter is still a
separate compiled graph, and the legacy demo at port 8000 still runs with graph mode off. Those
remain migration work; this smoke run is not a promotion gate.
