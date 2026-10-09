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
booking or the full regression corpus on LangGraph. This smoke run is not a promotion gate.

## Unified graph follow-up (2026-10-09)

The isolated orchestrator was rebuilt from the working tree and exercised in Chrome against the
same seeded banking database, with `ORCHESTRATOR_CONVERSATION_GRAPH=on` and local Qwen 3.5 9B.
The following happened in one browser conversation:

1. `Hazle un bizum de 20 euros a Marta Solis` returned the bank-authored preview for Marta Solis
   Vega, including 20,00 EUR and the remaining balance. No payment was sent. Replying `no` called
   the bank cancellation endpoint and the graph answered `Vale, no he enviado nada.`
2. `Desglosa mis gastos por categoría de los últimos 3 meses` returned 5.937,68 EUR across 88
   movements and all nine category totals. The renderer showed the first ten movements and kept
   the total across all 88.
3. `Muéstramelo en un gráfico` rendered the category result with the existing eight-category
   display limit. `Cámbialo a líneas` reused the displayed result; Chrome's rendered Vega DOM
   contained `mark-line` after the change.

This covers graph-owned Bizum preview/cancel, analysis, visualization and restyling in one real UI
session. It does not authorize or exercise the final money transfer, and this run did not restart
the orchestrator between the payment pause and reply. Unit tests cover the guarded `sí` path,
missing scopes, amount mismatch, ambiguous replies, and mixed analytics/Bizum with one canonical
version advance. Transfer and booking workflows remain unavailable on the graph route.

## Gemini continuity and protected-action run (2026-10-09)

The isolated stack was run with Gemini 3.1 Flash Lite using the configured key and synthetic bank
data. In one Chrome conversation:

1. An August account query returned 2.159,87 EUR in 33 movements. `¿Y en julio?` kept the account
   and expense direction and returned 1.895,97 EUR in 28 movements.
2. `¿Y comparado con agosto?` compared July with August, kept Cuenta corriente, and reported the
   263,90 EUR increase.
3. `¿A qué se debe la subida?` reused the comparison. The graph read both periods by merchant and
   returned merchant totals, movement counts, average amounts and the largest purchase for one
   merchant. This exercised the graph's bounded second model decision and its bank read.
4. `¿Cuánto he gastado este mes en mi cuenta corriente y envía un bizum de 1 euro a Marta Solis
   Vega?` returned the 1.030,42 EUR query and a 1,00 EUR bank-authored Bizum preview in the same
   conversation. Replying `no` canceled the preview. No money was sent.

This run first exposed and then verified a Gemini schema adapter defect: the shallow operation
schema sent `prepare_bizum` as an empty object, so Gemini returned no recipient or amount. The
adapter now preserves both required fields while the canonical reader still validates the full
operation schema. `test_gemini_provider.py` covers that wire shape.

**Known analysis boundary at the time of this run:** comparing September with the partial month of
October showed lower total spending in October, while the then-current investigation only reported
increases. The graph follow-up below adds symmetric merchant decreases attributed to purchase count
and average amount. This correction has focused graph tests but has not yet had a browser rerun.

Focused verification after these changes: 219 tests passed, one PostgreSQL integration test was
skipped because its optional test database is unset, and four prompt-size budget cases were
deselected; Ruff passed and mypy passed for the changed graph, state, controller and Gemini
provider modules. The excluded prompt-size cases remain a prompt-budget issue, not a passing check.

## Previous query operation (2026-10-09)

Added the structured `restore_previous` operation. Canonical state retains up to eight typed prior
focuses and their account selections; the model selects a one-based history position. Restoring a
focus runs its plan through the normal graph executor and queries banking again, so old totals and
movement rows are not replayed. The dialogue codec and PostgreSQL store accept schema versions
5/6 and new saves use version 7. The graph checkpoint version was advanced to avoid resuming paused
work under changed state semantics. Unit coverage selects an older query by depth and verifies a
fresh bank read, plus codec persistence of the history. Chrome with Gemini 3.1 Flash Lite exercised
both continuity paths: August 2.159,87 EUR → July 1.895,97 EUR → `Vuelve a la consulta anterior`
returned August 2.159,87 EUR; and “gimnasio” over the last month returned 59,80 EUR, followed by
`¿Y en octubre?` which retained the merchant filter and returned 29,90 EUR. The browser run also
exposed that PostgreSQL rejected stored schema version 5; the store now reads it. No payment was
attempted in this run.

The next browser pass went beyond one-step restoration: it asked August, July and June, then
`Vuelve a la consulta de agosto`, which selected history position 2 and freshly returned 2.159,87
EUR. It then queried all categories for three months, rendered the chart, and changed it to lines;
the DOM exposed Vega `mark-line` after the restyle. The renderer reused the stored result and made
no new bank query for that visual-only change.

A repeated-restore attempt exposed that consuming an older history position discarded the other
entries, so August could not be revisited after a later category query. The history now removes only
the selected entry and moves the current focus back into the history. Chrome with Gemini verified
August → July → June → restore August → category breakdown → restore August again. After restarting
the isolated orchestrator, it restored July from the same conversation and returned 1.895,97 EUR.
The history survives a process restart, keeps the account selection, and each restore performs a
fresh bank read.

## Month comparison follow-up with GPT-OSS 120B (2026-10-09)

Rebuilt the isolated `finguard-graph-test` Compose project and exercised the real web client in
headless Chrome, with the graph enabled, the seeded banking database, and Groq `openai/gpt-oss-120b`.
The conversation asked for September expenses in Cuenta corriente, October expenses, and then
`Y comparado con septiembre?`. GPT-OSS returned the comparison between September and the month to
date, retained Cuenta corriente, and reported the 1.066,94 EUR difference. Chrome reported no
JavaScript errors.

This run also reached Groq's token-per-minute limit: an intervening follow-up and subsequent
turns returned FinGuard's provider-unavailable response. The API logs recorded HTTP 429 and reset
metadata without logging prompts or credentials. Therefore this verifies the named-month comparison
and account continuity in the browser, but not a complete ten-turn run or the explanation and chart
turns under the hosted model.

The browser run with local Qwen 3.5 9B had first shown that a small model could ask which windows to
compare even though October was active and September was in history. The operations prompt already
contained the correct example, but its example selector omitted it. The selector now includes that
example while staying inside the maximum prompt budget. The interpreter tests cover its inclusion;
the Chrome/GPT-OSS comparison above exercises the corrected path. Groq's token quota remains the
limiting factor for a full hosted-model conversation.
