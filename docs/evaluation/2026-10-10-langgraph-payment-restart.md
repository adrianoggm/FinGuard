# LangGraph payment resume after orchestrator restart

**Date:** 2026-10-10
**Stack:** isolated `finguard-graph-test` Compose project, Gemini, PostgreSQL and seeded synthetic banking API
**Build provenance:** `/version` returned `sha: unknown`; this run is not tied to a container build SHA.

## Run

1. Sent `Hazle un bizum de 1 euro a Marta Solis Vega` to the graph-enabled `/v1/questions` API using the isolated payment-enabled development credential.
2. The graph returned `AWAITING_CONFIRMATION`, with the canonical conversation at version 1 and a bank-authored preview.
3. Restarted only `finguard-graph-test-orchestrator-1`; waited for `/health` to return `ok`.
4. Replayed the version-1 conversation with `no`.
5. The graph returned `CANCELLED`, `Vale, no he enviado nada.`, and conversation version 2.

The synthetic banking API logged one payment preparation (`POST /v1/accounts/{account}/payments`) and one cancellation (`POST /v1/accounts/{account}/payments/{intent}/cancel`, 204). It logged no confirmation request. No transfer was executed.

## Verification

- `pytest tests/test_graph_codec.py tests/test_graph_investigation.py tests/test_graph_turn.py tests/test_graph_wiring.py -q`: **123 passed**.
- `ORCHESTRATOR_TEST_DATABASE_URL=postgresql://finguard_orchestrator:finguard_orchestrator@127.0.0.1:5544/finguard_orchestrator uv run pytest tests/test_graph_postgres.py -q`: **1 passed**.
- Both isolated orchestrator instances reported healthy before the run; the Gemini instance returned healthy after the targeted restart.

This demonstrates that a pending payment confirmation resumes from PostgreSQL after an orchestrator process restart and that an explicit cancellation reaches the bank without confirming. It is API evidence, not browser evidence. It does not cover a restart during an in-flight bank request, the affirmative confirmation branch against the live stack, multi-replica deployment, the full conversation corpus, or migration and rollout approval.
