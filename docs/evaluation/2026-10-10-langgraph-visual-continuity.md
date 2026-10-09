# LangGraph visual continuity browser check

**Date:** 2026-10-10
**Environment:** isolated `finguard-graph-test` stack, graph flag on, seeded synthetic bank, Chrome headless.

The browser conversation asked for all expense categories over the last three months, requested a chart, then changed it to lines. The query returned nine categories and 5,867.59 EUR across 87 movements. The first rendered chart showed eight of nine categories. After the display-limit fix below, the rerun rendered all nine and the line restyle produced a Vega line mark. Neither run produced browser console errors.

The run exposed three presentation issues. The chart acknowledgement used the malformed phrase “desglose de en los últimos…”. The local model classified a requested restyle as chart creation. And the renderer's default limit hid the ninth category. The graph now uses a neutral, grammatical acknowledgement, and the active-chart state tells the structured planner to emit `update_visualization` for presentation changes. The graph passes the category row count as its display limit for an unbounded category breakdown; other charts retain the default of eight. Rebuilt the isolated orchestrator and repeated the browser flow: the restyle returned “He actualizado el gráfico con la misma consulta.”, all nine category labels were visible, and the DOM contained a `line mark`.

Focused tests cover the active-chart instruction, the create/update acknowledgements, all rows of a complete category grouping, retained result lineage, and no additional banking reads for chart operations. Four focused tests passed; Ruff and mypy passed. This is a browser smoke check, not the full corpus gate or promotion of the feature flag.

## Conversation continuity follow-up

The live API run then exercised annual monthly totals and an interrupted comparison. “Which months
had the most expenses in the last year?” returned the top three month totals; “show all months”
patched the same query and returned every monthly bucket. A category query followed by “which
category increased most and why?” opened a comparison clarification. The next “return to the previous
query” exposed a state bug: model-planned clarifications were not persisted as pending, so restore
looked for older history and failed even though the last answered query remained on screen.

The transition now persists an open question when an operation cannot apply, including a comparison
that lacks periods. Restoring while that question is open clears the unfinished question, removes
its stale turn from recent context, and reruns the current answered focus. The real-browser sequence
now continues from that restore with “and in October?” and patches the same category query to October.
It rendered the chart and line restyle, displayed all nine categories, and showed October's 7
movements; Chrome reported no JavaScript errors.

The API also returned the top three months for the last year, then all monthly buckets after “show
all months”. Focused tests cover restoring an interrupted comparison, preserving the next period
follow-up, and ordinary history restoration. Six focused tests pass; Ruff and mypy pass. These checks
used the isolated synthetic stack. The full 10-turn acceptance and corpus evaluation remain before
changing the feature flag.

## Rolling comparison, investigation, chart and restore (2026-10-10)

The isolated `finguard-graph-test` stack ran with the graph flag enabled, seeded banking data, and local Qwen 3.5 9B. A real Chrome conversation completed these eight turns:

| Turn | Result |
| --- | --- |
| Expenses by category, last three months | 5,867.59 EUR, 87 movements, all nine category totals |
| Show all categories | Retained the same query and all nine totals |
| Compare with the prior three months | 6,510.80 EUR (2026-04-10–2026-07-09) vs 5,867.59 EUR (2026-07-10–2026-10-10) |
| Show a chart | Rendered the comparison |
| Change it to lines | Updated the same chart; DOM contained two Vega line marks |
| Why the difference? | Investigated merchant totals and purchase counts over the exact comparison windows |
| Return to the previous query | Restored the full category query |
| And in October? | 1,030.42 EUR, seven movements, with October category totals |

The comparison prompt uses a typed `compare_previous=N` operation. Domain transitions resolve it against the active rolling query and validate that its period matches. For a cause follow-up, `investigate_comparison` reuses the stored pair and filters; the graph reads merchants and may ask its bounded assessor to inspect supporting transactions. Thus the planner does not have to regenerate period dates or restate filters. Investigation does not add a duplicate focus to history, so restore returns to the category query.

The run produced no browser JavaScript errors. Unit and graph tests cover exact window reuse, checkpointed scalar comparison reads, recovery, restore history, and the chart update operation. The corpus evaluation and hosted Gemini/Groq provider comparison remain outstanding; this run establishes the local model path only.
