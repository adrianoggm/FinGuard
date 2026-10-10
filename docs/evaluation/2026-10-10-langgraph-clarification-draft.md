# LangGraph clarification draft continuity

## Change

Clarifications now carry a closed, typed draft alongside the missing slot. The LangGraph transition
stores that draft in canonical `Pending` state, so a short reply fills the open field without asking
the model to reconstruct filters or output shape. The model contract expresses incomplete queries
as typed `ask` operations; the interpreter and graph create internal clarification transitions.

An `ALL` direction also requires an explicit metric in a new ask or clarification draft. If the model
omits it, the interpreter rejects that operation and gets its single existing repair attempt. That
prevents the domain's default `SUM` from turning a movement-list request into a total.

## Evidence

- Interpreter plus LangGraph graph suite: **286 passed** (including a single retry for provider 5xx).
- `mypy src/finguard_orchestrator`: **245 source files, no issues**.
- Hosted Gemini, isolated Docker stack with the graph enabled:
  - `Perdona que movimientos he tenido?` returned a period clarification and committed a pending
    draft with `direction=ALL` and `metric=COUNT` to Postgres.
  - `este mes` resumed that same conversation at version 2 and returned `7 movimientos`,
    `metric=COUNT`, and seven movement rows.
- Frozen corpus case `show_me_the_movements`: **1 turn, 0 property violations**.
- Live HTTP continuity on the rebuilt isolated Docker stack:
  - `Perdona que movimientos he tenido?` asked for a period.
  - `este mes`, carrying the returned conversation, resumed at version 2 with a `COUNT` plan and
    seven movement rows.
- No banking payment or transfer operation was involved.

## Full-corpus result after the change

The full Gemini run used the synthetic payment-enabled test credential, the same 67 frozen threads,
and a six-second request interval. It replayed all **142/142 turns** without banking authorization
failures. It recorded **23 property-violating turns**. Outcomes included 94 planned answers, 27
clarifications, six payment previews, one cancellation, and five provider errors. Container logs
identified those five errors as Google HTTP 503 service-unavailable responses. No payment was
confirmed. The provider adapter now retries one 5xx response once after a short delay; the corpus
has not yet been replayed with that change.

The focused continuity regression is fixed, but this corpus run does **not** pass the broader quality
gate. Remaining failures cover category intent, unsupported averages, Bizum correction wording,
missing-window clarifications, account grounding, direction/metric inheritance, grouped comparison
presentation, and unreadable/refused model outputs. The run report does not include token usage or
per-turn latency, so those metrics remain unmeasured here.

The isolated service listens on port 18000. This is test evidence only: `/version` does not report a
source SHA and the production feature flag/default has not been promoted. The existing untracked
`.conversation-test-results.txt` and `.superpowers/` artifacts were left untouched.

## Recovery policy and follow-up

The interpreter already allows one structured repair when the model output cannot be read or the
domain rejects a value. A provider outage is different and must not be sent back as a semantic
repair. The hosted adapter now retries one 5xx after 0.5 seconds, and for HTTP 429 it waits once
only when `Retry-After` or Google's structured `RetryInfo.retryDelay` is at most 20 seconds. Daily
quota failures without a short delay fail fast; logs and the returned error include only the
provider code and quota identifiers, not the error message or prompt.

A targeted post-change replay of four affected conversations confirmed that the grocery-to-month
follow-up retained its category and returned the new month, and that a category follow-up kept the
previous account and period when the model answered. Three requests were blocked by HTTP 429; one
correction consequently restarted with a period question because its preceding request had failed.
This is quota evidence, not evidence for or against semantic judging.

An LLM judge is not yet on the runtime path. First use the frozen corpus as an offline judge of
semantic consistency, then measure whether a separate verifier catches valid-but-wrong plans at
acceptable latency and call cost. If promoted later, it may reject a plan and request at most one
replan with typed feedback; only deterministic transition checks, bank results, and existing
payment guards may authorize execution or figures.
