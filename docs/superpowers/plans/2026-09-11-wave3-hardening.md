# Wave 3 Hardening Implementation Plan

> Committed 2026-09-11 on `task/wave3-hardening`, cut from `task/wave3 @ d88324c`. Source: the three read-only audits
> of 2026-09-11 (`.superpowers/audits/2026-09-11-wave3-audit-{design,code,behaviour,synthesis}.md`, ids A-N / C-N /
> B-N / G-N / W-N / M-N) plus the user's own confirmed findings (U-1..U-9 below). The user's word: «métele caña a esos
> errores sumados a estos». Standing authorisation (2026-09-05) covers execution without check-ins; push only on the
> user's explicit word; no merge into `develop`.

**Goal:** every Critical and Important finding fixed or honestly refused in writing; the user's nine findings fixed;
minors folded where the same file is open. The corpus learns the natural sentences it was kept away from. The record
(design, `docs/next.md`, capability matrix, RESULTS) says only what is measured.

## The user's findings (U-N)
- **U-1 (high, confirmed)** «desapílalo» builds `stack=None` (Vega-Lite decides — and stacks) instead of `stack="disabled"`; the verifier then refuses a representable change (`visual_interpreter.py:~132-177`).
- **U-2 (high, confirmed)** RECURRENCE's explanation says «número de movimientos» but its points plot `value_minor` (an amount) — and `test_visual_explanation.py` pins the wrong text.
- **U-3 (high, confirmed)** `_MEASURE_WORDS` lacks `COUNT` (and AVERAGE/PERIOD_AVERAGE); fallback «importe»; `test_measure_metric_values_found_map_to_a_word` walks only `FAMILY_DATASETS`, not the real producers (= A-7/C-2).
- **U-4 (medium-high, confirmed)** `conversation_mapper.to_domain` zeroes `plan_metric`/`row_count`; the explainer now reads `measure_metric()` on later turns — COMPARISON of COUNTs explains as «importe» even after U-3 (= C-5/A-6).
- **U-5 (medium, confirmed)** WEEK buckets are Mondays as ISO dates → «días»; the sealed context lacks `group_by` (= D-4.5-3 re-opened as a design gap).
- **U-6 (medium, confirmed)** bar → donut → bar drops the dimension's `EncodingSort` (the donut rebuild copies no `sort`), so the second restyle is refused by the `order` rule.
- **U-7 (medium, confirmed)** a `VisualAnswer` with `mark=null` plus non-empty encodings/stack/sort is counted as a clean abstention; no conditional validation.
- **U-8 (candidate)** `VisualPlanningPolicy`'s broad `except Exception` files code bugs as `provider_error`.
- **U-9 (state)** `evaluation/planning/RESULTS.md` (28 Aug) shows held-out unsafe 11.4 % vs a ≤ 10 % bar = FAIL, and HEAD has no newer measurement; nothing in the repo proves the gate holds today.

## Global Constraints
- Three apps, each with its own gates (orchestrator: ruff/format/mypy + four foreground pytest calls with `timeout: 600000`; gateway: ruff/format/mypy/pytest; web: vitest + typecheck). Baselines at BASE `d88324c`: orchestrator 4233 / 3 / 209 + corpus 207 (176 · 1194) at 0.0 %; gateway 122 / 1; web 118; ratchets UNMEASURED 22 / MUTE 43 / CONTESTED 0 — **this wave is allowed to move the ratchets DOWN and the controller lowers the literals** (Task 8 owns the corpus growth; a ceiling literal changes only in a commit that shows the measured number below it).
- `domain/` imports nothing from `application/`/`infrastructure/`; Spanish user-visible text; deterministic tests only; no real model; atomic commits, subject-only messages, no trailers; no push.
- Existing corpus episodes change only where a task names the turn; new episodes are the way to add sentences.
- Every «wrong-but-confident» answer becomes either a right answer or an honest ask/refusal — never silence.

## Tasks (owner files → parallelism: T1 ∥ T5 ∥ T6; then T2 ∥ T7; then T3 → T4 (both edit `visual_interpreter.py`); then T8; T9 last)

### Task 1 — The seal binds the conversation and the order (A-1, A-13)
`infrastructure/security/metric_tokens.py` `seal_context`/`verify_context`: the digest covers holder + conversation id + the context's position (or the previous chart's id as a chain) + content; `conversation_mapper` seals/verifies with the conversation's id and index; a replayed, reordered, duplicated or dropped context → untrusted (and the answer's `VISUAL_UNTRUSTED` text says a chart «no es de esta conversación» when the holder matched but the conversation did not). Dev key: `create_app()` logs loudly and the README/`docs/next.md` state that the loopback default is not a trust boundary (A-13). Tests: the four tamper shapes; an old sealed payload (pre-change) → untrusted, not a 500. Design §4/§13 amended (one paragraph).

### Task 2 — What crosses the wire is what the readers need (A-2, U-4, C-5, A-6, A-11, A-4, A-5, U-5)
`VisualContextPayload`/`to_domain`/`to_payload`: carry `plan_metric`, `row_count`, per-field `cardinality`, and a new sealed `group_by: GroupBy | None` (the plan's grouping when a `FinancialPlan` drew the chart); `_nouns_of` prefers `group_by` (WEEK → «semanas») and falls back to labels. The DRILL reads `metric_definition_borrowed` and binds to the DRAWING plan (`readers/drill_down.py`, `_compare_periods` scenario from A-2) — a borrowed plan → `DRILL_AMBIGUOUS`-style honest ask, never the latest window silently. `drawn_labels` on a series dataset ranks within the series the chart ranks, or sets `ranked=False` (A-11). Shadow mode records no `fallback_reason` (A-4); `changed_from_baseline` computed in promoted mode (A-5). Docs: design §4/§7 tables.

### Task 3 — The language surface stops guessing (B-1, B-2, B-4, C-1, C-6, C-3, A-8, A-9, A-10, B-3, B-6, B-8, C-4)
`category_words`: imperative exclusion forms («quita», «sin», «excluye», «menos») → exclusion, never inclusion (B-1). The plan builder: an unrecognised bare word never becomes a merchant filter — it asks (B-2). The controller: the restyle short-circuit requires a chart (C-1) and runs AFTER the payment branches (C-6); a bare «todos»/«sin límite» with no chart falls through (C-3). Seasons: «este verano» → an honest ask naming the months it would take, not the inherited window (B-4). `_VISUAL`: `lineas|areas|puntos`, «prefiero», «solo los N primeros» → `limit` (A-8, B-8); «¿por qué has elegido barras?» → `asks_why` before the restyle grammar and the grammar refuses «por qué … barras» (A-9); `_FINANCIAL` no longer blocks «ordena las categorías de mayor a menor» when the sentence is nothing but a sort (A-10). `derived_from` decided by the fact (same plan lineage), not the verb (B-3). The analyser claims «explícame el gráfico» (→ why), «¿por qué tanto?» and «¿qué comercio es responsable?» (→ drill) (B-6). `parsed_month` rejects year 0000 (C-4). Every fix: a red test through the streaming route with the fakes; the audit's sentence table re-run and pasted.

### Task 4 — Charts mean what they say (U-1, U-2, U-3, U-6, U-7, U-8, A-3, A-7)
`visual_interpreter`: «desapilar» → `stack="disabled"` (U-1). Donut/bar rebuilds carry the dimension's `EncodingSort` (U-6). `_MEASURE_WORDS` closed over the real `Metric` enum + the shape markers: COUNT/FREQUENCY → «número de movimientos», RECURRENCE → «importe» (its points are amounts — U-2), AVERAGE/PERIOD_AVERAGE → «importe medio»; the test walks every producer's `metric`/`plan_metric`, not only `FAMILY_DATASETS` (U-3, A-7). `verify()` refuses `limit` on an ungrouped period comparison (A-3). `VisualAnswer`: `mark=null` with any other field set → `invalid_proposal:structure`, not abstention (U-7). `VisualPlanningPolicy`: the broad except splits provider errors from code errors (a code error is logged as such and still falls back) (U-8).

### Task 5 — The gateway (G-1..G-11)
The payment-refusal flag clears (G-1); the fake assistant's charted/restyled payloads mirror the real orchestrator's shapes (G-2/G-3) and gain KPI, `explanation`, `save_candidate`, `parts`, `VISUAL_*` cases (G-8); the conversation is committed before synthesis so barge-in keeps the chart (G-4); compound answers: identity per part and the numbering not spoken (G-5); `VISUAL_EXPLAINED` and the refusals carry the chart id they are about (G-6); the refusal branch emits an honest minimal answer per ADR-010 (G-7); the newest-last invariant tested with three contexts (G-9); a 422 becomes a recoverable turn error (G-10); an unknown visualization kind stamps no id (G-11). ADR-010 amended where the protocol changes.

### Task 6 — The web (W-1..W-8)
The fence and `materializeVisualizationDerivation` agree on real payloads (W-1); a materialisation failure disarms the fence (W-2); straggler PCM frames dropped by turn id (W-3); audio-first ordering handled (W-4); a no-audio turn times out (W-5); `validateVoiceAnswer` accepts absence where the orchestrator omits (W-6); restyle answers keep «Ver la consulta»/«Ver los movimientos» from the source turn (W-7); `role="alert"` on the voice error (W-8). Tests for each.

### Task 7 — The record says what was measured (M-1..M-9, U-9)
`evaluation/planning/RESULTS.md`: the FAIL is stated at the top as the last measurement and HEAD's unmeasured state named; the runbook (`docs/…/runbook`, `justfile`, compose) made runnable (right default model, a real `--mode`, no dead env var) (M-3); `benchmark_e2e.py` gains `--follow-up-pcm-file` (M-7); the §8 gate rewritten as two separately measured quantities with their provenance (M-1); the baseline's model named (M-2); the budget described as a guess with the right-censoring caveat (M-4); every «landed / verbatim / end to end» claim site says «with the stub planner and the fake ledger» (M-6); a small script computes the gate quantity from a RESULTS row (M-9). Wave 4's prerequisite status stated honestly in `docs/next.md` (M-5). **The GPU runs themselves stay the user's.**

### Task 8 — The corpus learns the sentences it avoided (B-5, B-7, blind spots)
A new episode 34 with the audit's natural sentences and HONEST expectations (right, or an ask, or a refusal — never a wrong answer pinned); the harness gains `explanation_says` and a `chart_text_agrees` check (B-5: the chart's dimension/measure vs the answer's plan); restyle answers offer chips again (B-7); ratchets re-measured and lowered by the controller where the number fell. Episodes 32/33's `why` no longer says sentences were chosen to spare the ratchets.

### Task 9 — Docs
Design, `docs/next.md`, `conversational-capabilities.md`, this plan's Landed; the audits' «what is solid» carried as the wave's negative space.

## Landed

One line per task, SHAs post-strip (D-H-3's `git filter-branch` over `f43fbab..HEAD` removed the
`Co-Authored-By` trailer from five Task 7 commits and remapped every SHA in the batch — the list
below is the remapped one, not the one any individual task report first announced):

- **Task 1** (A-1/A-13) — `4cb9fc6`, `3ce9cb6`.
- **Task 2** (A-2/U-4/C-5/A-6/A-11/A-4/A-5/U-5) — `09ddc3f`, `7801a15`, `fd2c9a9`, `77f5f1d`, `a164a90`.
- **Task 3** (B-1/B-2/B-4/C-1/C-6/C-3/A-8/A-9/A-10/B-3/B-6/B-8/C-4) — `cd40ebe`, `72cb982`,
  `12880d4`, `cfb0836`, `541ecd3`, `e63d15a`, `2c3c75c`; B-2 left BLOCKED (R-H1, below), closed
  instead by Task 8's stop-list.
- **Task 3 fix round** (F-H1 Critical, F-H2, F-H3, raised by the batch-1 review) — `45c9d6b`,
  `bbcaef6`, `2b64f14`.
- **Task 4** (U-1/U-2/U-3/U-6/U-7/U-8/A-3/A-7, plus D-H-1) — `ab1ae38`, `96fcbb9`, `a628f72`,
  `86befb1`, `d5f4e3a`, `96435fd`, `614ed0e`.
- **Task 5** (G-1..G-11) — `0938625`, `ad95864`, `f0f4231`, `557e71a`, `9ee33fe`; G-2/G-3/G-6/G-8/G-11's
  own commit content landed inside `0fcb07a` (Task 6's commit) by a shared-index race — see the
  git-race map below, no content lost.
- **Task 6** (W-1..W-8) — `0fcb07a`, `c747362`, `52b5f3e`, `52be37d`.
- **Task 7** (M-1..M-9/U-9) — `0938625` (the State-at-HEAD block, same race as Task 5's G-1),
  `f4d9d77`, `f0c203d`, `93b51c9`+`edd379d` (one intended commit split in two by a background race),
  `0712927`.
- **Task 8** (B-2a, the pinned turns, harness kinds, B-7, episode 34) — `9147ac1`, `90c9264`,
  `ced6672`, `60db150`, `8c76c57`, `57b4140`.
- **Task 8b** (B-9, save request, «vuelve al anterior», the drill asks, `_SPLIT_BY_NAMES` reorder,
  the ratchets brought back to ceiling) — `c530efb`, `94bae8d`, `423f7c9`, `1bfec6e`, `df43db4`,
  `133e973`.
- **Task 9** (docs) — this task's own commit.

**Rulings, one line each:**
- **R-H1** — B-2 splits into a CLOSED stop-list (Task 8, zero calls) and the pinned corpus turns
  that had fixed the old guess-then-retract as expected behaviour, corrected once confirmed a
  confident wrong answer rather than an honest retraction; the stop-list is an interim measure,
  superseded by a general capitalised-or-known-merchant rule once no pinned episode still breaks
  under it.
- **R-H2** — a ratchet ceiling (`UNMEASURED`, `MUTE`) is never raised: Task 8 measured both over
  ceiling by driving episode 34's real sentences through the harness, and Task 8b brought both back
  to their ceiling — 22/22 and 43/43 — by claiming the sentences with real readers, never by
  widening the literal.

**Deferred D-H-N, open or closed:**
- **D-H-1** — CLOSED (Task 4): `QuestionResponse.about_chart_id` added, read-never-authoritative.
- **D-H-1b** (gateway `chart_identity` reading `about_chart_id`) — OPEN, gateway-side, not this wave's.
- **D-H-2** — OPEN: PCM audio frames carry no turn id on the wire; the web's W-3 fix narrows the
  barge-in race, the complete fix needs a gateway-side wire tag (ADR-010 now notes it explicitly).
- **D-H-3** — CLOSED: the five `Co-Authored-By` trailers stripped, the SHA map above is the result.
- **D-H-4** — OPEN, minor: the promoted-only floor recompute in `visual_planning_policy.py` is
  correct by control-flow tracing, not an explicit mode guard.
- **D-H-5** — OPEN, minor: `llm_controller.py` imports the private `_COMPARISON_LIMIT_REASON`
  across the domain/infrastructure boundary.
- **D-H-6** — OPEN: nothing forces a future continuation reader to stamp
  `plan_continues_conversation` itself; a reader-registry-walking test was proposed, not written.
- **D-H-7** — OPEN, IMPORTANT: the season clarification's draft carries only `direction`; a
  breakdown-asking season sentence still resolves to a total, not the ranking it asks for.
- **D-H-8** — OPEN, docs (this task): `scripts/handover_cases.py`'s "measured" set reads only
  `planning/conversations.yaml`, never `dev.yaml` — Task 8b's `dev.yaml` addition documents a plan
  shape but does not itself move the UNMEASURED ratchet; what moved it was the routing fix that
  stopped the sentence reaching the planner at all.

**The git-race attribution map** (so the history reads honestly rather than looking tampered — no
content was lost in any of the three incidents below, each confirmed by direct file inspection at
HEAD, independently, by the batch-1 review):
- `0938625` (`fix(voice): a refused voice payment does not silence the session`) carries Task 5's
  own G-1 files **plus** Task 7's `RESULTS.md` State-at-HEAD block — two implementers' commits
  landed as one, in a shared worktree, caught and mapped rather than rewritten while both were live.
- `0fcb07a` (`fix(web): the client accepts the answers the orchestrator really sends`) carries
  Task 6's W-1/W-2/W-3/W-4/W-6 web files **plus** Task 5's G-2/G-3/G-6/G-8/G-11 gateway files.
- `93b51c9`/`edd379d` share one subject line for two different files (`latency_gate.py` new,
  `RESULTS.md` edited) — a background race split one intended commit into two, not a duplication.
- The METHOD RULE this wave converged on — `git commit -m "<msg>" -- <paths>` in one call, never a
  separate `git add` — closed the race for every implementer from the point each adopted it onward.

Then: whole-branch review (opus, base `d88324c`), one fix wave, scoped re-review, CLOSED line; push and fast-forward on the user's word.
