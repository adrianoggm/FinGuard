# Financial Drill-Down (Wave 4.3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** §9's turns 4-5 work end to end, deterministically: «¿Por qué agosto es tan alto?» after a month series resolves «agosto» to one bar of the last chart, runs the plan in view narrowed to that month (never widened), and answers the figure with the movements behind it; «Enséñame los movimientos responsables.» lists those same movements again; an ambiguous reference («ese pico» on a line whose heights the server cannot see) asks which bar, with zero banking calls.

**Architecture:** Four deterministic seams. (1) A trusted chart context carries its **labels** — the dimension values drawn (`2026-08`, `RESTAURANTS`, `lunes`) in **rank order** (the label of the tallest bar first; ties keep drawn order) plus `ranked: bool` (False when the top two tie or the chart has no measure), and the series values — value-free strings already on the client (§3.3): a rank is a shape the chart shows, never an amount; sealed with the rest of the context (3b). Pre-read fact: the dataset's rows come in the ledger's order, so the handler sorts the labels by value where the context is born; nothing downstream sees the values. (2) A domain **reference resolver** reads a sentence against those labels: month names (via the answer writer's month vocabulary), precise category words, weekday names, and superlatives («el más alto», «la barra más alta», «el pico» → the first label; «el más bajo» → the last) on any chart whose `ranked` is True — a tie at the top (`ranked=False`) asks, as §3.3 requires; two candidates → ambiguous, none → no match. (3) The analyser's follow-up rule gains the drill shape (the drill lexicon **and** a resolvable label or superlative, with a plan in view), and the movement-set reference accepts «los movimientos responsables / detrás / que lo explican». (4) The handler consumes `DRILL_DOWN`: one resolved label → a **bounded plan** (the plan in view with `date_range` = that month, or `categories=(that key,)`, `group_by=NONE`, the account scope kept, refused if the label lies outside the plan's window) run once with movements → the figure and its evidence; ambiguous → `DRILL_AMBIGUOUS`, a Spanish question naming the candidates, zero calls; no match → today's path (the planner). The previous chart stays in the conversation («back» is a restyle of it).

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, pytest-asyncio auto, mypy strict on `src`, ruff 100, `uv`, PyYAML.

**Spec:** `docs/superpowers/specs/2026-09-05-continuous-visual-conversation-design.md` §3.3 (lines 126-149: the pipeline, the rules — keep the account scope, restrict to the pointed-at period or group, never widen, link the movements to the original evidence, allow "back", refuse an ambiguous reference with a question; reference resolution over the previous result's dimension values first), §9 turns 4-5, §10 row 4.3 («turns 4-5 pass; an ambiguous reference asks»), §13.

**Base:** `task/continuous-visual-conversation @ dfdd2f4` (Wave 4.2b closed). Suite at that head: 3965 / 3 / 206 + corpus 204 at 0.0 %; ratchets `UNMEASURED` 22 / `MUTE` 43 / recogniser 0.

## Global Constraints

- **Deterministic only.** No model call; the planner is reached only when the resolver finds nothing (today's path).
- **Value-free wire.** Labels are group keys and series values — never amounts, never counts, never the sentence; bounded by the rows the chart was built from (`VisualProfile.row_count` ≤ 100); sealed by 3b's mechanism (they are part of `context_semantics`).
- **Never widen.** The bounded plan narrows `focus_of(conversation)`: a month label must fall inside the plan's window (else refuse with the question «Ese mes no está en el periodo del gráfico»); a category label must be one the plan's rows had; `group_by=NONE`; account scope kept; `group_limit=None`.
- **Refuse, never guess.** Two candidates → ask; a superlative on a chart whose order is not a magnitude ranking → ask (the server does not see heights); a reference to a label the trusted chart does not carry → no match (today's path).
- **Trust first.** Only a trusted (sealed) last context is read for labels; an untrusted one → today's path.
- **Existing behaviour does not move.** Every existing episode answers as at base; the planner hand-off ratchets move only down.
- `domain/` imports nothing from `application/`/`infrastructure/`; Spanish user-visible text; closed sets via `get_args`; atomic commits; no `Co-Authored-By`; no push; never edit `Ceiling`/`Floor` except to lower a ratchet, planning cases, or existing episodes (episode 32 is the visual wave's own).
- Suite gate per commit: `uv run ruff check src tests scripts`, `uv run ruff format --check src tests scripts`, `uv run mypy src`, FOUR foreground pytest calls each with `timeout: 600000`.

---

## File Structure

| file | responsibility | task |
| --- | --- | --- |
| `domain/models/chart.py`, `domain/services/visual_profile.py` | `DrawnLabels` + `drawn_labels(dataset)` (rank order, `ranked`); `VisualContext.labels`, `series_labels`, `ranked` (travel; sealed) | 1 |
| `infrastructure/dto/llm_dto.py`, `infrastructure/mappers/conversation_mapper.py`, `infrastructure/mappers/llm_mapper.py`, `infrastructure/controllers/llm_controller.py` | the wire fields; set where a context is born (`drawn_labels` of the result's dataset); a derived context inherits them; the web type and design §4 name them | 1 |
| `domain/services/visual_reference.py` (new) | `resolve_reference(sentence, context) -> Resolved \| Ambiguous \| NoMatch` | 2 |
| `domain/services/turn_analyzer.py` | the drill shape (lexicon + resolvable label/superlative, plan in view) | 3 |
| `application/queries/ask_financial_question.py` | `_MOVEMENT_SET_REFERENCE` accepts «los movimientos responsables / detrás de … / que lo explican»; the `DRILL_DOWN` branch: bounded plan → run with movements → answer; `DRILL_AMBIGUOUS` | 3, 4 |
| `application/queries/readers/visual_follow_up.py` | `_drill_down_claims` also requires a resolvable reference (so «¿por qué es tan caro?» with no label stays UNRELATED) — or not: judge at pre-read | 4 |
| `tests/support/episodes.py`, `evaluation/conversations/32-visual-continuation.yaml` | `Turn.movements: int \| None` (count shown) and a `Ledger` fake `movements:` override; §9's turns 4-5 in place; the ambiguity ask | 5 |
| docs | | 6 |

---

### Task 1: A trusted chart carries its labels

`VisualContext.labels`/`series_labels`/`ranked` (labels: the dataset's distinct `dimension` values ordered by their summed `value_minor` descending, ties in dataset order, capped at 100; `series_labels`: distinct `series` values in dataset order; `ranked: bool` False when the two largest totals tie or no measure exists); `VisualContextPayload.labels`/`series_labels`/`ranked` (lists of strings, ≤ 100 items, each `^[^\x00-\x1f\x7f]{1,64}$` — a wire-shape limit only: real labels carry accents («Mié») and spaces («en julio de 2026»), and authority stays with the seal; `ranked: bool = False` so an older echo reads back unranked and asks); `to_payload` emits, `to_domain` reads; `context_semantics` covers them (the seal binds them — a test tampering one label → untrusted); `_remember_visual_context` fills them from `drawn_labels(result.visual_dataset)`; `_derived_context` copies them forward; `apps/web/src/api.ts` names the three fields («sealed; echoed, never read»); design §4's wire sentence and listing name them. Tests: the families' rank order, ties, the cap; round trip; a tampered label → untrusted; an old payload → empty and unranked; the producers. Two commits: `feat(visual): a dataset knows the labels it draws, in rank order`, then `feat(visual): a chart context carries its labels, sealed with the rest`.

### Task 2: A reference resolver over the labels

`domain/services/visual_reference.py`: `Resolved(label: str, kind: Literal["month","category","weekday","rank"])`, `Ambiguous(candidates: tuple[str, ...])`, `NoMatch`. `resolve_reference(sentence: str, context: VisualContext) -> Resolved | Ambiguous | NoMatch`: fold the sentence; months — every `YYYY-MM` label whose Spanish month name (the answer writer's `month_name` vocabulary, imported from the domain — if `month_name` lives in `answer_writer` (domain) reuse it, never copy the twelve names) appears as a word; categories — `category_words.precisely_named_in(sentence)` intersected with the labels; weekdays — the label itself as a word; superlatives (`el|la (barra|mes|categoria)? mas (alto|alta|grande|caro|cara)|el pico|el mayor` → `labels[0]`; `mas (bajo|baja|pequeno|pequena|barato|barata)|el menor` → `labels[-1]`) when `context.ranked` is True, else `Ambiguous(all labels)` (a tie at the top: §3.3's «ese pico» when two bars tie); two or more label matches → `Ambiguous(matches)`; nothing → `NoMatch`. Tests over the eight families' contexts. Commit: `feat(visual): a reference to the last chart resolves over the labels it drew, or asks`.

### Task 3: The analyser and the movement-set reference read the drill

`turn_analyzer.analyze`: with a plan in view and a trusted last visual context (`conversation.visual_contexts[-1]`, `trusted`), a sentence matching the drill lexicon (the same regex as `_DRILL_DOWN` — import it or move it to the domain so there is one copy) whose `resolve_reference` is `Resolved` or `Ambiguous` → `FOLLOW_UP`/`LAST_ANSWER`; `NoMatch` → unchanged. `_MOVEMENT_SET_REFERENCE` gains `\blos\s+movimientos\s+(?:responsables|detras|que\s+lo\s+explican)\b` and «enséñame/muéstrame los movimientos» when a `ResultReference` with `transaction_ids` exists — the existing `_referenced_result_plan` path answers turn 5. Tests: the two §9 sentences over a charted conversation → FOLLOW_UP; without a chart → unchanged; «¿por qué es tan caro?» (no label) → unchanged. Commit: `feat(turns): a drill-down that names a bar of the last chart is a follow-up, and «los movimientos responsables» names the evidence in view`.

### Task 4: The handler drills

In the handler, right after `_classify_and_record_visual_follow_up` returns `DRILL_DOWN` with a trusted last context: `resolve_reference`; `Resolved` → the bounded plan from `focus_of(conversation)` (month: `date_range=whole_month(year, month)` — refuse with «Ese mes no está en el periodo del gráfico» if not within the plan's window; category: `categories=(key,)`; weekday: refuse for now with «Todavía no puedo abrir un día de la semana» — or support via `date_range`? weekdays repeat; refuse, say so; rank: the resolved label's kind decides as above); `group_by=NONE`, `group_limit=None`, `excluded_*` kept, account scope kept → `self._answer(plan, movements=MAX_SELECTED_TRANSACTIONS)` (the existing answered path shows movements when `group_limit is None`) → the ordinary result carries `movements` and a `ResultReference` with `transaction_ids` (turn 5's evidence); `Ambiguous` → `outcome="DRILL_AMBIGUOUS"`, text «¿A qué te refieres: junio, julio o agosto?» built from the candidates through the same month/category naming the answer writer uses, conversation unchanged, zero calls; `NoMatch` → fall through to today's routing. One decision row: `Step.VISUAL_FOLLOW_UP` is already recorded; add `detail` to the DECLINED/ask? Keep the record minimal — the plan's `ran` is the evidence. Tests: the §9 turn 4 over episode 32's turn 3 state → one call, the narrowed plan, movements returned; the ambiguity ask; a month outside the window refused; an untrusted context → today's path. Commit: `feat(visual): a drill-down runs the plan in view narrowed to the bar it names, and asks when the bar is ambiguous`.

### Task 5: The corpus sees the drill

`tests/support/episodes.py`: `Turn.movements: int | None` (asserts the answer's movement count shown); the `Ledger` fake gains a per-turn `movements:` override (rows `[[description, amount_minor, iso_date, category], ...]`) returned by `run()` for that turn (the fake today returns movements only in one hardcoded case). Episode 32: §9's turns 4-5 inserted after turn 3 in their own words («¿Por qué agosto es tan alto?» → `route: QUERY`, `banking_calls: 1`, `ran: {categories: [TRANSPORT_PUBLIC, TRANSPORT_FUEL], window: 2026-08, group_by: NONE}`, `movements: 3`; «Enséñame los movimientos responsables.» → `banking_calls: 1`, `ran.transaction_ids` = the previous evidence, `movements: 3`), then the restyle turns as they are (the previous chart stays; «Pon el primer gráfico en vertical.» still resolves — confirm). A harness test for the ambiguity ask («¿Por qué ese pico es tan alto?» on the month line → `DRILL_AMBIGUOUS`, zero calls, the three months named). Ratchets unchanged. Commit: `test(corpus): §9's drill-down turns run in their own words, and an ambiguous bar asks`.

### Task 6: The record on paper

`docs/next.md` (4.3 landed: what it does, what it refuses, what it does not do — no click-to-drill on the client, weekdays not drilled, superlatives only on ranked charts), design §10 row 4.3 → done, §11's turn-4 bullet marked landed, `docs/conversational-capabilities.md` a drill-down row, the plan's Landed notes. Commit: `docs(visual): wave 4.3 — a bar can be asked about, and the answer is the movements behind it`.

---

## Self-review

- **Spec coverage:** §3.3's pipeline → Tasks 1-4; its rules (scope kept, never widen, evidence linked, back, ambiguity asks) → Task 4 + the previous chart staying; §10 row 4.3 → Task 5; §9 turns 4-5 → Task 5.
- **Placeholders:** the label pattern, the superlative rule's dependence on drawn order, the weekday refusal and the `_drill_down_claims` tightening are marked "judge at pre-read" — the briefs settle them from the tree.
- **Type consistency:** `Resolved.kind` literal named once; `resolve_reference` returns the three dataclasses; the handler and the analyser import the same function.
- **Pre-read facts settled:** (1) the dataset's rows come in the ledger's order (the handler does not sort them; the floor sorts the axis only) — hence the rank-ordered `labels` + `ranked` on the context; `whole_month(year, month) -> AbsoluteRange` lives in `domain/services/window_words.py:350`; `month_name`/`month_group_name` in `domain/services/answer_writer.py:485-498` (`_MONTHS` at :503); `MAX_SELECTED_TRANSACTIONS = 50`; the fake ledger returns `groups_for_next_turn` or one default group and movements only for one hardcoded case; `tool_of`'s VISUAL set lists six outcomes — `DRILL_AMBIGUOUS` needs its own route or joins VISUAL (judge at Task 5's pre-read).
- **Pre-read risks remaining:** (2) `whole_month` signature and where month names live (`answer_writer._MONTHS` is domain?); (3) the fake ledger's `run()` movement shape; (4) whether `_referenced_result_plan` re-runs one call or zero; (5) `Turn` and `tool_of` for the `DRILL_AMBIGUOUS` outcome (route `VISUAL`? a new `DRILL` route? — the harness's route set).

## Landed

One line per task, its commit SHAs read from `git log`:

- **Plan**: `dfdd2f4` (draft) + `dfdd2f4` (base named, Task 1 restated per R1-R5: domain `drawn_labels` in rank order + `ranked`, the wire limit, two commits) + `dfdd2f4` (the wire pattern's control bytes fixed as text).
- **Task 1** — a trusted chart carries its labels: `67bb49a` (domain `DrawnLabels`/`drawn_labels`, rank order + `ranked`) + `67bb49a` (context fields, wire, seal coverage, producers, web type, design §4).
- **Task 2** — a reference resolver over the labels: `67bb49a` (`domain/models/visual_reference.py`, `domain/services/visual_reference.py`, `resolve_reference`).
- **Task 3** — the analyser and the movement-set reference read the drill: `6d02255` (the drill lexicon moved to the domain, one copy) + `6d02255` (`turn_analyzer._drills_into_the_chart`) + `6d02255` (`_MOVEMENT_SET_REFERENCE` admits «los movimientos responsables/detrás/que lo explican» and a bare «enséñame/muéstrame/dame los movimientos»).
- **Task 4** — the handler drills: `be9aa7f` (`readers/drill_down.py`, the handler branch, `_answer(movements=)`) + `be9aa7f` (`_remember_visual_context` derives for `DRILL_DOWN`); fix round 1: `be9aa7f` (I-1 `parsed_month` made public and imported; M1 dead reshaped guard removed) + `be9aa7f` (M2 the in-progress month clamps to today; M3/M6 docstrings; M4/M5 tests).
- **Task 5** — the corpus sees the drill: `d8ce518` (harness `Turn.movements`, `Ledger.movements_for_next_turn`, `tool_of` maps `DRILL_AMBIGUOUS` to `CLARIFY`) + `d8ce518` (episode 32 gains §9's turns 4-5 verbatim; the ambiguity-ask test; `scripts/handover_cases.py` wiring).
- **Task 6** — the record on paper: this commit.

One line per ruling that changed the plan's own text above, rather than only settling a "judge at
pre-read" placeholder:

- **R14** drops the weekday refusal Task 4's own line above still names ("weekday: refuse for now
  with «Todavía no puedo abrir un día de la semana»"): weekday references are not resolved at all in
  4.3 — YAGNI, the only consumer would have been that refusal text, and `ReferenceKind` never grew a
  `"weekday"` member.
- **R15**: series labels travel on the wire (Task 1) but are never resolved — a comparison's window
  or a recurrence's cadence still goes to the planner as today; stated in the resolver's own
  docstring, not only here.
- **R16** (amends R10 and Task 4's own line above): the bounded plan is not the month/category alone
  — it is that label **and** a regroup by merchant (`group_limit=None`, the metric kept when
  SUM/COUNT, else normalised to SUM), because design §3.3's own third sentence, «¿Qué comercio es
  responsable?», has no turn of its own in episode 32 to answer it in. The "Ese mes no está en el
  periodo del gráfico" refusal Task 4's own line above named is dropped as unreachable: a label is
  inside the plan's window by construction (it is one of the plan's own rows), so the handler instead
  checks the plan's `group_by` matches the label's kind (`MONTH` for a month label, `CATEGORY` for a
  category label) and falls through to today's path otherwise.
- **R17**: turn 5, «Enséñame los movimientos responsables.», is answered by the existing
  `_referenced_result_plan` path once `_MOVEMENT_SET_REFERENCE` admits the new phrasings — not the
  new code path Task 3's own line above implied; the analyser is not involved.
- **R18**: the drill branch sits immediately before the referenced-result read in the handler, gated
  on `follow_up_seen[0].kind == "DRILL_DOWN"` and the newest context trusted; `_answer` gains
  `movements: int | None = None` forwarded to `run()` (the FREQUENCY/RECURRENCE branch keeps its own
  constant).
- **R19**: the fake `Ledger` returns movements only when asked (`run(movements=<int>)`) or when the
  plan carries `transaction_ids` — as Task 5 found it, not as an assumption; `Turn.movements: int |
  None` asserts the count the answer shows.
- **The Task 4 review's M2**: a drill into the month in progress ends today, never past the plan's
  own window — the plan's own Task 4 line above did not anticipate a chart drawn mid-month; the
  handler passes `query.today` and the window's end clamps to it (design §3.3, never widen).
- **Task 5's `scripts/handover_cases.py` wiring**: the ratchet replay injects the same per-turn
  groups/movements the corpus harness does, so the drill turns are not miscounted as planner
  handovers — without it the ratchets read 24/45 in that replay; with it, 22/43, unchanged, no
  literal touched.

One more sentence for the `docs/next.md` paragraph, already there: the in-progress month is clamped
to today (design §3.3, never widen), so «agosto» asked on the 5th drills the 1st-5th.

## Landed — whole-branch fix wave

The whole-branch review (`branch-review.md`) found one Critical and four Important items; the
controller's rulings (`branch-fix1.md`) are six atomic commits. One line per fix, its commit SHA
read from `git log`:

- **C-1 + I-1** — the context knows its window and its plan: `d8ce518` (`VisualDataset.window`,
  `VisualContext.window`/`plan_digest`, `domain/services/plan_digest.py`, `_answered_window`, the
  wire fields, sealed; design §4 amended in the same commit).
- **C-1 + I-1 + I-4** — the reader binds, intersects and asks only what it can act on: `d07a18b`
  (`read_drill_down` checks `newest.plan_digest == plan_digest(plan)` before narrowing anything,
  intersects the drilled month with the chart's own window before the M2 clamp, and checks
  `group_by` feasibility before `resolve_reference` ever runs — so an undrillable chart never asks).
- **I-2** — the evidence turn asks for exactly what it re-lists: `d07a18b`
  (`_answer(query, referenced_result, movements=len(referenced_result.transaction_ids) or None)` —
  the same line also serves the pre-existing «esos movimientos» reference path).
- **I-3** — the flagship episode's window genuinely contains its months: `12096f9` (the harness
  fake, `Ledger.run`, now answers `period_start`/`period_end` from injected `MONTH`-keyed groups
  clamped to `TODAY`; episode 32's turn 1 anchor becomes «¿Cuánto gasté el último trimestre?»,
  claimed deterministically by `_natural_period_aggregate_plan`'s own `_LAST_QUARTER` phrase —
  `RelativePeriod(LAST_N_MONTHS, n=3)`, the value the controller's own two named probes were
  reaching for but neither reached deterministically nor without moving the ratchets; `UNMEASURED`
  22 / `MUTE` 43 unmoved).
- **Minors M1, M2, M4, M5, M9, M10**: `12096f9` (a stale "cannot happen" comment reworded to what
  the code does; `_DERIVING_KINDS` asserted a subset of `get_args(VisualFollowUpKind)` at import
  time plus a one-line test pinning the handler's own `"DRILL_DOWN"` literal against it; the fake's
  movement-rule docstring says production shows movements where the fake shows none; the fixture-id
  comment reworded from "invented fresh" to "the fixture's own, stable"; `_ask` names only a
  candidate `_candidate_name` actually recognises and falls back to the generic question otherwise;
  the 100-label wire cap imported from `domain/services/visual_profile.MAX_DRAWN_LABELS` instead of
  written twice).
- **Docs**: this commit (`docs/next.md`'s 4.3 paragraph, `docs/conversational-capabilities.md`'s
  drill-down row, design §3.3's Landed paragraph, this file).

## Landed — fix round 2

The scoped re-review (`branch-re-review.md`) against `branch-fix1.md`'s own rulings found two new
Important items; the controller's rulings (`branch-fix2.md`) are two atomic commits. One line per
fix, its commit SHA read from `git log`:

- **N-1** — the empty-intersection guard now runs before the value is built: `8f40cf2`
  (`read_drill_down` computes `start`/`end` and checks `end < start` before constructing
  `AbsoluteRange`, so a month bucket the ledger reports outside the window it also reports, or a
  corpus episode whose `groups:` name a month past `TODAY`, falls to today's path rather than
  raising `InvalidValueError` as an unhandled HTTP 422).
- **N-2** — the drill's window is what the ledger answered *inside* what the plan asked, not the
  ledger's answer alone: `8f40cf2` (`_answered_window` intersects `answer.period_start/period_end`
  with `plan.date_range` when both are known, empty intersection → `None`; the harness fake's own
  `_resolved_window` now checks `plan.date_range` first, so an absolute plan answers exactly its
  own range and the month-key derivation applies only to a `RelativePeriod` plan; the re-reviewer's
  own end-to-end C-1 probe is now a harness-level test, `test_conversation_corpus.py`'s
  `test_c1_a_month_bar_never_speaks_past_the_slice_the_plan_asked_for`, rather than only a
  `read_drill_down` unit row — ratchets confirmed unmoved, `UNMEASURED` 22 / `MUTE` 43, because the
  test's own opening sentence reaches the planner and `handover_cases.py`'s `cases()` never reads
  anything but `evaluation/conversations/*.yaml`).
- **New Minor** (the import-time `assert` on `_DERIVING_KINDS` is stripped under `python -O`): left
  as is, per the ruling — consistent with `visual_follow_up.py`'s own `assert pair in
  get_args(Claimant)`, and the handler's literal is pinned by a test `-O` does not strip.
- **Docs**: this commit (`docs/next.md`'s never-widens paragraph, design §3.3's Landed paragraph,
  this file).
