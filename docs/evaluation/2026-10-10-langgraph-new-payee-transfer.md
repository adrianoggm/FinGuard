# LangGraph transfer to a new named payee

**Date:** 2026-10-10
**Runtime:** `main` at `b43bfc7a`, isolated Compose project `finguard-graph-test`, graph enabled, synthetic account

## Live sequence

The chat received: “Haz una transferencia a Juan Perez con IBAN ES9121000418450200051332”. Juan was not in the seeded contact directory. The graph accepted the explicit name and user-written IBAN and asked for the missing amount.

The isolated orchestrator was restarted. The conversation resumed with “20 euros” and returned a bank-authored preview for Juan Perez, the same IBAN, 20,00 EUR and the projected balance. The user declined. The graph returned `CANCELLED` at version 3. A read-only banking database query returned:

```text
CANCELLED | NULL | Juan Perez | ES9121000418450200051332
```

No payment was confirmed and no transaction was created. Only the isolated orchestrator was restarted; the shared stack was not touched. `/version` reported `b43bfc7a`, and health was restored after restart.

## Guards

The model emits only `prepare_transfer` and a semantic recipient hint. Deterministic code extracts the payee name only from the phrase adjacent to exactly one checksum-valid IBAN written by the user. The name used for banking comes from that phrase, not the model. If a different saved contact is also named, the turn is rejected before banking. IBAN digits are excluded from amount parsing. Canonical state retains the IBAN, recipient and transfer rail through clarification and restart; when a reply omits its rail, the stored rail wins over model reclassification.

Banking receives the explicit recipient name and IBAN through its typed transfer contract. Its preview and the separate explicit confirmation/cancellation flow remain in force.

## Automated verification

The focused graph, Postgres checkpoint, payment grounding, bank boundary and planner suites passed: **330 passed, 1 skipped**. Ruff and mypy passed (244 source files). The complete conversation corpus and promotion gate remain outstanding.
