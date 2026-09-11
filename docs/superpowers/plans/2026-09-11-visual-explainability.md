# Visual Explainability (Wave 4.5) Implementation Plan

> Committed 2026-09-11 on
> `task/continuous-visual-conversation`; BASE = Wave 4.4's closing head `e95ce67`. Standing authorisation:
> the user's 2026-09-05 13:07 instruction. Not authorised: push, merge into `develop`, any commit on `task/wave3`
> other than a fast-forward. Pre-read: `.superpowers/sdd/2026-09-10-visual-explainability/explorer-report.md`.

**Goal:** a chart can say why it is the chart it is — under a discreet «¿Por qué este gráfico?» on screen, and as a
spoken or typed question in the conversation — derived only from what the accepted chart itself carries, never from
a model's rationale, never from a discarded proposal, with zero banking calls and zero model calls.

**Design authority:** `docs/superpowers/specs/2026-09-05-continuous-visual-conversation-design.md` §7 (rewritten by
Task 1 of this plan — see Ruling R1), §9's invariants («zero explanations of a discarded spec»), §10 row 4.5, §11
bullet 1 («discreetly explains why bars») and §12/§13 (no automatic action from a chart; no wire authority; no
second memory; no rationale).

## Rulings that shape the plan (R1-R10; the ledger carries any later ones)

- **R1 — §7 is rewritten, not implemented as written.** The 4.1 ledger (`…/2026-09-06-visual-follow-up-context/progress.md:142`)
  already ruled that `decision`/`source_plan` are turn-local and cannot feed an explanation on a later turn. The
  explanation is a pure function of what the sealed `VisualContext` carries after a round trip: `spec`, `profile`,
  `labels`, `ranked`, `window`, `derived_from`, plus a **descriptive provenance** added by this wave and sealed with
  the rest: `source: "llm" | "deterministic" | "patch"` and `fallback_reason: FallbackReason | None`. Vocabulary,
  not authority: nothing reads it to decide anything; it only lets the explanation say who chose the chart.
- **R2 — provenance wording follows the real closed sets**, never §7's invented `AI_VERIFIED/DETERMINISTIC_FALLBACK/USER_PATCH`:
  `llm` → «Propuesto por el asistente y comprobado por las reglas de FinGuard.»; `deterministic` with a
  `fallback_reason` → «Visualización segura seleccionada automáticamente.»; `deterministic` without one → «Elegido
  por las reglas de FinGuard.»; `patch` → «Ajustado a petición tuya sobre el gráfico anterior.». The sentence never
  names a `RejectionRule`, a provider error or the discarded proposal (§9 invariant).
- **R3 — scope may state dates and a count of groups, never an amount.** `scope_summary` reads the sealed
  `window` («entre el 1 de junio y el 31 de agosto de 2026») and `profile.row_count` («{n} {grupos|meses|categorías…}»,
  the noun from `spoken_presenter`'s closed `_NOUNS`, exported). The evidence (movement) count does not survive the wire and is
  NOT stated. The value-free rule of 4.4 is thus widened for the explanation only: digits allowed for dates and the
  group count; a currency amount, a total or a percentage never — one test asserts no `€`, no decimal, no thousands
  pattern over every family.
- **R4 — one derivation, two surfaces.** `domain/services/visual_explanation.py` `explain_chart(context) ->
  VisualExplanation` (frozen dataclass: `chart_reason`, `ordering_reason | None`, `scope_summary`, `provenance`,
  `as_text()` joining them). Surface 1: `QuestionResponse.explanation: VisualExplanationResponse | None` on every
  answer that carries a chart (TEXT and VOICE alike; the restyle path too, from the derived context) — the web shows it
  under a `<details>` «¿Por qué este gráfico?» beside «Ver la consulta». Surface 2: the question «¿Por qué este
  gráfico?» (and its variants) answered in `text` with `as_text()`, outcome `VISUAL_EXPLAINED`. The gateway is
  untouched: it speaks `text` when `spoken` is `None` (R6 of 4.4 keeps `spoken` `None` on visual-only outcomes).
- **R5 — the question is claimed deterministically in the controller's visual short-circuit**, before
  `interpret_visual`, by a domain recogniser `visual_explanation.asks_why(sentence) -> VisualTarget | None` that reuses
  `visual_interpreter`'s `_TARGET` grammar («este/el primer/el último gráfico»). Phrasings claimed (closed, tested):
  «¿Por qué este gráfico?», «¿Por qué (en) barras/líneas/donut?», «¿Por qué este orden?», «¿Por qué (me lo) has
  puesto así?» ONLY when a trusted chart exists. `about_the_answer`'s verb phrasings are NOT touched; the CONTESTED
  ratchet (`Ceiling(0)`) is probed for every claimed phrasing before the corpus turn is written. No chart in the
  conversation → the recogniser abstains (the turn goes where it went before) — no new refusal text.
- **R6 — no new endpoint, no new memory, no automatic action.** The `<details>` is inert; no chip is added (a chip
  would be the first non-restyle chip and bypass `_chip_verifies`).
- **R7 — M-2 (4.4 branch review) is closed here:** `question_to_dto` hands the presenter (and now the explainer's
  noun) the plan only when `sha256(plan)[:16] == newest.plan_digest`; otherwise `None` → the generic noun. One test
  pairs a charted follow-up's noun with the chart's own grouping under a stale `last_plan`.
- **R8 — `turn_id` bound to a recoverable turn is REFUSED for this wave**, with the reason written down: it needs a
  store (§13's second memory) or a replayable record the orchestrator does not keep; the explanation does not need
  it (R1). `docs/next.md`'s sentence is updated to say so.
- **R9 — the video's polish is the user's**, like Task 9; the plan states what the on-camera run needs from the code
  (episode 32's order; the explanation visible after turn 1 and spoken after «¿Por qué este gráfico?»).
- **R10 — ratchets may only go down**; the new corpus turn speaks a phrasing the new recogniser claims, so
  `UNMEASURED`/`MUTE` fall or stay; `Ceiling` literals are never edited by an implementer (the controller lowers them
  in a docs-style commit when the measured number is below the literal).

## Global Constraints

- Three apps: orchestrator (Python 3.12, uv; gates `ruff check`, `ruff format --check`, `mypy src`; suite as four
  foreground pytest calls with `timeout: 600000`: `tests/test_[a-l]*.py --deselect tests/test_conversation_corpus.py`,
  `tests/test_[m-s]*.py`, `tests/test_[t-z]*.py`, `tests/test_conversation_corpus.py`), web (vitest, `npm run
  typecheck`); the gateway is untouched (a test asserts `git diff --stat BASE HEAD -- apps/voice-gateway` empty at close).
- `domain/` imports nothing from `application/`/`infrastructure/`. Spanish user-visible text. No model call on any
  new path. Deterministic tests only. No `Ceiling`/`Floor` edits by implementers; no existing corpus episode changed
  except the one turn Task 5 adds to episode 32 (and its `why`).
- The seal: any new `VisualContext` field travels through the three boundaries — `chart.py` + `llm_dto.VisualContextPayload`
  + `conversation_mapper` (to/from payload) — and enters the sealed digest (`visual_seal`), or the wire can lie about it.
- Baselines (recorded at BASE, filled by the controller): orchestrator 4143 passed / 3 skipped / 209 deselected (globs 2170 + 1004 + 969) + corpus 207 (176 · 1193) at 0.0 %; web 113; gateway 122 / 1 (must not change); ratchets 22/43/0.
- Commit messages: one-line subject only, no trailer. No push.

## File Structure

- `apps/orchestrator/src/finguard_orchestrator/domain/models/chart.py` — `VisualContext.source`, `.fallback_reason` (sealed).
- `…/infrastructure/dto/llm_dto.py` — `VisualContextPayload.source/fallback_reason`; `VisualExplanationResponse`; `QuestionResponse.explanation`.
- `…/infrastructure/mappers/conversation_mapper.py`, `…/domain/services/visual_seal.py` (or where the digest lives) — the two new fields.
- `…/infrastructure/mappers/llm_mapper.py` — `_remember_visual_context` records provenance from `result.visual_decision`; `explanation` filled after the seal; R7's digest check.
- `…/domain/services/visual_explanation.py` (new) — `VisualExplanation`, `explain_chart`, `asks_why`.
- `…/domain/services/spoken_presenter.py` — `_NOUNS` exported as `group_nouns()` for reuse.
- `…/infrastructure/controllers/llm_controller.py` — the `asks_why` short-circuit on both routes → `VISUAL_EXPLAINED`.
- `…/domain/enums.py` (or wherever outcomes live) — `VISUAL_EXPLAINED`.
- `apps/orchestrator/tests/support/episodes.py` — `VISUAL_EXPLAINED` in `_VISUAL_OUTCOMES`; `Turn.explanation: bool | None`.
- `apps/orchestrator/evaluation/conversations/32-visual-continuation.yaml` — one turn after turn 1.
- `apps/web/src/api.ts`, `Conversation.tsx`, `Conversation.test.ts` — the `<details>`.
- Docs: design §7/§10/§11, `docs/next.md`, `docs/conversational-capabilities.md`, this plan's Landed.

## Tasks

### Task 1: The design says what an explanation may know, and the context carries its provenance, sealed
Rewrite §7 per R1-R3 (the dataclass, the derivation inputs, the wording rules, the refused inputs). `VisualContext.source: VisualSource | None`, `fallback_reason: FallbackReason | None`, through the three boundaries and into the seal; `_remember_visual_context` fills them from `result.visual_decision` (`None` when a chart is echoed with no decision — never inferred, per `record_visual_decision`'s own refusal); a restyle context inherits `source="patch"`, `fallback_reason=None`. Tests: round trip preserves both; a tampered `source` on the wire fails the seal (`VISUAL_UNTRUSTED`); an echoed chart carries `None`. Two commits (docs; code).

### Task 2: A chart explains itself from what it carries
`visual_explanation.py`: `explain_chart(context) -> VisualExplanation`. `chart_reason` by mark and profile (bar nominal → «Barras: comparas {categorías|comercios|…} entre sí.»; bar temporal / line / area → «Líneas: ves cómo cambia el {gasto|ingreso|movimiento} a lo largo de los {meses|semanas|días}.»; donut → «Reparto: cada parte es su peso en el total.»; comparison → «Dos periodos lado a lado.»; point → …; the closed mapping asserted over every mark in `ChartSpec.mark`'s Literal). `ordering_reason` from the dimension's `EncodingSort` and `ranked` («Orden: de mayor a menor {gasto}.» / «Orden: cronológico.» / `None`). `scope_summary` per R3. `provenance` per R2. `as_text()`. Tests over `FAMILY_DATASETS` with the exact strings; the value rule test; the mark and provenance vocabularies asserted closed at import. One commit.

### Task 3: The explanation rides every charted answer, and the screen shows it discreetly
`VisualExplanationResponse` + `QuestionResponse.explanation` filled after the seal for any answer whose `visualization` or `visualization_derivation` names a chart (both routes; the restyle path from the derived context); R7's digest check for the presenter's and explainer's plan. Web: `Answer.explanation` in `api.ts`; a `<details>` «¿Por qué este gráfico?» beside «Ver la consulta» rendering the three lines + provenance; tests (present on a charted answer, absent otherwise, inert). Two commits (orchestrator; web).

### Task 4: «¿Por qué este gráfico?» is a question the conversation answers without asking anyone
`asks_why` recogniser (R5) and the controller short-circuit on both routes → `VISUAL_EXPLAINED`, `text = explain_chart(target).as_text()`, `spoken=None`, no banking, no model, the conversation unchanged (no new context minted — an explanation is not a chart). Harness: `VISUAL_EXPLAINED` in `_VISUAL_OUTCOMES`. Tests: each phrasing → `VISUAL_EXPLAINED` with zero calls on TEXT and VOICE; «el primer gráfico» targets the first; no chart → abstains; CONTESTED probe for every phrasing (0). One commit.

### Task 5: The corpus asks why, and the presenter's plan is the chart's
Episode 32 gains «¿Por qué este gráfico?» after turn 1 (`route: VISUAL`, `banking_calls: 0`, `no_model: true`, `explanation: true`), plus `Turn.explanation` in the harness; episode 33 turn 1 asserts `explanation: true` (VOICE, TEXT alike). R7's stale-plan test. Ratchets measured; the controller lowers a `Ceiling` if the number fell. Two commits.

### Task 6: The record on paper
Design §10 row 4.5 (done when: §11 runs on camera — the code side is ready; the run is the user's), §11 bullet 1 landed clause, `docs/next.md` (4.5 paragraph; R8's refusal of the `turn_id` binding), `docs/conversational-capabilities.md` row, this plan's Landed. One commit.

Then: whole-branch review (opus, base = 4.4's closing head), one fix wave, scoped re-review, CLOSED line. Not pushed; not fast-forwarded by default (the user decides).

## Self-review
- Every §7 input in R1 survives the wire and is sealed — checked against `chart.py:352-410` and `llm_dto.py:326-356`.
- Nothing reads `source`/`fallback_reason` to decide anything (grep at review).
- Zero explanations of a discarded spec: no `proposed_spec`/`rejected_rule` reaches the explanation (they are not on the context).
- The gateway diff is empty at close.
