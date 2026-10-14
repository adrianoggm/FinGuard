# LangGraph live transfer preparation and cancellation smoke

**Date:** 2026-10-10
**Checkout:** `main` at `f0cab919`
**Runtime:** isolated Compose project `finguard-graph-test`, graph enabled, seeded synthetic banking data
**Runtime provenance:** `/version` reported `sha: f0cab919`, `ref: main`; `/health` returned `ok` after the isolated orchestrator was rebuilt and recreated. The shared stack was not restarted.

## API sequence

An authenticated request to `POST /v1/questions` asked: “Haz una transferencia de 1 euro a Aitor Beltran Diaz”. The graph returned `AWAITING_CONFIRMATION`, conversation version 1, and a `PAYMENT_CONFIRMATION` workflow. The bank-backed preview identified Aitor Beltran Diaz, showed the saved synthetic IBAN ending `7892`, the 1,00 EUR amount, and the projected balance of 613,56 EUR. The response did not execute the transfer.

The next request answered “no” using the canonical workflow state. The graph returned `CANCELLED`, “Vale, no he enviado nada.”, conversation version 2, and cleared the workflow.

## Bank-side verification

Queried the isolated banking database in read-only mode for intent `a37e96e0-35bf-4712-98e5-e63e72cf1dd6`. The row was `CANCELLED`, with `transaction_id` NULL and rail `TRANSFER`. Thus this smoke verified a real bank preview and cancellation without creating a payment transaction.

This was an API and seeded-data smoke, not a browser run or a production payment. Transfers to newly typed bank details and booking creation remain outside this migrated flow. The broader corpus and promotion gates remain outstanding.
