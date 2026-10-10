# LangGraph clarification draft continuity

## Change

Clarifications now carry a closed, typed draft alongside the missing slot. The LangGraph transition
stores that draft in canonical `Pending` state, so a short reply fills the open field without asking
the model to reconstruct filters or output shape. This applies to `ask` and `clarify` operations.

An `ALL` direction also requires an explicit metric in a new ask or clarification draft. If the model
omits it, the interpreter rejects that operation and gets its single existing repair attempt. That
prevents the domain's default `SUM` from turning a movement-list request into a total.

## Evidence

- Interpreter plus LangGraph graph suite: **281 passed**.
- `mypy src/finguard_orchestrator`: **245 source files, no issues**.
- Hosted Gemini, isolated Docker stack with the graph enabled:
  - `Perdona que movimientos he tenido?` returned a period clarification and committed a pending
    draft with `direction=ALL` and `metric=COUNT` to Postgres.
  - `este mes` resumed that same conversation at version 2 and returned `7 movimientos`,
    `metric=COUNT`, and seven movement rows.
- Frozen corpus case `show_me_the_movements`: **1 turn, 0 property violations**.
- No banking payment or transfer operation was involved.

## Full-corpus result after the change

The full Gemini run used the synthetic payment-enabled test credential, the same 67 frozen threads,
and a six-second request interval. It replayed all **142/142 turns** with no HTTP 5xx and no payment
authorization failures. It recorded **23 property-violating turns**. Outcomes included 94 planned
answers, 27 clarifications, six payment previews, one cancellation, and five provider/operation
errors. No payment was confirmed.

The focused continuity regression is fixed, but this corpus run does **not** pass the broader quality
gate. Remaining failures cover category intent, unsupported averages, Bizum correction wording,
missing-window clarifications, account grounding, direction/metric inheritance, grouped comparison
presentation, and five unreadable/refused provider outputs. The run report does not include token
usage or per-turn latency, so those metrics remain unmeasured here.

The isolated service listens on port 18000. This is test evidence only: `/version` does not report a
source SHA and the production feature flag/default has not been promoted. The existing untracked
`.conversation-test-results.txt` and `.superpowers/` artifacts were left untouched.
