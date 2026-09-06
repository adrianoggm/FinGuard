# Visual Planner Semantic Hardening (Wave 3.5) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the three semantic contracts an external review of Wave 3 found open — a chart can be technically valid and wrong for the user — so that a comparison never loses its series, the profile never promises a field the mapper cannot render, the adapter never reinterprets a malformed answer, the evaluation judges suitability by structure rather than by mark alone, and the visual planner's model and budget are configured on their own.

**Architecture:** Six bounded changes on Wave 3's own seams. `VisualProfile.comparison` derives from the dataset's shape marker, not from the presence of a series. The verifier gains one rule: when the profile is a comparison, the proposal keeps the series (colour and the axis's offset for a bar with stacking disabled; colour for a line or area), and an offset without colour is refused. The mapper serialises every field the profile declares in every row, `None` where a row lacks it, so profile, verifier, mapper and Vega-Lite share one field set. The adapter validates the provider's JSON against a Pydantic model whose closed sets are the same `Literal`s the schema is built from; any failure is a structure failure (one repair, then `ProviderFailedError`), never a partial draft. The evaluation's cases gain structural requirements and an expected instruction effect. Settings gain the visual planner's own provider, model, temperature and token limit, falling back to the financial planner's.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, pytest-asyncio (`asyncio_mode = "auto"`), mypy strict on `src`, ruff line-length 100, `uv`, PyYAML.

**Spec:** `docs/superpowers/specs/2026-09-03-llm-visual-planner-design.md` — Section C (refuse, never repair), Section D (mark table: the grouped comparison bar's colour + offset + `stack: disabled`; line/area comparison with series on colour), Section E (availability), Section G (evaluation: suitability is annotated structure, not a mark). The external review of 2026-09-06 (pasted by the user; its findings are restated in each task) is the argument; where it and the spec differ, the spec wins.

**Base:** `task/wave3` at `0f35640` (Waves 3 and 4.1 landed). Suite at base, recorded — do not re-measure: `tests/test_[a-l]*.py` + `tests/test_[m-s]*.py` + `tests/test_[t-z]*.py` = **3793 passed / 3 skipped / 192 deselected**, corpus **190 passed at 0.0 %**.

## Global Constraints

- **The verifier refuses, never repairs; so does the adapter.** No task adds a repair, a coercion or a default that stands in for what the model wrote.
- **The model never receives rows, amounts or labels.** Task 3 changes what rows carry on the wire to the client, not what the profile payload carries to the model (`profile_payload` is untouched; its key-set test stays).
- **The user's question never enters `VisualProfile`, DTOs, records or logs.**
- **No hand-copied domain literals.** Every closed set is a `Literal` read through `get_args`; the Pydantic validation model (Task 4) and `visual_schema()` read the same `Literal`s, and a test proves their enums are equal.
- **The floor's own charts keep verifying.** Every task that touches the verifier re-runs the eight-family parametrised test; a floor family that stops verifying is a plan defect to report, never a test to weaken.
- **No visible change with the shipped setting.** `ORCHESTRATOR_VISUAL_PLANNER=deterministic` stays the default; Task 3's row contract changes what the client receives (extra `null` keys on some rows) and is the one intentional wire change — the web client must tolerate it (Task 3 checks the client's reading of rows).
- **Never edit `Ceiling`, `Floor`, dataset cases or corpus episodes.** The visual `cases.yaml` is Wave 3's own file and gains keys; `suitable_marks` values do not change.
- `domain/` imports neither `application/` nor `infrastructure/`.
- Atomic commits, imperative present, **no `Co-Authored-By`**, no push.
- Suite gate per commit: `uv run ruff check src tests scripts`, `uv run ruff format --check src tests scripts`, `uv run mypy src`, then FOUR foreground pytest calls each with `timeout: 600000`: `tests/test_[a-l]*.py --deselect tests/test_conversation_corpus.py`, `tests/test_[m-s]*.py`, `tests/test_[t-z]*.py`, `tests/test_conversation_corpus.py`.

---

## File Structure

| file | responsibility | task |
| --- | --- | --- |
| `domain/services/visual_profile.py` | `comparison = dataset.metric == "COMPARISON"` | 1 |
| `domain/services/visual_plan_verifier.py` | `_grouped`: a comparison keeps its series; offset without colour refused | 2 |
| `infrastructure/mappers/llm_mapper.py` | rows carry every declared field (`None` when absent); `valid_for` against the union | 3 |
| `infrastructure/planning/llm_visual_planner.py` + new `infrastructure/planning/visual_answer.py` | Pydantic validation of the provider's JSON; `_draft` from the validated model | 4 |
| `scripts/evaluate_visual.py`, `evaluation/visual/cases.yaml` | `requires`, `instruction_effect`; structural suitability and sensitivity | 5 |
| `bootstrap/config.py`, `.env.defaults`, `bootstrap/main.py`, `scripts/evaluate_visual.py` | the visual planner's own provider/model/temperature/tokens; manifest records them; latency stated as added turn latency | 6 |
| `docs/next.md`, `docs/conversational-capabilities.md` | the record | 7 |
| tests: `tests/test_visual_profile.py`, `tests/test_visual_plan_verifier.py`, `tests/test_visualization_stage1.py`, `tests/test_llm_visual_planner.py`, `tests/test_evaluate_visual.py`, `tests/test_the_visual_planner_is_configurable.py`, `tests/test_visual_prompt*.py` (if present) | | 1–6 |

---

### Task 1: A comparison is what the dataset says it is

**Files:**
- Modify: `apps/orchestrator/src/finguard_orchestrator/domain/services/visual_profile.py` (~l.141, `comparison=has_series`)
- Test: `apps/orchestrator/tests/test_visual_profile.py`

**Interfaces:**
- Consumes: `VisualDataset.metric` — the dataset's *shape* marker (`COMPARISON`, `RATIO`, `RATIO_SHARE`, `RECURRENCE`, or a measure name; `chart.py:219-243`); the handler builds comparison datasets with `metric="COMPARISON"` (`ask_financial_question.py:~3335`).
- Produces: `VisualProfile.comparison is True` iff `dataset.metric == "COMPARISON"`; a RECURRENCE dataset (series = cadence) has `comparison is False` while keeping `mutually_exclusive=False`.

- [ ] **Step 1: Failing tests** (append to `tests/test_visual_profile.py`, using that file's dataset builders)

```python
def test_a_recurrence_with_series_is_not_a_comparison() -> None:
    """RECURRENCE uses series for the cadence (MONTHLY, …). Wave 3 derived `comparison` from the
    presence of a series, so this profile told the model two contradictory things at once —
    "use grouped bars" and "use points on occurred_at". The shape marker is the authority."""
    profile = visual_profile_of(RECURRENCE_DATASET, total_minor=None, group_by="NONE")
    assert profile.comparison is False
    assert profile.mutually_exclusive is False          # a series still means slices of one dimension


def test_a_comparison_dataset_is_a_comparison_even_before_its_series_is_read() -> None:
    profile = visual_profile_of(COMPARISON_DATASET, total_minor=None, group_by="CATEGORY")
    assert profile.comparison is True


def test_a_plain_breakdown_is_not_a_comparison() -> None:
    profile = visual_profile_of(BREAKDOWN_DATASET, total_minor=None, group_by="CATEGORY")
    assert profile.comparison is False
```

(`RECURRENCE_DATASET`, `COMPARISON_DATASET`, `BREAKDOWN_DATASET`: reuse the exporter's `FAMILY_DATASETS["recurrence"][0]`, `["categorical_comparison"][0]`, `["breakdown"][0]` — `from export_chart_fixtures import FAMILY_DATASETS` the way `tests/test_evaluate_visual.py` imports the scripts, or the file's own builders if it has them.)

- [ ] **Step 2: Run** — `uv run --directory apps/orchestrator pytest tests/test_visual_profile.py -q` — FAIL on the recurrence test.
- [ ] **Step 3: Implement** — `comparison=dataset.metric == "COMPARISON",` with the comment: "the shape marker, not the presence of a series: RECURRENCE carries a series for its cadence and is not a comparison". Then run `tests/test_visual_prompt*.py`/`test_llm_visual_planner.py` (the payload carries `comparison`) and `tests/test_visual_plan_verifier.py` (the floor families).
- [ ] **Step 4: Run** the four suite calls. Expected: the floor's RECURRENCE and comparison families still verify (the floor reads `comparison` — if a floor family changes chart under the new derivation, STOP and report: that is a floor/verifier disagreement the controller rules on).
- [ ] **Step 5: Commit** — `fix(visual): a comparison is what the dataset's shape says, not what the presence of a series implies`.

---

### Task 2: A comparison keeps its series

**Files:**
- Modify: `apps/orchestrator/src/finguard_orchestrator/domain/services/visual_plan_verifier.py` (`_grouped`, ~l.234-260)
- Test: `apps/orchestrator/tests/test_visual_plan_verifier.py`

**Interfaces:**
- Consumes: `profile.comparison` (Task 1), `profile.role("series")`, `_dimension_axis`, `_OFFSET_FOR`, `_on`.
- Produces: new refusals in the `grouped` rule — `"a comparison keeps its series: put it on color and on the {offset}"` (bar), `"a comparison keeps its series: put it on color"` (line/area), `"a comparison's bars do not stack: stack must be disabled"`, `"an offset needs its color: the same series on both"` (offset without colour, any profile).

- [ ] **Step 1: Failing tests**

```python
def test_a_comparison_bar_without_its_series_is_refused() -> None:
    """The review's case: dimension on y, measure on x, no series anywhere. It looks reasonable and
    the comparison between periods has vanished."""
    profile = _comparison_profile()                      # from the file's family builders
    draft = _bar(dimension_on="y", measure_on="x")        # no color, no offset
    rejected = verify(draft, profile)
    assert isinstance(rejected, Rejection) and rejected.rule == "grouped"
    assert "keeps its series" in rejected.reason


def test_a_comparison_bar_with_color_but_no_offset_is_refused() -> None: ...
def test_a_comparison_bar_with_offset_but_no_color_is_refused() -> None: ...   # "needs its color"
def test_a_comparison_bar_that_stacks_is_refused() -> None: ...                 # stack zero/normalize
def test_a_comparison_line_without_its_series_on_color_is_refused() -> None: ...
def test_the_grouped_comparison_bar_the_floor_draws_still_verifies() -> None: ...   # the existing family test covers it — assert explicitly once
def test_an_offset_without_color_is_refused_on_any_profile() -> None: ...
```

- [ ] **Step 2: Run** — FAIL (all authorised today).
- [ ] **Step 3: Implement** in `_grouped`: after the existing two-offsets and colour/offset-mismatch checks, `if offsets and color is None: return Rejection("grouped", "an offset needs its color: the same series on both")`; then `if profile.comparison:` — for `bar`: require `_on(spec, "color")` naming a series field AND `_on(spec, _OFFSET_FOR[dimension.channel])` on the nominal dimension axis, and `spec.stack == "disabled"`; for `line`/`area`: require `_on(spec, "color")` naming a series field; a donut/point on a comparison profile: leave to `_mark`'s existing rules (do not widen). Keep the existing `uses_series` branch for non-comparison profiles that nonetheless use a series. Comment the rule with Section D's row.
- [ ] **Step 4: Run** `tests/test_visual_plan_verifier.py` (the eight families must still verify — the floor's comparison bar carries colour + offset + `stack: disabled`; if it does not, STOP and report), then the four suite calls.
- [ ] **Step 5: Commit** — `fix(visual): a comparison keeps its series — colour and offset for a bar, colour for a line — and an offset never travels without its colour`.

---

### Task 3: One field set from profile to pixel

**Files:**
- Modify: `apps/orchestrator/src/finguard_orchestrator/infrastructure/mappers/llm_mapper.py` (~l.215-240: row serialisation + `fields`)
- Test: `apps/orchestrator/tests/test_visualization_stage1.py`
- Check (read, patch only if needed): `apps/web/src` — how rows are read (a `null` `series`/`occurred_at` must not break the client).

**Interfaces:**
- Consumes: `VisualProfile.field_names()` (the union the model was shown), `VisualRow(dimension, value_minor, series, occurred_at)`.
- Produces: every serialised row has exactly the keys `profile.field_names()` (`None` where the row lacks the value); `fields` passed to `valid_for` is that same set; a spec naming a declared field never disappears at the mapper.

- [ ] **Step 1: Failing tests**

```python
def test_every_row_carries_every_field_the_profile_declares() -> None:
    """The review's third blocker: the model was shown a field (the union), proposed it, the
    verifier accepted it, and the mapper — validating against the intersection — dropped the chart.
    One contract: rows carry what the profile says, None where a row has no value."""
    dataset = VisualDataset(metric="SUM", rows=(
        VisualRow("Comida", 100, series="junio"), VisualRow("Transporte", 50),   # second row: no series
    ))
    result = _decided(dataset)                             # the file's helper to a decided result
    payload = llm_mapper.question_to_dto(result, ...)      # as the file already calls it
    rows = payload.visualization.data
    declared = set(result.visual_decision.profile.field_names())
    assert all(set(row) == declared for row in rows)
    assert rows[1]["series"] is None


def test_a_chart_naming_a_declared_field_is_not_dropped_by_the_mapper() -> None:
    """A spec with `series` on color over the dataset above renders; at the base it vanished."""
    ...
    assert payload.visualization is not None and payload.visualization.chart_id
```

- [ ] **Step 2: Run** — FAIL (`KeyError`/set mismatch; the second test gets `None`).
- [ ] **Step 3: Implement** — serialise with `{"dimension": row.dimension, "value_minor": row.value_minor, "series": row.series, "occurred_at": row.occurred_at}` filtered to `profile.field_names()` (the decision's profile — `result.visual_decision.profile`; for the KPI branch, which has no decision, keep today's shape); `fields = frozenset(profile.field_names())`; delete the intersection. Then read `apps/web/src` for the code that consumes `visualization.data` (grep `data` in the chart component / `chartFamilies`) and confirm `null` values pass through to Vega-Lite (they do: Vega-Lite treats null as missing); add a fixture-level assertion in `apps/web/src/chartFamilies.test.ts` only if the exporter's fixture changes — Task 3 does NOT regenerate the fixture unless the exporter's families produce rows with mixed fields (check `git diff --stat apps/web/src/__fixtures__/chart-families.json` after `uv run python scripts/export_chart_fixtures.py`; if it changes, that is the contract taking effect — regenerate and commit it in the same commit, and run the web tests: `npm test` in `apps/web` — foreground, `timeout: 600000`).
- [ ] **Step 4: Run** — `tests/test_visualization_stage1.py tests/test_visualization_stage2_core.py tests/test_export_chart_fixtures.py`, then the four suite calls, then the web tests if the fixture moved.
- [ ] **Step 5: Commit** — `fix(visual): every row carries every field the profile declared, so a chart the verifier accepted is never dropped at the mapper`.

---

### Task 4: The adapter validates the whole answer, or refuses it whole

**Files:**
- Create: `apps/orchestrator/src/finguard_orchestrator/infrastructure/planning/visual_answer.py` — Pydantic models `VisualAnswer(mark: Mark | None, encodings: list[EncodingAnswer], sort: SortMode | None = None, sort_field: str | None = None, stack: Stack | None = None, limit: int | None = None, rationale: str | None = None)` with `model_config = ConfigDict(extra="forbid", strict=True)`; `EncodingAnswer(channel: Channel, field: str, type: FieldType, sort: SortAnswer | None = None)`; `SortAnswer(mode: SortMode, field: str | None = None)` — every closed set typed by the `Literal`s from `domain/models/chart.py`.
- Modify: `apps/orchestrator/src/finguard_orchestrator/infrastructure/planning/llm_visual_planner.py` (`_parse` validates with `VisualAnswer.model_validate_json`; a `ValidationError` is a structure failure; `_draft` builds from the model — no `.get`, no `str(...)` coercion)
- Test: `apps/orchestrator/tests/test_llm_visual_planner.py`

**Interfaces:**
- Produces: `parse_visual_answer(text: str) -> VisualAnswer | None` (None = structure failure); abstention = `VisualAnswer` with `mark is None`; `_draft(answer: VisualAnswer) -> VisualPlanDraft`.
- Invariant test: `set(get_args(Mark)) == set(VisualAnswer.model_fields["mark"].annotation's literals)` — simplest: a test that the schema's enums equal `get_args` of each `Literal` (already exists) plus one asserting the model refuses a mark/channel/type/stack/mode outside those sets and any extra key.

- [ ] **Step 1: Failing tests** — `{"encodings": []}` (no `mark` key) ⇒ repair path then `ProviderFailedError` (today: abstention); `{"mark": "bar", "encodings": [], "sort": "descending"}` (sort as a string, not an object) ⇒ structure failure (today: check what happens — the report says); `{"mark": "bar", "encodings": [], "rows": []}` (extra key) ⇒ structure failure (today: ignored); `{"mark": "bar", "encodings": [{"channel": "x", "field": "dimension", "type": "nominal", "extra": 1}]}` ⇒ structure failure; `{"mark": null, "encodings": []}` ⇒ abstention (`plan(...) is None`, one prompt); a valid answer ⇒ the same draft as today (the existing tests). Also: `limit: 5.0` and `limit: true` ⇒ structure failure (strict mode refuses float/bool for int — confirm; Pydantic strict rejects `True` for `int`).
- [ ] **Step 2: Run** — FAIL on the four new refusals.
- [ ] **Step 3: Implement** the models and rewire `_parse`/`_draft`; keep the abstention-before-structure order (`mark: null` is a valid `VisualAnswer` with `mark None`); the repair prompt and `ProviderFailedError(self._model, ...)` unchanged; log line unchanged. Remove the hand-written required-key checks the models now enforce (no duplicate gate).
- [ ] **Step 4: Run** `tests/test_llm_visual_planner.py tests/test_visual_planning_policy.py`, then the four suite calls; `uv run mypy src` (the models are in `src`).
- [ ] **Step 5: Commit** — `fix(visual): the adapter validates the provider's whole answer against the same closed sets the schema names, and refuses it whole`.

---

### Task 5: Suitability is a structure, not a mark

**Files:**
- Modify: `apps/orchestrator/scripts/evaluate_visual.py` (`Case.requires: dict | None`, `Case.instruction_effect: dict | None`; `judge`; `stability` sensitivity)
- Modify: `apps/orchestrator/evaluation/visual/cases.yaml` (add `requires:` to the comparison, ranking, share and recurrence cases; `instruction_effect:` to the two instructed cases)
- Test: `apps/orchestrator/tests/test_evaluate_visual.py`

**Interfaces:**
- `requires` keys (all optional): `channels` — list of `[channel, role]` pairs that must be present with a field of that role (`dimension`, `measure`, `series`; roles resolved through the profile); `series: true` — a series field must appear on `color` (and on an offset when the mark is `bar`); `order: [channel, mode]` — the axis carries that per-encoding sort mode (or the chart-wide sort equals it); `stack: <Stack> | null` — the spec's stack equals it (null = must be absent); `limit: true|false`.
- `instruction_effect` keys: `mark: <Mark>` — the proposal's mark equals it; `orientation: horizontal|vertical` — the dimension sits on `y`/`x`; `sort: <SortMode>`.
- `judge`: `suitable = mark in suitable_marks and requires_hold(spec, profile, requires)`; the `valid ∧ suitable ∧ changed` figure uses this `suitable`. `stability`: for an instructed case, `sensitive` iff `instruction_effect` holds on the proposal — a canonical difference alone no longer counts.

- [ ] Tests first (the file's constructed `VisualDecision`s: a comparison bar without series ⇒ not suitable; with colour+offset ⇒ suitable; the donut instruction on a bar that only changed orientation ⇒ not sensitive; a donut ⇒ sensitive); then the code; then the YAML annotations — written from Section D's mark table, one `requires` block per case whose family Section D constrains (state each in the case's comment); `suitable_marks` unchanged. Commit: `feat(eval): suitability requires the structure Section D names, and sensitivity requires the instruction's own effect`.

---

### Task 6: The visual planner is configured on its own

**Files:**
- Modify: `apps/orchestrator/src/finguard_orchestrator/bootstrap/config.py` (`visual_planner_provider: str | None = None`, `visual_planner_model: str | None = None`, `visual_planner_temperature: float | None = None`, `visual_planner_max_output_tokens: int | None = None` — each `None` meaning "the financial planner's"), `apps/orchestrator/.env.defaults` (four lines, each with its reason: choosing a better chart model must not move the natural-language → query precision that is worth 30 points), `bootstrap/main.py` (the wiring resolves each with `or settings.planner_*`; the provider through `providers.get(...)`), `scripts/evaluate_visual.py` (the manifest records the four resolved values; `RESULTS.md`'s latency row is labelled "added turn latency — in shadow/hybrid the answer waits for the planner" and the header names the visual model and provider), `tests/test_the_visual_planner_is_configurable.py` (defaults `None`; resolution to the planner's; an explicit visual model builds the adapter with it — asserted through `create_app()` as the sibling test does).
- Commit: `feat(config): the visual planner has its own provider, model, temperature and token limit, falling back to the financial planner's`.

---

### Task 7: The record on paper

- `docs/next.md` §9: one dated paragraph after 4.1's — the review's three contracts closed (comparison from the shape marker; a comparison keeps its series; one field set profile→pixel), the adapter's whole-answer validation, structural suitability, the separate configuration; what is still pending for promotion: the three GPU runs on one SHA and the mode decision (Task 9, the user's), stated with the thresholds already recorded.
- `docs/conversational-capabilities.md`: the visual planner row's Estado/Evidencia cells updated (still "apagada por defecto"; evidence now includes the structural suitability).
- Commit: `docs(visual): wave 3.5 — the three semantic contracts are closed; promotion still waits for its evidence`.

---

## Self-review

- **Coverage of the review:** blockers 1–3 → Tasks 1–3; parser → Task 4; evaluation → Task 5; configuration and the latency statement → Task 6; "run CI and three evaluations on one SHA, promote shadow → hybrid on thresholds" → Task 9 (the user's), thresholds already in `docs/next.md`.
- **Spec:** Section D's grouped-bar row and line/area comparison row are what Task 2 enforces; Section C is what Task 4 restores; Section G is what Task 5 sharpens.
- **Placeholders:** Task 2's test bodies name the shape and the expected rule/reason and leave the family builders to the verifier test file's own helpers; Task 5's YAML annotations are authored at implementation from Section D — the brief will list each case's `requires` explicitly (controller-authored, not the implementer's judgement).
- **Pre-read risks at dispatch:** (1) the floor's comparison bar — does it carry colour + offset + `stack: disabled` exactly (Task 2's gate)? (2) `test_visual_profile.py` builders and whether the exporter's `FAMILY_DATASETS` is importable from tests; (3) the web client's reading of `visualization.data` rows and whether the exporter fixture changes under Task 3; (4) Pydantic strict-mode behaviour for `int` vs `bool`/`float` in this Pydantic version; (5) the `.env.defaults` planner block's style for Task 6.
