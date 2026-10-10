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
plan, active clarification and bounded suspended clarification tasks (each with a stable opaque
task ID and its owning account scope), recent turns, a
bounded aggregate-only result artifact, and the verified chart specification/profile with result
and chart versions. At the current implementation, the canonical dialogue-state schema is version
25, the internal LangGraph turn checkpoint schema is version 22, and the conversation graph
version is `conversation/39`; these advance independently when their respective payloads or behavior
change. A resumed suspended clarification must name its task ID; the transition never selects a
draft by recency. Older dialogue payloads receive IDs through the `Pending` default when decoded.
The dialogue state and ports live in `graph_state.py`; payment, visual and shared state
access nodes live in `graph_payments.py`, `graph_visualization.py` and `graph_store.py`. `graph.py`
wires these workflow slices into the one StateGraph.

It also has typed slots for an unfinished payment draft or the exact bank-authored confirmation
preview, so a later graph turn can resume from canonical state without reconstructing payment data
from the client. Bizum preparation and confirmation now run in the same conversation graph. Mixed
analytics/Bizum turns save the analytic result and pending bank preview with one canonical version
advance. Bizum, saved-contact and user-specified-IBAN transfers, and own-account transfers use typed
pending drafts when a recipient/account or amount is missing, then the existing bank preview and
explicit-confirmation path. A typed `list_contacts` operation reads payees without dismissing an
active payment draft. Drafts survive unrelated analytics turns; ambiguous Bizum contacts remain
restricted to the bank directory candidates, and
the two kinds of draft cannot overwrite each other. Transfer drafts retain an opaque idempotency key
across clarification turns. Supplied values are grounded against the relevant account catalogue and
closed amount/contact readers before the payment command is called. A confirmation reply is reduced
to an allow-listed yes/no/unclear value before LangGraph checkpoints its resume payload. Checkpoint
state is versioned separately from the application schema. Canonical dialogue state and financial
artifacts never contain movement rows. A turn-local checkpoint may temporarily contain the bounded
bank-authored page needed to resume a movement-list read without repeating it; the checkpoint is
deleted after a successfully committed turn or best-effort when a replacement request takes over.
A failed resumable turn can retain that page until retry or takeover, and abandoned checkpoints do
not yet have a TTL reaper. The product runbook must treat checkpoint storage as financial data with
corresponding access and retention requirements.

Banking stores the exact receipt in the same database transaction that books the ledger entries and
marks an intent confirmed. Reconfirming that same intent replays the saved receipt without another
booking, allowing a graph retry after a worker crash to recover the successful result. Cancelled and
expired intents remain terminal. Confirmed intents created before this receipt-snapshot migration
retain their old refusal behavior because no exact receipt can be reconstructed safely.

The canonical dialogue state retains up to eight typed prior focuses and their account scopes. The
model can emit the closed `restore_previous` operation when the user explicitly goes back, selecting
one by its one-based position in the history shown to the model. The graph restores that typed focus
and executes it against banking again, so displayed figures always come from a fresh bank response.
The selected entry is moved to the active focus while the other entries remain navigable; when a
later query replaces the active focus, it is added back to history. This allows revisiting a query
after other queries or a process restart without preserving stale figures.
Prior schema-5 through schema-9 state remains readable; the graph version advances so paused workflows
are not resumed under changed semantics.

PostgreSQL is the durable store for graph checkpoints, canonical state, turn reservations and
artifact metadata. Conversation writes use an account-scoped compare-and-set. Concurrent turns
against one version return a conflict. Repeated idempotency keys replay or resume the same turn;
reuse with a different request is rejected. A checkpoint alone is not treated as a concurrency or
idempotency mechanism. In-flight reservations carry a five-minute lease renewed every thirty
seconds. A process crash therefore does not leave a version permanently reserved: after lease
expiry, the same idempotency key resumes from its graph checkpoint, while a new request may take
over only if the canonical conversation version has not advanced. Canonical compare-and-set and
bank command idempotency remain the final guards if a worker is paused while its lease expires.
Each lease claim also has a fresh fencing token; an old worker cannot heartbeat, release, or close
the reservation after another worker resumes it under the same public idempotency key. The request
also races graph execution against the heartbeat and cancels its graph work if ownership is lost;
it does not write late call counts or checkpoint recovery state from the superseded worker.
Migration `0006_atomic_turn_completion` closes the post-CAS response gap: the final dialogue CAS
and a bounded encoded result plus state snapshot are committed to the reservation in the same
PostgreSQL transaction. If the worker exits before normal `DONE` finalization, the same idempotency
key replays that snapshot without rerunning a model or bank operation. Neither the reservation
snapshot nor the short-lived graph checkpoint is canonical dialogue history. The checkpoint can
hold the bounded movement rows needed to resume a successful read; graph threads are deleted after
the exact response is committed. Payment workflow ids are canonical domain state, not LangGraph
thread ids; each confirmation or cancellation request gets its own checkpoint thread. Canonical
dialogue state stores only bounded aggregates and visual summaries, never raw ledger rows.

## Runtime shape

One graph follows the smallest useful cycle:

`load state → interpret → validate/apply → execute safe operation → assess result → repeat if needed → render → persist`

Visual operations are first-class typed edits over a persisted result: changing chart type reuses
the result; changing an analytical dimension requests a new validated aggregation and redraws the
active chart in the same turn. The current conversation graph slice creates and restyles bar/line
charts and KPIs from the bounded active result. Bizum draft/preparation, own-account transfer
draft/preparation and confirmation use this same compiled graph and invoke the existing guarded
banking commands. The preview is committed to canonical state before the graph interrupts; the next
user request gets a request-specific checkpoint thread and routes from that canonical preview to the
confirmation node. The stable payment workflow id is separate from the LangGraph thread id.
Confirmation still requires the account-scoped workflow and confirmation scope, and a pause or
failure is never authorization. Mixed analytics/Bizum turns share one checkpoint and one canonical
version advance. Booking still has no graph workflow.

The graph may call one model repeatedly within explicit turn budgets. Each hosted HTTP retry or
fallback attempt consumes budget; a local generation consumes one unit. It is not a multi-agent
system. Nodes are adapters to existing financial contracts, validators and renderers, not new
copies of their business rules. Comparison explanations report both increases and decreases by
merchant. Decreases are attributed only to bank-reported purchase counts and average amounts;
the large-purchase inspection remains limited to increases it can establish.

Failed generation attempts are added to the turn checkpoint before its reservation is released.
Resuming the same idempotent turn therefore continues with its remaining budget instead of
resetting the model limit on each HTTP retry. If that budget is exhausted, the graph returns a
bounded safe response without another provider request. Deterministic payment cancellation and
confirmation handling remains available independently of the planner budget.

The interpreter also owns the semantic choice between a new task and an answer to an open
clarification: `ask` starts a fresh query, while `patch(target=pending)` merges supplied fields into
the saved draft. The deterministic transition applies that choice; it does not infer a clarification
from whichever slot happened to be missing.

When graph mode is active, the standalone `/v1/payments` routes return an explicit 503 directing
clients to the unified conversation endpoint. They must not create a second payment checkpoint or
fall through to direct command execution. The legacy routes remain available only while graph mode
is off; the web Bizum and own-account transfer tabs now enter the canonical chat instead.

## Migration sequence

The repository now defaults to `CONVERSATION_ENGINE=model` and
`CONVERSATION_GRAPH=on`, so LangGraph is the normal conversational runtime. Selecting
`rules` or turning the graph off is an explicit compatibility override; provider, graph, or
banking failures in graph mode do not select the legacy interpreter. This default does not
mean the migration gates below have passed, and it is not evidence of conversation quality.
Legacy modules and routes remain until the required end-to-end coverage is reviewed. Graph
question endpoints require a caller-supplied `Idempotency-Key`; clients must reuse it when
retrying the same turn so the durable reservation and checkpoint identify one attempt.

1. Keep ordinary analytics turns on the graph and remove any fallback reinterpretation.
2. Keep canonical state, reservations and checkpoints in PostgreSQL; verify restart, retry,
   concurrency, idempotency and schema migration together, including a crash after banking books a
   payment but before the graph commits its conversation state.
3. Finish chart explanation and browser-level rendering. Analytical regrouping now patches the
   canonical query, rereads banking and redraws the active chart; visual-only edits retain result
   lineage and make zero banking reads.
4. Finish end-to-end verification of saved-contact, user-specified-IBAN and own-account transfers;
   then add booking only when its authorization, confirmation and idempotency contracts can be
   reused without a second conversational coordinator.
5. Remove legacy conversation coordination only after API and browser coverage of full
   multi-turn analytics, visuals and protected financial actions; then remove the rollout flag.

Until each capability is migrated, it must be explicitly unavailable on the graph route or handled
by a typed, non-linguistic adapter. It must never silently send the user's words to a legacy
interpreter. The graph route prepares and confirms Bizum, saved-contact transfers, user-specified-IBAN
transfers and own-account transfers in its own conversation checkpoint. Booking remains explicitly
unavailable there. A
payment/transfer/booking workflow echoed from a
legacy session is refused without invoking either coordinator; its complete confirmation payload is
preserved on the wire so this refusal cannot discard or accidentally confirm a staged payment. Such
a session must be resolved through the same version of the application that created it.

## Consequences and verification

The LangGraph package and PostgreSQL checkpointer are runtime dependencies. Deployments must
provide PostgreSQL; an in-memory checkpointer is for tests only. Graph mode is the repository
default, but that selection alone does not satisfy acceptance. Migration acceptance requires
replaying the existing corpus plus multi-turn API and browser conversations, inspecting figures
and rendered charts, and measuring continuity, recovery, latency, model calls, token cost and
orchestration code removed.

The graph sends every turn through the configured structured interpreter. Ledger-row requests use a
separate `MovementListPlan` with an optional reporting window and stable keyset pagination; a
window is never required just to list recent rows, and lists are not forced into an aggregate
`FinancialPlan`. A new list or independent ask drops a previous task's account filter,
while a clarification completion and a paginated continuation retain the scope owned by their task.
A `patch_movement_list` operation changes only supplied list fields, preserves omitted filters and
scope, and resets the cursor; `continue_current` advances the cursor without changing the query.
Relative windows retain the date reference used on the first page, so later pages cannot silently
move to a different calendar window after midnight.
`Patch` carries only changed analytical fields. `Visualize` is a typed operation over the canonical
aggregate result or active chart. The stored aggregate and chart metadata are bounded, versioned and
account-scoped. Browser verification now covers an analytics query combined with a payment preview,
then cancellation with no confirmation. Affirmative payment from the browser remains open.

A synthetic ten-turn graph acceptance sequence now exercises account balances, paginated movement
lists, account and topic changes, a category query, chart creation, an unsupported capability,
Bizum preview and cancellation. It verifies persisted versions and that cancellation never calls
bank confirmation. Its interpreter and bank are scripted fakes, so it does not establish model
quality or human usability. A separate real Gemini 3.5 Flash Lite Chrome session has now passed the
same ten-turn user flow, including visible movement rows and chart, an analytics query combined with
a Bizum preview in one versioned turn, and cancellation through the browser. A separate API smoke
also restarted the isolated orchestrator with a Bizum preview pending, then cancelled it from the
same conversation after the restart; PostgreSQL and banking stayed up. These remain authored
synthetic cases, not full corpus/generalization or human UX evaluation.

A real Chrome smoke run on an isolated graph-enabled stack verified a category query, a bar chart,
and a line restyle after restarting the orchestrator. Category labels were observed in Spanish in
the rendered chart. The run used local Qwen 3.5 9B because Groq GPT-OSS 120B returned HTTP 429 for
the token-per-minute limit; see `docs/evaluation/2026-10-09-langgraph-browser-smoke.md`. This is
evidence for one graph and persistence path, not corpus-wide or provider-promotion evidence.
