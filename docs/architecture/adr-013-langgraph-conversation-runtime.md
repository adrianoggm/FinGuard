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
and the verified chart specification/profile with result and chart versions. Its version-4 schema
also has typed slots for an unfinished payment draft or the exact bank-authored confirmation
preview, so a later graph turn can resume from canonical state without reconstructing payment data
from the client. The payment HTTP flow now stores and resumes the exact preview in canonical state;
the general conversation graph still does not own payment planning within a mixed analytics/payment
turn. Checkpoint state is versioned separately from the application schema. Movement ledgers never
enter canonical state or checkpoints.

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
result. Payment HTTP prepare/confirm routes now use a separate LangGraph pause/resume workflow and
the existing guarded banking commands. The confirmation graph reuses the persisted bank preview,
requires the account-scoped workflow id, and does not treat a pause or failure as authorization.
This is an intermediate migration step: mixed analytics/payment turns and one compiled conversation
entry point remain unimplemented. Booking still has no graph pause/resume path.

The graph may call one model repeatedly within explicit turn budgets. It is not a multi-agent
system. Nodes are adapters to existing financial contracts, validators and renderers, not new
copies of their business rules.

## Migration sequence

1. Keep ordinary analytics turns on the graph and remove any fallback reinterpretation.
2. Keep canonical state, reservations and checkpoints in PostgreSQL; test restart, retry,
   concurrency, idempotency and schema migration together.
3. Finish chart explanation, analytical regrouping and browser-level rendering. Visual-only edits
   must retain result lineage and make zero banking reads.
4. Fold payment pause/resume into the conversation graph, then migrate booking, preserving banking
   authorization, confirmation and idempotency checks.
5. Remove legacy conversation coordination only after API and browser coverage of full
   multi-turn analytics, visuals and protected financial actions.

Until each capability is migrated, it must be explicitly unavailable on the graph route or handled
by a typed, non-linguistic adapter. It must never silently send the user's words to a legacy
interpreter. The current graph route still refuses payment/transfer/booking operations; the separate
payment endpoints use the pause/resume workflow. A payment/transfer/booking workflow echoed from a
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
versioned and account-scoped. Browser-level verification and folding payment into the unified graph
remain open work.
