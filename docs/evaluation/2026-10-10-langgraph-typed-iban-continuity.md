# LangGraph typed-IBAN transfer continuity

**Date:** 2026-10-10
**Checkout/runtime:** `main` at `43f95aa8`
**Environment:** isolated Compose project `finguard-graph-test`, graph enabled, seeded synthetic banking account

## Live API sequence

The first `/v1/questions` request asked for a transfer to saved contact Aitor Beltran Diaz and included the Spanish IBAN `ES91 2100 0418 4502 0005 1332`, but omitted the amount. The graph returned `NEEDS_CLARIFICATION` and asked for the amount. The conversation was stored at version 1.

Only the isolated orchestrator container was restarted. After it became healthy, the same conversation was resumed with “20 euros”. The graph returned `AWAITING_CONFIRMATION` at version 2, printed the same IBAN in the bank-authored preview, and showed the projected balance. No payment was confirmed.

The user then answered “no”. The graph returned `CANCELLED` at version 3. A read-only query of the banking database found the corresponding intent as `CANCELLED`, with `transaction_id` NULL and the same IBAN. The shared stack was not restarted.

`/version` reported SHA `43f95aa8` and `/health` returned `ok` after restart. This verifies durable draft state across an orchestrator process restart, continuation, bank preview, and cancellation against the exact committed build.

## Guards and implementation notes

The LLM emits only the typed `prepare_transfer` operation and contact name; it cannot provide the IBAN. Deterministic code extracts one IBAN from the user's sentence, checks its mod-97 checksum, and removes IBAN digits from amount parsing. A transfer operation must still match the spoken rail and a unique contact in the caller's bank directory. Banking receives the name and IBAN as an explicit transfer destination and remains responsible for its own validation and immutable payment intent. Confirmation remains a separate explicit turn.

The prompt now retains the canonical rail when continuing a payment draft, so a pending transfer cannot be reinterpreted as a Bizum. Checkpoint codec coverage confirms the IBAN survives canonical state serialization.

## Automated verification

The focused graph, checkpoint, banking-boundary, payment-grounding and interpreter suites passed: **327 passed, 1 skipped**. Ruff and mypy passed (244 source files). The broader full-corpus promotion gate has not been run as part of this smoke.
