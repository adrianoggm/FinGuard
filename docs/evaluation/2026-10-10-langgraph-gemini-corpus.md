# LangGraph Gemini conversation corpus run

**Date:** 2026-10-10
**Runtime:** isolated `finguard-graph-test`, graph enabled, Gemini 3.1 Flash Lite, seeded synthetic bank
**Build provenance:** `/version` reported `sha: unknown` for both runs.

## Full frozen conversation corpus, before prompt repair

The end-to-end evaluator replayed all **67 threads / 142 turns** with a six-second minimum interval between requests. There were **20 property violations** and no HTTP 5xx or provider errors in the service logs. This is a regression diagnostic, not an A/B comparison or a promotion result.

The violations covered several distinct issues:

- An individual largest expense was returned as the merchant with the largest aggregate.
- Count questions could lose `COUNT`; the model also confused count, average, and total semantics.
- Some missing-period turns asked for clarification without exposing their pending typed plan in the response, so the public API evaluator could not verify the saved fields.
- A fuzzy request for movements was rendered with a net total as the lead answer.
- Other misses involved catalogue help, account-name grounding, salary direction, correcting a pending payment amount, and grouped comparison output.

The full per-turn report is in the local temporary directory as `finguard-graph-gemini-full-20261010.json`; it contains only the evaluator's bounded response evidence and is not committed.

## Focused rerun after prompt repair

The prompt now distinguishes a single ranked movement from a grouped merchant total and states the count/list semantics. The rebuilt isolated graph replayed six selected corpus threads (seven turns):

- The largest expense and the largest expense in June both returned one bank movement, not a merchant aggregate.
- “How many movements this month?” returned `COUNT` and seven bank movements.
- A Mercadona purchase-count clarification followed by “last month” returned a count of one and retained the merchant and period.
- “Show me movements from the last four months” retained the requested period and returned movement rows.
- Two violations remained: the evaluator cannot inspect a pending `COUNT` plan on the clarification turn, and the typo-heavy “what movements have I had?” turn leads with a total despite returning movement rows.

The focused JSON report is in the local temporary directory as `finguard-graph-gemini-focus-20261010.json`; it is not committed.

## Decision

The prompt repair is useful but the graph is **not ready for promotion**. The full run still exposes semantic and presentation gaps, and this diagnostic has no rules-engine control, blind judge, generalization set, or complete browser/payment promotion gate. The missing pending-plan evidence is a measurement limitation; it must not be reported as proven pending-state correctness solely because a later turn happened to work. The movement-list presentation mismatch remains a product issue to resolve in the typed operation/renderer boundary.
