# LangGraph live API continuity check

**Date:** 2026-10-09
**Checkout:** `main` at `004b667e`
**Stack:** isolated Compose project `finguard-graph-test`, graph enabled, Qwen 3.5 9B, seeded banking data

The running orchestrator's `/version` endpoint reported `sha: unknown`, so this is runtime evidence
for the isolated graph deployment, not a binary-to-commit provenance check. The API was healthy at
`http://localhost:18000/health`. No shared stack was restarted.

## Conversation and persistence

The authenticated API sequence asked for all expenses by category over the last three months,
requested all categories, rendered the result as a chart, and changed the chart to lines. It returned:

| Turn | Result | Total | Groups | Version |
| --- | --- | ---: | ---: | ---: |
| Category breakdown | 5.937,68 EUR | 593.768 minor units | 9 | 1 |
| Show all categories | 5.937,68 EUR | 593.768 minor units | 9 | 2 |
| Visualize | bar chart | 593.768 minor units | — | 3 |
| Change to lines | line chart | 593.768 minor units | — | 4 |

The visual-only replies contained an empty `groups` array but retained the aggregate and visualization
specification. The prior analytical reply held the nine groups.

Only the isolated orchestrator container was then restarted. After `/health` returned `ok`, the same
conversation state was sent with `¿Y por comercio?`. The graph returned 30 merchants over the same
three-month period, retained the 5.937,68 EUR total and advanced to version 5. This verifies graph
continuity across that process restart against the currently running isolated service.

The focused PostgreSQL integration test `test_postgres_state_cas_and_turn_resume_replay` passed
against the isolated database. It covered account-scoped loads, CAS race behavior, retry after a
failed reservation, replay of a completed request and rejection of a reused request id with a
different fingerprint.

## Limits

This is a direct API check, not a browser rendering run. Existing browser evidence for Vega rendering
and chart restyling is recorded in `2026-10-09-langgraph-browser-smoke.md`. The live stack's build SHA
was unknown and this was a single seeded conversation. Saved-contact transfer preparation is now
covered by graph tests but has not had a live banking or browser run. Transfers to newly typed bank
details and bookings remain unmigrated; the full conversation corpus is also outstanding.
