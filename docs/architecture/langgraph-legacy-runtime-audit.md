# LangGraph and legacy conversation runtime audit

**Status:** static source audit, 10 October 2026. This is not runtime evidence and does not authorize
removing the compatibility path.

## Current construction modes

| Settings | Constructed conversation path | Persistent state |
|---|---|---|
| `conversation_engine=rules`, `conversation_graph=off` | `AskFinancialQuestionHandler` | The legacy handler/query path |
| `conversation_engine=model`, `conversation_graph=off` | `AnswerTurnHandler` with the structured operation interpreter | `MemoryDialogueStore`; process restart loses turns |
| `conversation_engine=model`, `conversation_graph=on` | `graph_engine`; the two handlers above are not constructed | PostgreSQL canonical dialogue store, turn reservations, and LangGraph checkpoints |

The current source defaults select `model` with the graph on. `.env.defaults` does not set either
engine-selection option; ignored `.env` values or the process environment can still override the
code defaults. `rules` or graph-off is an explicit compatibility override. This makes LangGraph the
default construction path in the checkout; it does not prove which configuration or source revision
a running container uses, and it does not mean the product acceptance gates have passed. A graph-off
process now emits a startup warning identifying the selected compatibility engine so the legacy
path is visible in deployment logs.

## Request routing and fallback boundary

Investigation evidence reconstruction and bank-grounded explanation rendering live in
`application/graph/graph_analysis.py`. Counted adapters for banking reads live in
`application/graph/graph_execution.py`; pure plan-trace and artifact conversions live in
`application/graph/graph_artifacts.py`. `graph.py` retains StateGraph node orchestration and the
bounded read loop. These modules separate result interpretation, execution accounting and result
construction from routing without changing the checkpoint contract.
The maximum of three financial reads is now checked inside the counted query and balance adapters,
so executor fan-out (such as one ranked movement per banking call) cannot bypass the graph-node
budget check.

- The composition root mounts exactly one question-route owner:
  `graph_question_controller.py` when graph mode is on, otherwise the legacy question router from
  `llm_controller.py`. The graph controller dispatches only to the authoritative engine and returns
  503 if it cannot answer; it has no route into the legacy interpreter.
- Process/catalogue/message routes and deterministic account/card/contact screens now live in
  separate controllers. `llm_controller.py` is imported only inside the non-graph compatibility
  branch. The application and infrastructure package facades resolve legacy planner/reader exports
  lazily, and `main.py` constructs the old question/payment handlers only in that same branch.
  Static imports therefore show the graph startup path no longer imports the old linguistic
  coordinator. Graph startup now refuses a non-authoritative graph factory result or any legacy
  `ask_handler`, and `/ready` requires both an authoritative `app.state.engine` and no legacy
  handler before reporting `active_runtime=langgraph`. In graph mode, `/ready` also probes that
  PostgreSQL is reachable and that the application role can resolve and read the canonical state,
  turn reservation, and LangGraph checkpoint tables using zero-row queries, with a two-second
  bound; it reports unavailable persistence instead of treating an in-memory graph object as proof
  that durable conversations can be served. This goes beyond echoing the environment setting, but
  an observed running process and route trace are still needed to prove that the deployed process
  matches this source.
- `engine_dispatch._the_engine` returns 503 when model or graph mode is configured but its handler
  was not built, and returns 503 if graph mode is configured with a non-authoritative handler. A
  missing or mismatched structured-operation runtime is not permission to use the old handler.
- `engine_dispatch.refuse_legacy_fallback` returns 503 if an authoritative graph returns no answer.
  Graph delegations are converted into explicit graph responses before that guard.
- Within the graph, `Delegate("CHART")` is routed to the native visualization node; it does not
  reach the legacy chart adapter. That node consumes the canonical verified artifact and persists
  the chart view with its own version. The similarly worded “charts unavailable” response in the
  turn adapter is not the graph chart path.
- A movement-list query keeps its next-page cursor and a bounded chart-ready page summary in
  canonical dialogue state; that summary contains only grouped amounts and category/merchant
  labels, carries the result version, and describes only the displayed page. A turn-local graph
  checkpoint also temporarily contains the bounded bank-authored page while the turn is in
  progress or resumable, so retry can continue without repeating the read. Successful completion
  deletes that checkpoint, and a replacement request that takes over the lease attempts to remove
  its predecessor's checkpoint. Because a delayed worker could still finish a checkpoint write,
  takeover cleanup is best effort. A stale `NEW` or `RESUME` reservation is now fenced as
  `ABANDONED` after seven days and its checkpoint is deleted the next time that conversation is
  used; the old request receives 410 and a new idempotency key can continue from canonical state.
  Cleanup is lazy per conversation rather than a global scheduled reaper, so an inactive
  conversation's checkpoint may remain until it is used again. Mixed-currency or ungroupable pages
  have no chart artifact and receive an explicit limitation response. These are source-level
  claims; concurrency and data-retention behavior still need authorized runtime verification.
- A workflow echoed from an older rules session is explicitly refused in authoritative graph mode;
  it is not sent back through the old interpreter or payment handler.
- The compatibility controller and its readers remain in the repository and are reachable through
  the explicit non-graph branch. Static imports show they are not loaded by graph-mode composition;
  this source audit does not prove the running process matches that configuration. A complete
  consumer audit and runtime trace are still required before deletion.

## Dependency inventory

| Component | Graph mode | Removal classification |
|---|---|---|
| `AskFinancialQuestionHandler`, `LlmQueryPlanner` and legacy payment reader | Imported and constructed only in the non-graph compatibility branch; absent from graph route registration | Legacy-only; remove after session and payment migration gates |
| `AnswerTurnHandler` and `LlmTurnOperations` | `AnswerTurnHandler` is not constructed; the typed operation interpreter is reused by `graph_engine` | Shared interpreter; do not remove |
| `engine_dispatch` and current question HTTP routes | Executed to enter the graph and enforce fail-closed behavior | Keep during migration; later simplify to graph-only routing |
| Banking adapters, account/contact directory, guarded command ports | Constructed and injected into graph wiring | Shared domain execution and safety; keep |
| Visual presenter/renderer and typed result/artifact contracts | Used by graph presentation and web rendering | Shared product capability; keep |
| Legacy rules, lexical readers, old tests and fixtures | Not required by graph construction, but imported by legacy path/tests | Candidates only after a repository-wide consumer audit |

This table is based on construction and import sites, not file size. `application/queries` is not a
safe deletion unit: it contains conventional query handlers and shared result/port contracts as
well as conversational coordination.

## Evidence that still blocks retirement

1. The latest completed four-session, 60-turn report is
   `apps/orchestrator/evaluation/real/2026-10-10-48f18b4c-long-sessions-4x15.md`. It achieved
   **1/4 sessions** (the first); sessions 2–4 still lost or contaminated context. It could not
   check any of the 60 figures because the runner found no `last_plan` in graph state. The
   report's product-code identity is `unknown`, so this is useful failure evidence but cannot be
   attributed conclusively to the current source. A later mixed-model replay,
   `2026-10-10-48f18b4c-gemini36-4x15.md`, achieved 0/4 with 47 failed turns, 12 valid responses
   and one chart awaiting browser verification; it switched manually among Gemini 3.6, 3.7 and
   3.8 and is not a per-model comparison. The subsequent `postfix` replay returned HTTP 429 on
   59/60 turns. These reports must not be collapsed into a semantic score: provider availability,
   missing figure-grounding data, and runtime identity each limit what they prove.
   The current runner now accepts the response's typed `plan_context` as a read-only trace when
   graph-mode wire state omits `last_plan`, while refusing checks whose account scope or filters
   cannot be represented. This source change does not retroactively verify those 60 figures; a
   new attributable session run is still required.
2. A separate real Chrome run is documented in
   `docs/evaluation/2026-10-10-langgraph-gemini-corpus.md`: one ten-turn Gemini 3.5 Flash Lite
   banking session passed its scripted goals, including account changes, a chart, a bank-authored
   Bizum preview and cancellation without confirmation. A restart smoke also resumed and cancelled
   a pending preview. `/ready` reported graph mode and `/version` reported
   `48f18b4c-dirty-session-acceptance-v2`. This is positive, attributable evidence for that one
   authored session and restart case; it does not establish unseen-session quality, human usability,
   broad recovery, or safe legacy retirement. The tested account had no Hucha movements, so that
   branch only established an empty list, not switching between populated accounts.
3. The earlier paced Gemini 3.8 Flash run stopped after four of 60 turns; all four returned
   `PROVIDER_ERROR`/HTTP 429. The configured five-second interval did not make that run usable, and
   the provider response did not identify which quota was exhausted. This is availability
   evidence only, not a continuity score or proof that the limiter is ineffective for every quota.
   The current default now reserves one provider-wide slot every 12 seconds (at most 5 requests per
   minute), including retries and model failovers, because the configured candidates can have a
   5-RPM tier. This is a conservative implementation change, not verified quota avoidance; other
   clients sharing the project and daily/token quotas remain outside this pacer's control.
   The runner directs operators to `just up` to stamp source identity; that recipe was missing from
   the `justfile` at the time of this audit. It now aliases the existing `back` startup recipe.
4. A complete graph promotion run still needs attributable `/ready` (including its constructed
   runtime check), `/version` and its source fingerprint, turn and
   session/task scoring, SQL figure checks, recovery, rendered browser evidence, and protected
   payment cancellation. Request/model-attempt headers and the graph financial-read header are
   intended to improve attribution and prove that visualization reused stored results. The latter
   counts query and balance ports, not account/contact catalogue reads.
5. Before deleting legacy code, complete the import/call-site inventory, remove the legacy
   constructors and rollout flags in separate changes, and run the full API/browser workflow gates.
   Any leftover route must fail closed.

The reservation implementation now adds a five-minute PostgreSQL lease, a thirty-second heartbeat,
and a fresh fencing token per claim (`0005_turn_reservation_leases`). This closes the permanent
`NEW` reservation left by a crashed process and prevents a superseded claim from closing a later
claim. Graph execution now races the heartbeat and is cancelled when ownership is lost. It is
source-level implementation only until the migration, restart, same-key resume, concurrent
takeover and payment idempotency paths are exercised against PostgreSQL and banking.

The post-CAS recovery window now has a source-level fix: migration `0006_atomic_turn_completion`
adds a bounded response/state snapshot to the idempotency reservation, and `PostgresDialogueStore`
writes it in the same transaction as the canonical CAS. `GraphTurnHandler` replays that committed
snapshot if the process exits before normal reservation finalization. The result snapshot lives in
the turn reservation, while canonical dialogue state contains no movement rows; the separate
turn-local LangGraph checkpoint can hold a bounded bank-authored page until completion or
takeover. Migration `0008_abandoned_checkpoint_retention` adds a seven-day retention boundary for
failed reservations, fences stale same-key retries with HTTP 410, and retries checkpoint deletion
for that conversation before granting new work. Cleanup is lazy when a conversation is used; no
global scheduler or cross-account scan is added. This is source-level implementation and still
requires PostgreSQL concurrency, retry, and retention verification before claiming recovery is
proven.

When a client names a conversation whose canonical row is missing, `GraphTurnHandler` now retains
that supplied identity and compares the supplied version with the empty version-zero state. It
does not silently create a separate request-derived conversation or accept a stale version as a
new thread. This is a source-level fail-closed correction; verify the stale response and same-key
recovery behavior against PostgreSQL before treating missing-row handling as proven.

The PostgreSQL dialogue store previously accepted schemas 4–9 and 13 onward while rejecting 10–12,
even though `codec.loads_state` migrates those schemas and the 2026-10-09 browser run persisted
schema 10. Its allowlist now accepts every schema from 4 through the current codec version. This
restores source-level compatibility with known persisted conversations; restart/resume against
actual schema-10/12 rows is still required before claiming the migration verified.

The bank-side crash window after a successful payment confirmation also now has a source-level
recovery path. Migration `0020_replay_confirmed_payment_receipt` stores the exact receipt atomically
with the ledger booking and terminal intent update; confirming that same intent again replays the
stored receipt without booking another transaction. Old confirmed intents without a stored receipt
remain refusals. The database transition trigger now refuses a new transition to `CONFIRMED` unless
the same write includes that receipt, closing the gap where another writer could have created an
unreplayable new confirmation. This path still requires PostgreSQL failure-injection and end-to-end
verification before it can be treated as a proven payment-recovery guarantee.

Replacing a bank-authored payment preview has a second cross-service recovery boundary: the old
intent is cancelled before the replacement intent is prepared and saved to dialogue state. Bank
cancellation now declares the account required by `payment_intents_own_account` RLS, and repeating
an already completed cancellation is a no-op. The replacement prepare keeps the graph turn's
idempotency key, so resuming that same turn can continue without creating another intent. This is
source-level recovery only; inject a process failure after the cancellation commits and before the
replacement state CAS, then verify same-key resume, one live intent and an unchanged ledger before
counting the path as proven.

The operation interpreter now makes one bounded same-schema semantic review when its first valid
operation is `Clarify` and canonical state already has an active or pending task. The second result
may confirm the clarification or choose a typed `ask`/`patch`; application code does not rewrite the
first operation. If the review hits a provider error or returns unreadable output, the original safe
clarification is kept. It is charged as a normal model generation under the existing per-turn
budget, and logs record only operation kinds, missing-field enums, draft field names and patch
targets. This is a source-level recovery measure, not evidence that a hosted model corrects its
semantics; the next session run should compare clarification rate, continuity, calls and provider
availability with this behavior enabled.

Failed-generation attempts are now added to the graph checkpoint during turn cleanup, alongside
bank reads that completed before a node failed. The checkpoint baseline prevents attempts already
recorded by successful nodes from being counted twice. A same-key resume starts with that saved
budget and returns a safe bounded response once it is exhausted; the provider's outer HTTP-attempt
cap remains an additional guard. Deterministic payment cancellation and confirmation do not depend
on planner budget. This closes a source-level retry-budget reset path, but provider-failure recovery
still needs an attributable resume run against PostgreSQL.

Migration `0007_replay_financial_read_count` carries the query/balance read count with the atomic
committed result so an idempotent replay reports the same financial-read header as the original
turn. Historical committed rows remain `NULL` and must be classified as unverified by evaluation,
not as zero reads. This accounting supports the “reuse the prior result without another bank read”
visual invariant; it is not itself proof that a deployed visualization honored the invariant.

## Removal sequence

1. Keep the graph as the only authoritative coordinator; preserve explicit 503 behavior and never
   reinterpret a graph-owned turn through the old language path.
2. Close full-session, visual, persistence/restart, concurrency/idempotency, recovery and payment
   gates against a build identified by `/version`.
3. Extract the graph HTTP routes from `llm_controller.py`; retain only shared authentication,
   banking, rendering and domain execution services.
4. Remove legacy constructors, adapters and linguistic readers in dependency order. Delete only
   modules proven to have no remaining conventional query or shared-service consumers.
5. Remove rollout settings only after graph-only startup and route behavior are established, then
   measure production orchestration code removed versus graph-specific code added.
