# Natural-Language Acceptance (Wave 4.2b) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** §9's scenario works end to end in its own words, deterministically: «Separa transporte público y gasolina.» re-queries the summer split by the two categories and its chart derives from the first; «Ahora compáralo por meses.» turns the answered query into a month-by-month series; «Pon el gráfico anterior en vertical/horizontal.» turns the bars; and the corpus episode says so with no conceded wording.

**Architecture:** Four deterministic seams, no model rung. (1) The turn analyser's follow-up rule grows three shapes a charted conversation makes unambiguous: a sentence opening with «ahora», a reshape/compare verb carrying a clitic pronoun («compáralo», «sepáralo», «desglósalo»), and a split imperative naming two or more precise categories — all only when a `last_plan` exists. (2) The contextual follow-up reader gains two patches on the plan in view: split-by-named-categories (`categories=named, group_by=CATEGORY`) and regroup-by-calendar («por meses/semanas/días» → `group_by=MONTH|WEEK|DAY`), so the comparison capability guard is never reached for a regroup. (3) The chart gains an orientation: `EncodingPatch.orientation`, the interpreter's whitelist grows `horizontal|vertical`, and the merge swaps a bar's `x`↔`y` (and `xOffset`↔`yOffset`); the verifier already accepts either axis. (4) Episode 32 is rewritten in §9's words with the four per-turn assertions, and the planner hand-off ratchets move only the way their contract allows (down, when a reader claims a sentence back; one planning case written for turn 1 if its sentence still reaches the planner).

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, pytest-asyncio (`asyncio_mode = "auto"`), mypy strict on `src`, ruff line-length 100, `uv`, PyYAML.

**Spec:** `docs/superpowers/specs/2026-09-05-continuous-visual-conversation-design.md` §5 (the deterministic reader decides first; the model rung is optional and not built here), §9 (the base episode), §10 row 4.2 («done when»), §11 (acceptance: turns 2 and 3 in words), §13. The argument: the third external review (2026-09-07 10:03) — «Wave 4.2 demuestra infraestructura de continuidad, no todavía la conversación visual continua».

**Base:** `task/continuous-visual-conversation` after Task 3b lands (the plan commit's parent is 3b's docs commit). Suite baseline: recorded at that head in the ledger, never re-measured.

## Global Constraints

- **Deterministic only.** No model call in tests or the default runtime; the `turn_interpreter` ladder is untouched; every new recognition is a lexicon rule with a test naming the sentence it claims and one it must not.
- **Refuse, never guess.** A split names ≥2 *precise* categories (`category_words.precisely_named_in`) or it is not a split; a regroup names a calendar unit or it is not a regroup; a sentence that matches none of the new shapes is analysed exactly as today.
- **Existing episodes do not move.** The four suite calls plus the corpus prove it; a sentence in another episode that changes route under a new rule is reported, never papered over (the rule narrows or the change is ruled).
- **The ratchets keep their contract.** `UNMEASURED`/`MUTE` literals move only down, when a reader claims a handed-over sentence back; a still-handed-over §9 sentence gets a planning case (the contract's other way down) — turn 1 only, probe-first. Never up.
- **No wire change** except the honest one: an orientation patch is a spec change the wire already carries (channels); `VisualContextPayload` gains nothing.
- **Result → chart, never chart → plan.** The reshape patches are plan patches on `focus_of(conversation)`; the chart follows from the result as today.
- `domain/` imports neither `application/` nor `infrastructure/`; closed sets via `get_args`; Spanish user-visible text.
- Atomic commits, no `Co-Authored-By`, no push. Never edit `Ceiling`/`Floor` except to LOWER a ratchet per its contract; never edit existing corpus episodes (episode 32 is this wave's own).
- Suite gate per commit: `uv run ruff check src tests scripts`, `uv run ruff format --check src tests scripts`, `uv run mypy src`, FOUR foreground pytest calls each with `timeout: 600000`.

---

## File Structure

| file | responsibility | task |
| --- | --- | --- |
| `domain/services/turn_analyzer.py` | `_FOLLOW_UP` grows the three shapes (gated on `last_plan`) | 1 |
| `domain/services/category_words.py` | read-only (precise names) | 1, 2 |
| `application/queries/readers/explicit_lexicon.py` | `temporal_month/week/day` accept the plural («por meses») | 2 |
| `application/queries/readers/follow_up.py` | split-by-named-categories patch; regroup-by-calendar patch | 2 |
| `domain/models/chart.py` | `EncodingPatch.orientation`; `merge_encoding_patch` swaps a bar's axes | 3 |
| `domain/services/visual_interpreter.py` | `_VISUAL` grows `horizontal|vertical`; the intent carries `orientation` | 3 |
| `infrastructure/mappers/llm_mapper.py` | chips: «Ponlo en vertical» / «Ponlo en horizontal» when they change the chart | 3 |
| `evaluation/conversations/32-visual-continuation.yaml` | §9's words, four assertions per turn | 4 |
| `evaluation/planning/conversations.yaml` | one case for turn 1, only if the probe says it still reaches the planner | 4 |
| `tests/test_what_we_hand_to_the_planner.py` | `UNMEASURED`/`MUTE` lowered if the count drops | 4 |
| docs: `docs/next.md`, design §10/§11, `docs/conversational-capabilities.md`, this plan | | 5 |

---

### Task 1: A charted conversation reads its follow-ups

**Files:**
- Modify: `apps/orchestrator/src/finguard_orchestrator/domain/services/turn_analyzer.py` (`_FOLLOW_UP`, ~l.30-32, and the relation decision ~l.118-132)
- Test: the analyser's test file (`tests/test_turn_analyzer*.py` — the brief names it)

**Interfaces:**
- Consumes: `category_words.precisely_named_in(sentence)` (domain), `conversation.last_plan`.
- Produces: `analyze(...)` returns `relation=FOLLOW_UP, focus=LAST_ANSWER` for, when `last_plan is not None`: (a) a sentence opening with `ahora\b`; (b) a reshape/compare verb with a clitic — `(compara|separa|desglosa|agrupa|reparte|divide|distingue)(lo|la|los|las|melo|mela|melos|melas)\b` at the start; (c) a split imperative — `^(separa|desglosa|divide|distingue|reparte)\b` followed by ≥2 precise categories in the sentence. Everything else exactly as today.

- [ ] **Step 1: Failing tests** (the file's builders; a conversation with a `last_plan` vs one without)

```python
@pytest.mark.parametrize(
    "sentence",
    [
        "Separa transporte público y gasolina.",
        "Ahora compáralo por meses.",
        "Desglósalo por comercio.",
        "Sepáralo en transporte público y gasolina",
    ],
)
async def test_a_charted_conversation_reads_a_reshape_as_a_follow_up(sentence) -> None:
    analysis = await analyze(sentence, conversation=WITH_LAST_PLAN, interpreter=Deterministic(), today=TODAY)
    assert (analysis.relation, analysis.focus) == (TurnRelation.FOLLOW_UP, TurnFocus.LAST_ANSWER)


@pytest.mark.parametrize(
    "sentence",
    [
        "Separa transporte público y gasolina.",   # no plan in view: nothing to follow
        "Ahora compáralo por meses.",
    ],
)
async def test_without_a_plan_in_view_the_same_sentence_is_a_new_request(sentence) -> None:
    analysis = await analyze(sentence, conversation=IDLE, interpreter=Deterministic(), today=TODAY)
    assert analysis.relation is TurnRelation.NEW_REQUEST


async def test_a_split_that_names_one_category_is_not_a_follow_up() -> None:
    analysis = await analyze("Separa la gasolina.", conversation=WITH_LAST_PLAN, interpreter=Deterministic(), today=TODAY)
    assert analysis.relation is TurnRelation.NEW_REQUEST   # one name is a filter question, not a split; the reader decides it as today
```

- [ ] **Step 2: Run** — FAIL on the first parametrised test.
- [ ] **Step 3: Implement** — extend `_FOLLOW_UP` (or add a sibling `_RESHAPE_FOLLOW_UP` and a `_split_names_two(folded)` helper) inside the existing `conversation.last_plan is not None and …` branch; comment each shape with the sentence that earns it. `fold()` the sentence the way the module already does (accents/case).
- [ ] **Step 4: Run** the analyser tests, `tests/test_visual_follow_up.py`, then the four suite calls. If any existing corpus episode's turn changes route (the corpus call goes red), STOP and report the sentence and the rule that claimed it.
- [ ] **Step 5: Commit** — `feat(turns): a charted conversation reads «ahora…», a clitic reshape verb and a two-category split as follow-ups`.

---

### Task 2: The reader splits by names and regroups by calendar

**Files:**
- Modify: `apps/orchestrator/src/finguard_orchestrator/application/queries/readers/explicit_lexicon.py` (`temporal_month` ~l.291-301 and its week/day siblings accept the plural)
- Modify: `apps/orchestrator/src/finguard_orchestrator/application/queries/readers/follow_up.py` (`read_contextual_follow_up`: the split patch before the `if named: return None` decline ~l.457; the presentation-dimension branch ~l.319-340 accepts a calendar group)
- Test: the reader's and the lexicon's test files (the brief names them)

**Interfaces:**
- Produces, on the plan in view (`previous`): **split** — for a sentence matching `^(separa|desglosa|divide|distingue|reparte)(lo|la|los|las)?\b` with `named = precisely_named_in(sentence)` of length ≥ 2: `replace(previous, categories=named, excluded_categories=(), group_by=GroupBy.CATEGORY)` (window, direction, account, merchants kept); **regroup** — for a sentence carrying a calendar unit («por meses», «por mes», «mes a mes», «por semanas», «por días») and a reshape/compare verb or nothing else: `replace(previous, group_by=<MONTH|WEEK|DAY>)`; the comparison readers are unaffected (they run first and decline as today), and the COMPARISON capability guard is never reached because the reader claims.
- `explicit_lexicon.temporal_month` matches `\b(?:por|al|cada)\s+mes(?:es)?\b` (same for week/day plurals).

- [ ] **Step 1: Failing tests**

```python
def test_a_two_category_split_re_queries_the_plan_in_view_by_those_categories() -> None:
    plan = read_contextual_follow_up(_query("Separa transporte público y gasolina.", conversation=CHARTED_SUMMER))
    assert plan is not None
    assert plan.categories == ("TRANSPORT_PUBLIC", "TRANSPORT_FUEL")
    assert plan.group_by is GroupBy.CATEGORY
    assert plan.window == CHARTED_SUMMER.last_plan.window and plan.direction == CHARTED_SUMMER.last_plan.direction


def test_a_regroup_by_months_turns_the_plan_in_view_into_a_series() -> None:
    plan = read_contextual_follow_up(_query("Ahora compáralo por meses.", conversation=SPLIT_SUMMER))
    assert plan is not None and plan.group_by is GroupBy.MONTH
    assert plan.categories == SPLIT_SUMMER.last_plan.categories and plan.window == SPLIT_SUMMER.last_plan.window


def test_the_plural_calendar_unit_is_read() -> None:
    assert explicit_lexicon.temporal_month("gasto por meses") is not None
    assert explicit_lexicon.temporal_month("gasto por mes") is not None


def test_a_one_name_split_still_declines() -> None:
    assert read_contextual_follow_up(_query("Separa la gasolina.", conversation=CHARTED_SUMMER)) is None
```

- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement**; the handler needs no change (the reader is already in the cascade at `stood_at=10`); confirm end to end through `turn_for`: «Ahora compáralo por meses.» after a split answer runs ONE ledger query with `group_by=MONTH` and is NOT refused (`outcome != "REFUSED"`), and its `VISUAL_FOLLOW_UP` row is `ANALYTICAL_RESHAPE / follow_up_plan`.
- [ ] **Step 4: Run** the reader/lexicon tests, `tests/test_visual_follow_up.py`, `tests/test_what_we_hand_to_the_planner.py` (the two sentences are no longer handed over — if `UNMEASURED`/`MUTE` counts DROP, lower the `Ceiling` literals to the new counts in this commit, per their contract; never raise), then the four suite calls.
- [ ] **Step 5: Commit** — `feat(readers): a split names its categories and a regroup names its calendar, so the plan in view is re-queried instead of refused`.

---

### Task 3: A bar can turn

**Files:**
- Modify: `apps/orchestrator/src/finguard_orchestrator/domain/models/chart.py` (`EncodingPatch.orientation: Literal["horizontal", "vertical"] | None`; `merge_encoding_patch`)
- Modify: `apps/orchestrator/src/finguard_orchestrator/domain/services/visual_interpreter.py` (`_VISUAL` + the intent)
- Modify: `apps/orchestrator/src/finguard_orchestrator/infrastructure/mappers/llm_mapper.py` (`_chart_suggestions` candidates)
- Test: `tests/test_visualization_stage2_core.py`, `tests/test_visual_plan_verifier.py`, `tests/test_visualization_stage1.py` (chips)

**Interfaces:**
- `Orientation = Literal["horizontal", "vertical"]` in `chart.py`; `EncodingPatch.orientation: Orientation | None = None`; in `merge_encoding_patch`, for `mark == "bar"` (the merged mark): `horizontal` = the dimension-role field on `y`, `vertical` = on `x`; if the current placement already matches, the spec is unchanged (the controller's no-op defence then answers `VISUAL_UNCHANGED`); otherwise every `x`↔`y` and `xOffset`↔`yOffset` channel swaps (tooltip/color untouched); for any other mark the merge raises `ValueError("orientation applies to bars")` (→ the `merge` refusal).
- `interpret_visual`: `_VISUAL` gains `horizontal|vertical|tumba\w*|de pie`; «en horizontal»/«túmbalo» → `orientation="horizontal"`, «en vertical»/«de pie» → `"vertical"`.
- Chips: «Ponlo en vertical» / «Ponlo en horizontal» join the candidates and pass the same gate (verify + not-a-no-op), so a horizontal bar offers only «Ponlo en vertical».

- [ ] **Step 1: Failing tests** — the floor's breakdown bar (dimension on `y`) + «pon el gráfico anterior en vertical» → dimension on `x`, measure on `y`, presented spec differs, verifier accepts; the same bar + «ponlo en horizontal» → `VISUAL_UNCHANGED` through `_visual_only_response`; the grouped comparison bar turned → offsets swap (`yOffset`→`xOffset`) and still verifies (`test_the_floors_own_chart_verifies_as_itself`'s family, turned); a line asked to turn → `merge` refusal; chips over the breakdown include «Ponlo en vertical» and not «Ponlo en horizontal».
- [ ] **Step 2-4:** implement, run the three files + `tests/test_llm_visual_planner.py` (the prompt is untouched — orientation is a patch, not a model vocabulary), the four suite calls.
- [ ] **Step 5: Commit** — `feat(visual): a bar can turn — «en vertical» / «en horizontal» swap the axes the bar owns`.

---

### Task 4: The episode speaks §9's words

**Files:**
- Modify: `apps/orchestrator/evaluation/conversations/32-visual-continuation.yaml`
- Maybe modify: `apps/orchestrator/evaluation/planning/conversations.yaml` (one case for turn 1), `tests/test_what_we_hand_to_the_planner.py` (ratchets lowered)

- [ ] **Probe first**, through the harness, §9's turns: 1 «¿En qué he gastado más este verano?» — does a deterministic reader claim it, or does it reach the planner? If it reaches the planner and no planning case exercises it, write the case in the planning dataset (`why`: §9's opener; expected plan: EXPENSE, `group_by: CATEGORY`, the window the readers give «este verano» — read `explicit_facts`/`natural_periods`; if the readers know no summer window, the case's window is what the planner should produce and the brief says which; if that is genuinely ambiguous, keep Task 7's anchor sentence for turn 1 and say so in `why` — never guess). 2 «Separa transporte público y gasolina.» → `route: QUERY`, `banking_calls: 1`, `derived_from: true`, `chart:` a bar over two categories (the `groups:` override supplies `TRANSPORT_PUBLIC`/`TRANSPORT_FUEL` rows). 3 «Ahora compáralo por meses.» → `route: QUERY`, `banking_calls: 1`, `derived_from: true`, `chart:` temporal (line or bar, dimension temporal on the axis; `groups:` rows keyed by month). 6 «Pon el gráfico anterior en vertical.» → `route: VISUAL`, `banking_calls: 0`, `derived_from: true`, `chart:` with the dimension on `x`; then, if the six-turn floor allows, «Pon el gráfico anterior en horizontal.» → `route: VISUAL`, `banking_calls: 0`, the same chart as turn 3's (a second flip). Turns 4-5 stay 4.3's, named in `why`.
- [ ] Ratchets: after Tasks 1-2 the two reshape sentences are claimed deterministically; if `UNMEASURED`/`MUTE` dropped and Task 2 did not already lower the literals, lower them here.
- [ ] Commit — `test(corpus): the visual episode speaks §9's words — a split, a regroup and a turned bar, each remembering the chart before it`.

---

### Task 5: The record on paper

- `docs/next.md`: a dated paragraph after 4.2's fix-wave notes — 4.2b landed: the three follow-up shapes, the two reader patches, orientation, the episode in §9's words; the measurement updated («3 of 14» → the new count of §3's phrasings claimed, measured through the classifier over the fourteen sentences — the brief lists them); what still is not done (turns 4-5 = 4.3; the model rung of §5 not built, deliberately).
- Design §10 row 4.2 → done (with 3b and 4.2b named); §11's acceptance lines marked landed.
- `docs/conversational-capabilities.md`: the two visual rows' Estado → «construida y probada conversacionalmente (episodio 32 en las palabras del §9)».
- This plan: "Landed" notes.
- Commit — `docs(visual): wave 4.2 is the continuous visual conversation it set out to be, in its own words`.

---

## Self-review

- **Spec coverage:** §11's two acceptance sentences → Tasks 1-2 (+ the episode, Task 4); §9's turn 6 → Task 3; §10 row 4.2 → Task 5. §5's model rung deliberately not built (stated).
- **Placeholders:** Task 1/2 test fixtures (`WITH_LAST_PLAN`, `IDLE`, `CHARTED_SUMMER`, `SPLIT_SUMMER`, `_query`) are the files' own builders — the briefs name them after the pre-read. Task 4's turn 1 is probe-first with an explicit fallback.
- **Type consistency:** `GroupBy.CATEGORY/MONTH` (domain enum), `precisely_named_in` returns canonical keys (`TRANSPORT_PUBLIC`, `TRANSPORT_FUEL`), `Orientation` literal named once.
- **Pre-read risks at dispatch:** (1) the analyser's test file and how it builds conversations with a `last_plan`; (2) whether `_FOLLOW_UP`'s branch order lets `ANSWER_TO_SLOT`/`CORRECTION` win first (it must — the new shapes go in the FOLLOW_UP branch only); (3) `explicit_facts._combined_total`'s vetoes — the reader's split patch must not depend on `facts.group_by`; (4) the corpus fake ledger's month-keyed groups for turn 3; (5) `merge_encoding_patch`'s donut channel logic must not collide with the axis swap; (6) «este verano» as a window.
