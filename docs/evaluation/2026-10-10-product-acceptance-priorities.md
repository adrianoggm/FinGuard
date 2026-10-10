# FinGuard conversation acceptance priorities

This captures the product review received on 10 October 2026. Progress is judged by whether a
person can complete a short, natural banking session, not by the number of supported phrases,
schemas, or passing unit tests. The existing eleven-turn acceptance session in
`apps/orchestrator/evaluation/real/conversations.yaml` is the concrete starting point; its outcomes
must be scored as a whole session and by task.

## Immediate product priorities

1. **Movements:** list recent transactions without requiring a date window, retain the selected
   account and result type when the user says “and the savings account?”, and paginate without
   duplicates when asked for more.
2. **Clarifications:** bind a question to its owning task and missing typed fields. Merge a reply
   into the existing valid draft, including when it also repeats or corrects an already-known
   field. Ask only for fields that remain missing; suspend an open draft when a new task takes the
   floor, then resume it only when the user returns to it.
3. **Topic changes and task continuity:** keep independent tasks in one conversation. A new
   restaurant query must not inherit an unrelated savings-account filter; a comparison and chart
   should refer to the appropriate prior result; asking about Bizum capability must not start a
   payment; preparing and cancelling a payment must preserve the existing confirmation guards.

## Integrated banking session acceptance

Use this as a product-level session, alongside the existing four 15-turn provider sessions. The
session is scored as one unit: a failure that strands a later task means the session did not pass,
even if earlier answers were individually correct.

| Turn | User request | Required behavior |
| --- | --- | --- |
| 1 | “¿Me dices cómo están mis cuentas?” | Return bank-backed account balances. |
| 2 | “Enséñame los últimos 20 gastos de la corriente.” | List recent movements without requiring a date window. |
| 3 | “Dame otros 10.” | Continue the Cuenta corriente list with stable pagination and no duplicate rows. |
| 4 | “¿Me los enseñas en una gráfica?” | Chart the displayed page from its retained summary; label it as that page, not the full ledger query. |
| 5 | “¿Y los de la Hucha?” | Keep the list type and expense direction, change only the account, and correctly report no expenses. |
| 6 | “¿Cuánto gasté en restaurantes el mes pasado?” | Start a new task without carrying over the Hucha account filter. |
| 7 | “Compáralo con agosto.” | Preserve the restaurant scope and compare the requested periods. |
| 8 | “Ponme una gráfica.” | Visualize the relevant prior result and render a usable chart. |
| 9 | “¿Puedes hacer Bizums?” | Explain the capability without creating a payment draft. |
| 10 | “Vale, quiero enviar 15 € a Marta Ruiz Gómez.” | Prepare a bank-authored preview for the unambiguous contact; do not execute payment. |
| 11 | “No, mejor déjalo.” | Cancel the pending payment; never call bank confirmation. |

Score the session and each user task for completion, continuity, topic changes, unnecessary
clarification, recovery, safety, and visible output. Record the graph/runtime identity and deployed
revision for every run. Scripted interpreters and bank fakes can verify orchestration invariants, but
cannot establish model quality or usability; final acceptance also needs unseen sessions and human
participants who are not told the internal operation contracts.

This scenario is an acceptance target, not evidence that the current implementation passes it.
Do not treat a turn-level corpus score, a synthetic graph test, or an isolated browser smoke as a
substitute for a complete session result.

The ordering above follows the checked-in demo seed: Laura's Hucha receives internal transfers and
interest, but no expense movements. Pagination is therefore checked on Cuenta corriente, where the
seed has enough spending rows; the Hucha turn separately checks that the list remains an expense
list and returns the correct empty result instead of misclassifying transfers or interest.

## Architectural review constraints

The product goal is a conversational banking assistant, not a phrase-to-operation classifier. The
user should be able to move among account balances, movement lists, analysis, comparisons,
visualizations, capability questions, and guarded payment preparation without learning FinGuard's
internal plans or operation names. Preserve relevant context for follow-ups and discard unrelated
filters when the user starts a new task.

Keep user intent separate from execution plans. Do not force every task through `FinancialPlan`:

- Recent movement lists need a query contract that supports no date window, ordering, limits, and
  stable pagination.
- Aggregations and comparisons need analytical contracts.
- Payment preparation and confirmation remain a guarded transaction flow.
- Visualization requests and changes should operate on a verified prior result/artifact where
  possible, without recalculating banking data unnecessarily.

Clarifications belong to an identified task. Merge a natural-language reply into that task's valid
draft, preserve known fields, accept corrections or repeated fields, and ask only for what remains
missing. A clearly new request may take the floor while the earlier task is suspended. Do not solve
these cases by adding phrase-specific rules or by making the model emit one exact field set for each
clarification. The bounded suspended-task limit must be explicit: when full, keep every existing
draft and ask the user to resume or complete one before opening another. Never silently evict an
unfinished clarification to make room.

The single LangGraph runtime coordinates distinct domain capabilities; it must not become another
monolithic coordinator. Keep bank contracts, authorization, confirmation guards, and financial
execution deterministic. Treat legacy retirement as a separate, evidence-led workstream: trace
imports, startup construction, routes, configuration, tests, and consumers; classify code as
LangGraph-active, legacy-only, shared, or dead; remove only legacy-only coordination in reviewable
increments. Do not delete shared banking queries or safety code based on file size. Check the actual
`/ready` engine/graph identity and `/version` revision before attributing a user-visible failure to
LangGraph; configuration defaults and repository files alone do not prove which runtime served it.

The attached review also reported that, on the repository revision it inspected, the default
configuration selected `rules` with the graph off, legacy modules remained present, and the cited
evaluation had not demonstrated complete-session acceptance. These are historical audit findings,
not claims about the current checkout. Re-audit the current startup path and deployed identity before
relying on them. File sizes quoted in that review are not removable-code estimates.

Until the integrated session succeeds, prioritize closing the domain and continuity gaps it exposes
over adding unrelated capabilities. A session that strands a task, asks for information already
available, loses its intended scope, renders no useful result, or violates a payment guard fails as
a session even if most individual turns look correct.

An explicitly stated direction and period is a complete new query even when the category is
omitted; it must not inherit an unrelated merchant or category filter. A patch is for a turn the
user relates to the visible task, and should preserve only fields that task owns. The model's
`ask` versus `patch(target=pending)` operation is the semantic choice; the transition layer applies
it and must not rewrite one into the other based on which field is missing.

These are contract and state-model problems. Do not address them by accumulating phrase-specific
rules or making every intent fit an analytical `FinancialPlan`. Movement lists, aggregations,
payments, and visualization updates have distinct execution contracts coordinated by the single
LangGraph runtime.

## Evidence required before declaring progress

- Run complete sessions of roughly 5–15 turns, preserving returned state between turns. Report
  success per session and per task; isolated turn accuracy is secondary.
- Score continuity, unnecessary clarification, topic changes, recovery, financial safety, and
  rendered visual output. A valid JSON operation is not a product success if the task is unresolved
  or the conversation loops.
- Add unseen sessions and human improvisation for final generalization; keep them out of prompts
  and implementation examples.
- Attribute each result to `/ready` and `/version`. A partial run caused by provider quota is an
  availability failure, not a product-quality result.
- For browser-visible acceptance, also record the loaded web bundle fingerprint from
  `document.documentElement.dataset.finguardWebSource`; backend `/version` alone cannot establish
  which renderer displayed a conversation. It fingerprints the web source snapshot at Vite build
  or startup, so restart the dev server before an attributable run after source edits. The Playwright
  banking-session run attaches this value together with `/ready` and `/version` to its JSON report;
  reject a run that was served by a non-authoritative or unexpected conversation runtime. The
  report also includes a minimized JSON attachment for every completed turn and each chart's
  selected planner source, fallback reason, and verified spec, so a visible chart cannot be
  mistaken for evidence that the LLM chose it. The turn attachments exclude transaction values
  and movement details. The current source configures the planner in `hybrid` mode: an LLM
  proposal is verified before display, with the deterministic planner as the explicit fallback.
  The report defaults to `apps/web/test-results/report.json` and can be redirected with
  `FINGUARD_E2E_REPORT`.
- Record logical calls separately from actual HTTP attempts, and report provider-supplied prompt
  and completion tokens only when usage metadata is present.
- Count each hosted retry/failover attempt against LangGraph's bounded per-turn generation budget;
  a provider outage must not evade the graph's reasoning limits.
- Audit imports, construction paths, routes, tests, and configuration before deleting legacy
  coordination. Remove it in reviewable increments only after graph coverage and migration
  criteria pass. Preserve shared banking, security, and presentation code.
- Keep product validation and legacy retirement as related but separately evidenced workstreams:
  first classify each legacy component as graph-active, legacy-only, shared, or dead by tracing
  actual imports, construction paths, routes, and consumers; then remove legacy-only coordination
  in reviewable increments once the graph session and safety criteria are met. File size alone is
  never an estimate of safely removable code.
- Keep `graph.py` modular by responsibility; LangGraph must remove bespoke coordination logic, not
  merely relocate it into graph nodes.
- Prefer a small set of complete, natural sessions (about 5–15 turns each) over large collections of
  disconnected one- and two-turn prompts. Evaluate the session as a whole and retain per-task
  outcomes so one stranded task cannot be hidden by many easy turns.
- Freeze unrelated feature expansion while fixing a demonstrated end-to-end session gap. After the
  acceptance session passes, use unseen sessions to check that the fix generalizes rather than
  adding the accepted conversation to prompts.

The next provider-limited run should use the existing four 15-turn sessions and the same captured
ledger snapshot where intentionally accepted. The Gemini Live models are excluded; structured
operation planning requires the Flash models.

## Current-checkout implementation evidence (not acceptance evidence)

Static inspection of the current worktree confirms these implementation pieces are present:

- Periodless movement lists use a dedicated `ListMovements` operation and `MovementListPlan`,
  separate from analytical `FinancialPlan` queries. The Banking API adapter and repository carry
  a keyset cursor, and `PatchMovementList` preserves omitted filters while resetting the cursor
  when a filter changes. Optional relative periods are pinned to the turn's reference date so a
  page continuation across midnight cannot shift the result window. An unspecified direction now
  means all movement types; the interpreter narrows to expenses or income only when the user asks.
- Clarifications have a task id, saved draft and captured account scope. The canonical dialogue
  state can suspend bounded pending tasks and requires an explicit task id to resume one.
- Moving a suspended draft back to the active slot cannot trim the suspended-task list to make
  room. If capacity is unavailable, the transition keeps the original state and asks the user to
  complete, resume, or cancel a task first.
- A clarification that appears despite an active, pending, or suspended task receives one bounded
  same-schema semantic review. When the provider budget is exhausted or the review cannot correct
  an ungrounded filter, the graph stops before Banking. This is source-level behavior; its effect
  on unnecessary clarification rates remains unverified.
- Account-filter grounding accepts a distinctive partial display name only when it matches exactly
  one account in the holder's catalogue (for example, “corriente” for “Cuenta corriente”). Generic
  fragments do not select scope. This aligns the pre-bank semantic guard with the domain account
  resolver; it still needs the integrated movement-list session to confirm “la corriente” works.
- Answered focus history now also carries an opaque task id. The interpreter can select a named
  previous task with `resume_focus`; the domain removes that exact entry, returns the current focus
  to history, and re-executes the selected query under the same account/version guards. Relative
  `restore_previous=N` remains available for genuinely ordinal requests. Existing serialized
  history entries derive a deterministic id from versioned canonical JSON of their focus and
  account scope when decoded, so a checkpoint resumed after restart sees the same target even if
  Python's dataclass display representation changes. This is source-level continuity support, not
  evidence that the model identifies the right historical task in a live conversation.
- The operation interpreter now makes at most one same-schema semantic review when a valid
  operation introduces category, account, merchant, or person filters absent from both the current
  words and the owning task. This is a trigger for the LLM to reconsider task ownership and scope,
  not an application-side rewrite of intent. If the review cannot run, the ungrounded operation is
  replaced with a clarification before any banking read; after review, unresolved ungrounded scope
  is also stopped before reaching Banking. This addresses a class of observed
  cross-task filter leakage; it remains source-level evidence until a complete session shows the
  model correcting the operation without creating unnecessary questions. If an unreadable first
  answer already consumed the one repair allowed for a turn, unresolved ungrounded scope is
  clarified instead of bypassing the review and reaching Banking.
  A comparison with two explicit periods and a stated query is classified as a fresh task by the
  same boundary as the domain transition, so filters from the prior focus trigger review instead
  of being mistaken for inherited context. One-sided comparisons remain follow-ups and retain their
  owning task's compatible filters. This targets the recorded Marta filter leaking into a new
  restaurant comparison; it still requires a complete session run. The grounding check now reads
  only the specific owner selected by the typed operation: active focus, current pending draft, or
  the named suspended draft. It no longer unions those tasks' filters and treats them as globally
  reusable context. Category aliases in those owned drafts are normalized to category keys before
  comparison, so “supermercado” and `GROCERIES` do not look like two different filters. The
  grounding check recognizes the same full category vocabulary exposed to the planner, while the
  planner remains responsible for choosing among aliases that can name more than one category.
  When a follow-up names a new category, the review also rejects an operation that carries both
  that category and an unmentioned category already on the task; that combination broadens the
  filter instead of replacing the user's subject.
- A complete financial question that the model emits as a `patch` over a filtered focus now gets
  one same-schema semantic review before execution. The model decides whether it is a new task or a
  continuation; if the review is unavailable or still proposes that risky patch, FinGuard asks
  before reading the ledger. This specifically targets the 4×15 session where “gastos de julio”
  retained supermarket and Mercadona, followed by “solo gasolina” retaining Mercadona. It remains
  unverified until the same complete sessions can be replayed with attributable source identity.
- The semantic review also checks category/merchant collisions: if `merchant_terms` repeats a
  precise alias for a category selected by the same or owning task, the model must decide whether a
  distinct shop was actually named. If that review is unavailable, unreadable, or leaves the same
  collision unresolved, the ambiguous operation becomes a clarification instead of querying a
  misleading literal merchant filter. This
  specifically targets the recorded “super” filter leakage; it still needs a provider-backed session.
  A patch to one side of a comparison checks the category already owned by that exact side, so
  merchant terms cannot bypass the collision review merely because the other side is untouched.
  The hard collision check now requires the whole merchant term to equal the category alias;
  longer specific names such as “Supermercado Alfa” remain eligible for the semantic review and
  are not rejected just because they contain that word. It also excludes category “kinds” such as
  “gimnasio”/“gym” within SUBSCRIPTIONS, which correctly narrows the query to gym merchants.
  The shared planner prompt now resolves its earlier conflicting guidance explicitly: category
  aliases such as “super” map to the category, while `merchant_terms` requires a separately named
  shop or merchant. This removes the prompt ambiguity that could reintroduce the collision, but does
  not establish model compliance without the deferred session run.
- The shared operation prompt distinguishes omitted patch fields (retain) from empty lists
  (clear). A bare regrouping patches `group_by`; the transition reconciles the result shape with
  that dimension, preserves the active metric and query scope, and clears an inherited top-N that
  conflicts with a calendar series. The model still chooses the regrouping; deterministic code
  enforces the domain invariant. This is source-level support, not evidence that a hosted model
  follows it; complete provider-backed session acceptance is still required.
- Clearing top-N on a grouped ranking is an explicit `clear_limit` operation. The transition now
  changes the compatible result shape from `RANKING` to `BREAKDOWN` for category, merchant,
  counterparty, and account groupings, while preserving the period, direction, metric, filters, and
  account scope. This fixes the domain transition for “show all categories”; it still needs a
  provider-backed session to prove the model selects the operation. Static tracing confirms the
  presenter slices groups only when `group_limit` is set, the web view renders every returned
  group, and Banking API applies its bounded `MAX_GROUPS` ceiling rather than an inherited top one.
- The graph stores verified aggregate artifacts for chart operations and the web renderer displays
  bank-returned movement rows. A movement-list turn also stores a bounded chart-ready summary of
  that displayed page (grouped amount and category/merchant labels only; no transaction IDs or
  descriptions), linked to the canonical result version. The graph now accepts a movement-list
  operation composed with visualization in one turn, using that same fetched page for the view. A
  later chart request can also reuse the displayed page without another bank read; either view must
  describe that page, not the full ledger query. Mixed-currency or ungroupable pages do not get a
  chart artifact. The same-turn chart composition accepts both named-task resumption and relative
  `restore_previous`, re-executes the selected query and builds the view from its bank result. This
  is source-level implementation evidence, not verified rendering or continuity.
  Scalar comparisons now retain
  both bank-derived values, while
  grouped comparisons use the requested count, amount, or average per group and the actual grouping
  dimension. Drilling into visual results remains separate work.
- Graph routing is configured as the normal runtime, and the HTTP dispatch path fails closed if
  its authoritative handler is missing or declines a turn. The legacy route remains an explicit
  compatibility configuration pending the separate migration audit.
- Bizum preparation now snapshots the bank-resolved contact name and phone into the immutable
  payment intent. The preview and confirmation use that same destination; confirmation no longer
  joins the mutable contact row. Existing pending intents without this snapshot fail closed and
  require a new preview, with a distinct safe refusal so the user is not told to check for a
  payment that could not have happened. Migration `0021_bizum_recipient_snapshot` adds the field
  and constraint. This is source-level payment safety evidence only; migration, preview,
  contact-edit and replay behavior still require the deferred runtime verification.
- Failed graph turns retain their checkpoint for seven days so the same idempotency key can resume
  without repeating model or bank work. Older reservations are fenced as abandoned before their
  turn-local checkpoint is deleted; that same key receives 410, while a new key can continue from
  canonical dialogue state. Cleanup runs when that conversation is used again, not from a global
  scheduler. This is implemented in reservation migration `0008` but still needs database
  concurrency and retention verification.

These findings describe code paths only. They do not prove that an LLM completes the acceptance
session, that a deployed container is running this worktree, or that the visual behavior is usable.
Do not upgrade them to product claims without an authorized end-to-end run tied to `/ready` and
`/version`.

## Follow-up review: implementation must close the domain gap

The later review reinforces that adding a field such as `result_kind` is not sufficient if the
underlying query contract still requires an analytical period, rejects limits for records, or
cannot execute stable pagination. Judge a capability end to end: semantic intent, a suitable typed
plan, banking adapter support, state continuity, and useful rendering must all exist. Keep movement
lists, analytical aggregates, payment flows, and visualization changes as distinct execution
contracts coordinated by the graph.

For clarification, do not require the model to return one exact set of fields. The owning task
should retain its valid draft; a reply can add a missing value while repeating or correcting known
ones. A new goal can suspend that task without corrupting its draft. Visual work also remains
unfinished until verified results can drive the appropriate useful view and the user can act on
that view; metric cards alone do not establish a general visualization experience.

The review observed substantial legacy modules alongside a growing `graph.py`. Treat those as
audit prompts, not current-checkout measurements: trace actual consumers and runtime paths before
removal, keep shared banking and safety code, and split graph responsibilities before the graph
becomes a replacement monolith. Prioritize complete-session gaps over adding unrelated features;
do not claim success from isolated operation tests or turn-level scores.

## Preserved review comments and decision guardrails

The attached review makes two structural concerns explicit and they remain part of the active
product direction:

1. **The system must work as a natural banking session.** Evaluate short complete sessions (about
   5–15 turns), by session and task, including topic changes, corrections, continuity, recovery,
   visible output, and safety. Do not optimize for isolated phrases or treat valid operation JSON as
   task completion. Freeze unrelated feature expansion while a demonstrated session gap remains.
2. **The legacy runtime is still present and must be audited, not guessed away.** The review's
   `main`-branch startup/default findings and file-size figures refer to the revision it inspected;
   they are not assertions about this checkout. Trace actual imports, startup construction, routes,
   flags, tests, and consumers. Classify each module as graph-active, legacy-only, shared, or dead.
   Retire only proven legacy-only coordination in reviewable changes after migration and safety
   evidence; preserve shared banking APIs and safeguards.

The review also records a concrete domain-contract challenge: at the inspected revision, a recent
movement request such as “mis últimos 20 gastos” could not be represented end to end because the
record path still required a period and did not support its own limit/pagination contract. Treat
that as a historical finding to verify against the current source, not as a current implementation
claim. A field or schema addition is not a completed capability unless the banking adapter,
pagination/state behavior, and renderer support it too.

Clarification handling must merge natural replies into the owning task's valid draft, including
repeated or corrected fields; ask only for genuinely missing information; and let a new task suspend
the draft. Keep intent distinct from execution: movement lists, analytical queries, payments, and
visualization changes have separate domain contracts coordinated by the single LangGraph runtime.
Charting or changing a view should use the relevant verified result when available instead of
needlessly repeating the bank query. A capability question about Bizum must not initiate payment;
preparation and cancellation must retain their confirmation and no-execution guarantees.

The review cites non-controlled historical scores (10, 13, 15, and 14 failing properties out of 142
turns across successive contract/prompt variants). They show no demonstrated improvement, but are
not a controlled estimate of current quality. Likewise, its reported legacy and graph file sizes are
not deletion estimates. Use these observations to guide audits and acceptance design, never as
evidence that the current checkout passes or fails without matching source revision and runtime
identity.
