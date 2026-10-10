# FinGuard financial response contracts

## Goal

Make the conversation produce a verified financial artifact and a safe, useful view of that
artifact. The LLM decides what the holder is asking and may propose a view; it never creates bank
facts, chooses authorized accounts, or authorizes a payment. LangGraph remains the single
conversation orchestrator and the existing banking and payment ports remain the execution boundary.

The design replaces phrase-specific routing with three explicit contracts:

1. **Semantic query plan** — what financial operation is requested and what shape of answer is
   expected.
2. **Financial data artifact** — the bank-backed result, its meaning, provenance, and identity.
3. **Visual response specification** — a validated composition of renderer-owned blocks that refer
   to artifact fields, never model-authored amounts or executable chart code.

These are domain objects, not three new services. A simple turn should still need one planning model
call, one banking execution, deterministic materialization, and deterministic rendering.

## Current boundary and observed gap

`DialogueState` is already the canonical, versioned conversational state. It stores a `focus`, a
`FinancialArtifact`, an `ActiveVisual`, and bounded history. `graph.py` already owns sequencing and
keeps banking calls and model calls bounded. Payment previews still pass through the guarded command
path and require an explicit confirmation. These are foundations to preserve.

The state is not yet a general result ledger: `FinancialArtifact` currently represents bounded
verified aggregates, including explicit result kind, trusted source, and provenance. It has stable
query and artifact IDs, and `ActiveVisual` has a stable view ID. The
wire response now also carries an additive, closed `visual_response` composition whose blocks refer
to that artifact and view; it contains no financial values. Existing data fields remain the source
for the current UI while the renderer migrates block by block. `PlanFields.result_kind` distinguishes
scalar, records, ranking, breakdown, and time-series asks from their metric; the general artifact
ledger and full component renderer remain to implement.

Ordinary banking reads execute in the graph's `execute_read_only` node and the typed result is
checkpointed before presentation. A query plus a visual request can now run in one graph turn: the
graph executes one banking read, materializes the bounded result, creates the view, and commits both
under one dialogue-version increment. Query/artifact IDs derive from the conversation and committed
version; view IDs derive from the artifact and view version, so graph resumes and API rendering retain
the same identity. Graph and dialogue checkpoint schemas were bumped explicitly with a migration test.

The baseline unpaced Gemini 3.1 Flash Lite replay ran all **142/142** turns from **67** frozen threads
with **10 property violations**. Two unpaced replays after adding `result_kind` completed all turns
with **13** and **15** violations; one replay included a provider interpretation failure. They do not
establish an improvement, and the remaining findings still cluster around missing query shape/window,
inherited filters, and a plan/answer mismatch. A live Gemini smoke for the previously broken request
“desglósame mis gastos por categorías los últimos 3 meses y enséñamelo en un gráfico” returned all
nine categories plus a chart, with typed `BREAKDOWN`, query/artifact/view IDs, and one banking query.
A follow-up restyle kept the query and artifact IDs, changed the view ID, and made no banking query.
See the dated machine reports in `evaluation/engine/runs/`; the corpus harness does not yet measure
tokens or per-turn latency.

## Target contracts

The exact serialized names may evolve during implementation; the semantics below are invariants.

### 1. Semantic query plan

```text
SemanticQueryPlan
  query_id: opaque UUID
  result_kind: SCALAR | RECORDS | RANKING | BREAKDOWN | TIME_SERIES | COMPARISON
  operation: SUM | COUNT | AVERAGE | PERIOD_AVERAGE | SHARE | CHANGE
  dimensions: ordered typed dimensions (month, category, merchant, account, ...)
  filters: typed categories, merchant terms, direction, and resolved scope
  time_range: explicit resolved start/end and display timezone
  comparison: optional pair of explicit time ranges and comparison basis
  ordering: typed dimension/measure and direction
  limit: optional explicit record/group limit
```

`RECORDS` is not `COUNT`: it returns transaction records and may carry a separate `record_count`.
An omitted limit means all groups/records within the bank contract's established bounded page policy;
the model cannot silently turn “break this down” into “top one”. A comparison has two explicit
operands. Validation resolves accounts against holder permissions and resolves periods before the
bank call. User-supplied labels remain requests until grounded against bank data.

The interpreter emits this typed meaning through strict structured output. Transitions apply it to
canonical conversation state; they do not inspect the sentence a second time to infer result shape.
Visual intent can accompany a query plan, so “compare these months and show me a chart” is one
semantic turn rather than a query followed by a mandatory separate visual operation.

### 2. Financial data artifact

```text
FinancialArtifact
  artifact_id: opaque UUID
  query_id: SemanticQueryPlan.query_id
  kind: same closed result-kind vocabulary
  status: VERIFIED | PARTIAL | EMPTY
  source: banking port identifier and source query/version
  as_of: timestamp and display timezone
  dimensions: typed field metadata and granularity
  measures: typed aggregation, unit, currency, and scale
  data: bounded typed rows OR a permission-scoped data_ref
  provenance: bank result identifier(s), never LLM prose
```

Only the application materializer may create a `VERIFIED` artifact, after validating the banking
response. Amounts and counts come from that response or deterministic calculations over it. Stable
IDs identify the query and artifact; revisions create new artifacts and retain their parent ID.
Artifact access is scoped to holder, conversation, and expiry. Large movement sets are stored behind
a reference rather than copied into LangGraph checkpoints. Presentation-only changes reuse the same
artifact and make no banking call. Analytical changes create a new query and artifact.

### 3. Visual response specification

```text
VisualResponseSpec
  response_id: opaque UUID
  layout: COMPACT | FINANCIAL_ANALYSIS | TABLE | COMPARISON
  blocks: ordered closed union of
    MetricCard | MetricGroup | TimeSeriesChart | RankingChart | CompositionChart |
    ComparisonView | TransactionTable | InvestigationView | PaymentConfirmation | Insight
  each block: block_id, artifact_id(s), typed field/view, validated properties
```

The planner may select a composition from a closed component catalogue. The server verifies that
every reference resolves and that the component is suitable for the artifact: nominal categories
cannot be drawn as a temporal line; a transaction table must reference `RECORDS`; comparisons need
two operands; a metric card needs one measure. No block accepts arbitrary HTML, JavaScript,
Vega-Lite code, or free-form numeric values. The React renderer owns responsive layout, typography,
currency formatting, colors, and accessible interactions. Vega-Lite stays as the renderer for
validated chart blocks; KPI, table, comparison, evidence, and payment blocks are native components.

An insight is rendered only if its claims can be traced to artifact fields or deterministic derived
facts. A narrative that mentions an unsupported amount or causal claim is rejected or reduced to
grounded facts. The model can identify a question worth investigating, but explanations remain
bounded by returned evidence.

## Conversation and graph state

Extend the existing canonical `DialogueState`; do not add a second memory store. It owns the active
query/artifact/view IDs, selected entities, open clarification, payment workflow, and bounded history
of artifact/view IDs. IDs are references, not copied bank rows or a claim of truth. Persist contract
schema versions with each checkpoint. Migrations are explicit and monotone; compare-and-set on the
existing conversation version remains the concurrency authority. A repeated request ID must be
idempotent, and a failed checkpoint write must not expose an uncommitted artifact as active.

LangGraph responsibilities stay stable and small:

```text
load/reserve → interpret → resolve references → validate → execute → materialize artifact
                                                     ↘ clarify                 ↓
                                      assess results / bounded investigation → compose view
                                                                           → verify view
                                                                           → commit & emit
```

Only investigation uses another model iteration, with explicit per-turn model-call and bank-call
budgets. A visualization-only edit skips banking. A query plus requested visualization can be
planned together. Payment preparation and confirmation remain guarded operations; a visual action
or artifact ID can never authorize money movement.

## First vertical slice — status

The category breakdown path now interprets **“desglósame mis gastos por categorías los últimos 3
meses y enséñamelo en un gráfico”** as an unbounded `BREAKDOWN` plus a view operation. The graph
executes one authorized bank read, stores the bank-backed aggregate with stable query/artifact IDs,
builds the validated Vega-Lite chart, and commits the query and view in one version. React displays
the chart and the complete category list. A presentation-only restyle retains the query and artifact
IDs, advances the view ID, and makes no bank call.

The API now emits a value-free `VisualResponseSpec` for graph answers with artifact identities. A
category breakdown with a total composes a metric block and breakdown block; a chart adds a block
that references the stable artifact and view. The DTO is closed and its block fields cannot carry
amounts or renderer code. React now uses the composition to decide whether the chart and breakdown
belong in the response, while reading all figures from the verified response fields. It is the first
step toward the component registry, not yet a general visual renderer. Category-to-drill-down is
also pending: a click is not yet a typed operation naming the source artifact. Next slices should
cover `RECORDS` versus `COUNT`, time series, comparisons/investigation, historical views, and
payment confirmation rendering. Dialogue codec schema 17 migrates the previous `active_result` payload
to `active_artifact` in memory, inferring its result kind from the typed plan or comparison. New
checkpoints store the artifact discriminant, verified status, trusted source, and stable query
provenance; the current banking contract does not expose a separate bank result ID. Graph execution
checkpoints are versioned as `conversation/22` with state schema 20. The existing compare-and-set
version remains authoritative.

## Verification and migration

- Contract tests cover each result kind, required/forbidden fields, amount provenance, and component
  suitability.
- Graph tests cover one-call planning, query-plus-view, visual-only edits without a bank call,
  analytical edits creating a new artifact, ID continuity, checkpoint restart, version conflict,
  idempotent retries, and guarded payment pause/resume.
- Playwright currently exercises the live browser path for a category breakdown plus chart and a
  presentation-only restyle (`apps/web/e2e/category-breakdown.playwright.ts`). Desktop/mobile
  coverage, a category drill-down, and restoring an earlier view remain outstanding. Run it with
  `FINGUARD_E2E_ORCHESTRATOR_URL`, `FINGUARD_E2E_TOKEN`, and optionally
  `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH` set.
- Re-run the same frozen conversation suite and compare correctness, continuity, figure grounding,
  provider failures, latency, model/bank calls, and code dedicated to interpretation. Keep the report
  and known failures; do not count a clarification or an unexecuted turn as a correct answer.
- Retire the old aggregate-only / optional-chart response path as each vertical
  slice moves, rather than accumulating parallel legacy and new renderers.

## Out of scope

No multi-agent architecture, arbitrary model-generated chart code, direct model access to bank APIs,
new banking contracts without evidence they are needed, or payment execution without explicit user
confirmation.
