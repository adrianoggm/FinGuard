# ADR-013: LangGraph as FinGuard's conversation runtime

**Status:** accepted, migration in progress
**Date:** 2026-10-09

## Decision

LangGraph will become the only coordinator for user conversations. It owns turn sequencing,
continuation, bounded analysis, visual operations, pauses and resume. A configured LLM produces
typed operations; deterministic domain code validates them and banking contracts execute them.
The legacy handlers and linguistic readers are frozen except for critical security fixes and
temporary compatibility adapters that do not interpret user language.

This decision does not move financial authority into the model or the graph. Banking remains the
source of figures. Existing permission checks, payment validation, explicit confirmation,
idempotency and compare-and-set version checks remain mandatory at the operation boundary.

## Canonical conversation state

The graph's versioned conversation state is the source of truth. It includes the active financial
plan, pending operation or clarification, recent turns, a bounded aggregate-only result artifact,
and the verified chart specification/profile with result and chart versions. Its version-8 schema
also has typed slots for an unfinished payment draft or the exact bank-authored confirmation
preview, so a later graph turn can resume from canonical state without reconstructing payment data
from the client. Bizum preparation and confirmation now run in the same conversation graph. Mixed
analytics/Bizum turns save the analytic result and pending bank preview with one canonical version
advance. Own-account transfers use a typed pending draft when the destination or amount is missing,
then the existing bank preview and explicit-confirmation path. Drafts retain an opaque idempotency
key across clarification turns; supplied values are grounded against the account catalogue and
closed amount grammar before the payment command is called. Checkpoint state is versioned separately
from the application schema. Movement ledgers never enter canonical state or checkpoints.

The canonical dialogue state retains up to eight typed prior focuses and their account scopes. The
model can emit the closed `restore_previous` operation when the user explicitly goes back, selecting
one by its one-based position in the history shown to the model. The graph restores that typed focus
and executes it against banking again, so displayed figures always come from a fresh bank response.
The selected entry is moved to the active focus while the other entries remain navigable; when a
later query replaces the active focus, it is added back to history. This allows revisiting a query
after other queries or a process restart without preserving stale figures.
Prior schema-5/6 state remains readable; the graph checkpoint version advances so paused workflows
are not resumed under changed semantics.

PostgreSQL is the durable store for graph checkpoints, canonical state, turn reservations and
artifact metadata. Conversation writes use an account-scoped compare-and-set. Concurrent turns
against one version return a conflict. Repeated idempotency keys replay or resume the same turn;
reuse with a different request is rejected. A checkpoint alone is not treated as a concurrency or
idempotency mechanism.

## Runtime shape

One graph follows the smallest useful cycle:

`load state → interpret → validate/apply → execute safe operation → assess result → repeat if needed → render → persist`

Visual operations are first-class typed edits over a persisted result: changing chart type reuses
the result; changing an analytical dimension requests a new validated aggregation. The current
conversation graph slice creates and restyles bar/line charts and KPIs from the bounded active
result. Bizum preparation, own-account transfer preparation and confirmation use
`interrupt()`/`Command(resume=...)` in this same compiled graph and invoke the existing guarded
banking commands. The confirmation node reuses the
persisted bank preview, requires the account-scoped workflow id and confirmation scope, and does not
treat a pause or failure as authorization. Mixed analytics/Bizum turns share one checkpoint and one
canonical version advance. External transfers and booking still have no graph workflow.

The graph may call one model repeatedly within explicit turn budgets. It is not a multi-agent
system. Nodes are adapters to existing financial contracts, validators and renderers, not new
copies of their business rules.

## Migration sequence

1. Keep ordinary analytics turns on the graph and remove any fallback reinterpretation.
2. Keep canonical state, reservations and checkpoints in PostgreSQL; test restart, retry,
   concurrency, idempotency and schema migration together.
3. Finish chart explanation, analytical regrouping and browser-level rendering. Visual-only edits
   must retain result lineage and make zero banking reads.
4. Extend graph coverage to remaining transfer and booking flows, preserving banking authorization,
   confirmation and idempotency checks.
5. Remove legacy conversation coordination only after API and browser coverage of full
   multi-turn analytics, visuals and protected financial actions; then remove the rollout flag.

Until each capability is migrated, it must be explicitly unavailable on the graph route or handled
by a typed, non-linguistic adapter. It must never silently send the user's words to a legacy
interpreter. The graph route prepares and confirms Bizum and own-account transfers in its own
conversation checkpoint. External transfers and booking remain explicitly unavailable there. A
payment/transfer/booking workflow echoed from a
legacy session is refused without invoking either coordinator; its complete confirmation payload is
preserved on the wire so this refusal cannot discard or accidentally confirm a staged payment. Such
a session must be resolved through the same version of the application that created it.

## Consequences and verification

The LangGraph package and PostgreSQL checkpointer are runtime dependencies. Deployments selecting
the graph must provide PostgreSQL; an in-memory checkpointer is for tests only. The graph will not
be promoted by changing a flag alone. Promotion requires replaying the existing corpus plus
multi-turn API and browser conversations, inspecting figures and rendered charts, and measuring
continuity, recovery, latency, model calls, token cost and orchestration code removed.

The graph sends every turn through the configured structured interpreter. `Patch` carries only
changed fields. `CreateVisualization` and `UpdateVisualization` are typed operations over the
canonical aggregate result and active chart. The stored aggregate and chart metadata are bounded,
versioned and account-scoped. Browser verification of multi-turn payment confirmation and mixed
payment/analytics remains open.

A real Chrome smoke run on an isolated graph-enabled stack verified a category query, a bar chart,
and a line restyle after restarting the orchestrator. Category labels were observed in Spanish in
the rendered chart. The run used local Qwen 3.5 9B because Groq GPT-OSS 120B returned HTTP 429 for
the token-per-minute limit; see `docs/evaluation/2026-10-09-langgraph-browser-smoke.md`. This is
evidence for one graph and persistence path, not corpus-wide or provider-promotion evidence.
