# Visual Planner (waves 3-4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The model proposes a chart as a bounded draft, the domain authorises or refuses it, the policy runs the proposal beside the deterministic floor in `shadow`, every turn records what was proposed and what was shown, and an evaluation says whether the proposals are valid, suitable, stable under paraphrase, and different from the floor.

**Architecture:** `VisualPlannerPort` (domain, async) returns a `VisualPlanDraft` of plain strings; `VisualPlanVerifier` (domain, pure) turns a draft into a `ChartSpec` or a `Rejection` and never repairs; `VisualPlanningPolicy` (domain, async) holds the model, the floor and the verifier, enforces the budget, resolves abstention / timeout / provider error / rejection, and computes `changed_from_baseline` on canonicalised specs. `LlmVisualPlanner` (infrastructure) implements the port against the provider already loaded, with a value-free profile payload and a JSON schema built from the domain literals. The application seam `_decided_visual` becomes async, awaits the policy, and records one `Decision` plus a structured seven-field line. Nothing on the wire changes: in `shadow` the user sees the floor.

**Tech Stack:** Python 3.12, dataclasses, `Protocol`, `asyncio.wait_for`, pydantic-settings, pytest-asyncio (`asyncio_mode = "auto"`), the existing `LlmProviderPort` (`complete(prompt, *, model, temperature, max_output_tokens, schema)`), `observability.decisions.record`, PyYAML for the evaluation cases.

**Spec:** `docs/superpowers/specs/2026-09-03-llm-visual-planner-design.md` — Sections B (port, guarantee, the no-values test), C (the draft, the verifier's contract), D (the rules), E (the policy and its mode table), F (the record), G (measurement). Sections A and D's floor-side rules landed on `task/visual-foundation` (`23e7efd`); this plan starts there.

## Global Constraints

- **No second model.** `LlmVisualPlanner` uses `settings.planner_model` on `planner_provider`, with a different prompt. Loading another model evicts `qwen3.5:9b` from an 8 GB card.
- **The question may go to the provider; it never enters `VisualProfile`, the conversational state, a DTO, or a log.** `_own_words` in `observability/decisions.py` redacts anything not in the service's vocabulary — nothing holder-written is ever passed to `record()`.
- **The visual model never receives rows, amounts, movement descriptions or dimension values.** Enforced by a structural test on the payload's key set (spec B), not by "no digit appears".
- **The verifier refuses and never repairs.** `verify(draft, profile) -> ChartSpec | Rejection`.
- **In `shadow` the user sees the floor.** `accepted=False`, `selected_source="deterministic"`, `fallback_reason=None`.
- **`None` from the port means abstention and nothing else.** Timeout and provider failure are exceptions the policy resolves.
- **Every enumerated draft field is a plain `str`.** `ChartEncoding` / `ChartSpec` validate; typing the draft would move the refusal into the adapter.
- **No hand-copied domain literals.** Every allowed-value set comes from `typing.get_args(Mark | Channel | FieldType | Stack | SortMode)`. This branch's predecessor removed three copies of `Channel` after one caused a production bug.
- **`changed_from_baseline` compares canonicalised specs**: titles and encoding order do not count.
- **Three runs, never one number.** Any promotion figure is the minimum over three runs with identical manifests, following `calibrate_robustness_gate.py`'s rule.
- **Ruff line limit 100; no trailing pragma to dodge it.** Gates before every commit: `uv run --directory apps/orchestrator pytest -q -m "not integration" --deselect tests/test_conversation_corpus.py`, then `tests/test_conversation_corpus.py` alone, `ruff check .`, `ruff format --check .`, `mypy src`. Web only if `apps/web` is touched (it should not be).
- **Baseline at `23e7efd`: 3614 passed / 3 skipped / 192 deselected + 190 corpus at 0.0 %; web 112.** Recorded; do not re-measure. **The conversation corpus cannot see charts** (0 of 190 episodes reach `question_to_dto`) — a green corpus proves nothing about this plan.
- **Implementers work in a worktree cut from a named SHA**, never in the primary tree. Suites run in the foreground, blocking. Commit per item. Never add a `Co-Authored-By` trailer. Do not push.
- **Never edit a `Ceiling` or a `Floor`**, a dataset case or a corpus episode to move a number.

---

## File structure

| file | responsibility |
| --- | --- |
| `domain/models/visual_plan.py` (new) | `ProposedSort`, `ProposedEncoding`, `VisualPlanDraft`, `Rejection`, `canonical(spec)` — the proposal vocabulary and the equality that `changed_from_baseline` uses |
| `domain/ports/visual_planner.py` (new) | `VisualPlannerPort` |
| `domain/services/visual_plan_verifier.py` (new) | `verify(draft, profile) -> ChartSpec \| Rejection`, one function per rule |
| `domain/services/visual_planning_policy.py` (new) | `VisualPlanningPolicy`, `VisualMode`, `FallbackReason` |
| `domain/services/deterministic_visual_planner.py` (modify) | `VisualDecision` gains the record's fields |
| `infrastructure/planning/visual_prompt.py` (new) | `profile_payload`, `build_visual_prompt`, `visual_schema()` |
| `infrastructure/planning/llm_visual_planner.py` (new) | `LlmVisualPlanner` |
| `observability/decisions.py` (modify) | `Step.VISUAL_PLANNER` |
| `application/queries/ask_financial_question.py` (modify) | `_decided_visual` async, records, handler takes `visual_policy` |
| `bootstrap/config.py`, `bootstrap/main.py` (modify) | two settings, the wiring |
| `scripts/export_chart_fixtures.py` (modify) | `FAMILY_DATASETS` factored out so the evaluation shares the shapes |
| `evaluation/visual/cases.yaml` (new), `scripts/evaluate_visual.py` (new), `justfile` (modify) | the measurement |
| `tests/support/visual_planners.py` (new) and one test module per unit | |

All paths below are relative to `apps/orchestrator/src/finguard_orchestrator/` for source and `apps/orchestrator/` for tests, scripts and evaluation.

---

### Task 1: the draft, the rejection, and canonical equality

**Files:**
- Create: `domain/models/visual_plan.py`
- Test: `tests/test_visual_plan.py`

**Interfaces:**
- Consumes: `ChartSpec`, `ChartEncoding`, `EncodingSort` from `domain/models/chart.py`.
- Produces: `ProposedSort(mode: str, field: str | None = None)`; `ProposedEncoding(channel: str, field: str, type: str, sort: ProposedSort | None = None)`; `VisualPlanDraft(mark: str, encodings: tuple[ProposedEncoding, ...], sort: str | None = None, sort_field: str | None = None, stack: str | None = None, limit: int | None = None, rationale: str | None = None)`; `Rejection(rule: str, reason: str)`; `canonical(spec: ChartSpec) -> tuple[object, ...]`.

- [ ] **Step 1: Write the failing tests**

```python
"""The proposal vocabulary: plain strings on purpose, and the equality the baseline uses."""

from finguard_orchestrator.domain.models.chart import ChartEncoding, ChartSpec, EncodingSort
from finguard_orchestrator.domain.models.visual_plan import (
    ProposedEncoding,
    ProposedSort,
    Rejection,
    VisualPlanDraft,
    canonical,
)


def test_a_draft_accepts_any_string_because_the_domain_refuses_later() -> None:
    """`ChartSpec.__post_init__` is where "sideways" gets refused. A draft that raised here would
    turn a model error into an adapter exception, which is the split this type exists to prevent."""
    draft = VisualPlanDraft(
        mark="sideways",
        encodings=(ProposedEncoding("zOffset", "dimension", "nominal"),),
        stack="upside_down",
    )
    assert draft.mark == "sideways"
    assert draft.encodings[0].channel == "zOffset"


def test_a_rejection_names_its_rule() -> None:
    rejection = Rejection(rule="matrix", reason="a measure never goes in color")
    assert rejection.rule == "matrix"


def _bar(*, titled: bool, order: tuple[int, ...] = (0, 1, 2)) -> ChartSpec:
    encodings = (
        ChartEncoding("y", "dimension", "nominal", "Categoría" if titled else None,
                      sort=EncodingSort(mode="descending", field="value_minor")),
        ChartEncoding("x", "value_minor", "quantitative", "Importe" if titled else None),
        ChartEncoding("tooltip", "dimension", "nominal"),
    )
    return ChartSpec(mark="bar", encodings=tuple(encodings[i] for i in order))


def test_canonical_ignores_titles_and_encoding_order() -> None:
    """Reordering encodings or retitling an axis is not a different visual decision, and counting
    it as one would inflate the number that is supposed to prove the planner adds something."""
    assert canonical(_bar(titled=True)) == canonical(_bar(titled=False, order=(2, 0, 1)))


def test_canonical_sees_a_different_mark_channel_or_sort() -> None:
    base = canonical(_bar(titled=False))
    swapped = ChartSpec(
        mark="bar",
        encodings=(
            ChartEncoding("x", "dimension", "nominal",
                          sort=EncodingSort(mode="descending", field="value_minor")),
            ChartEncoding("y", "value_minor", "quantitative"),
            ChartEncoding("tooltip", "dimension", "nominal"),
        ),
    )
    unsorted = ChartSpec(
        mark="bar",
        encodings=(
            ChartEncoding("y", "dimension", "nominal"),
            ChartEncoding("x", "value_minor", "quantitative"),
            ChartEncoding("tooltip", "dimension", "nominal"),
        ),
    )
    assert canonical(swapped) != base          # orientation is a decision
    assert canonical(unsorted) != base         # the sort is a decision
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --directory apps/orchestrator pytest tests/test_visual_plan.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'finguard_orchestrator.domain.models.visual_plan'`.

- [ ] **Step 3: Write the module**

```python
"""What the model proposes, before the domain has said whether it is a chart.

Every enumerated field is a plain `str` on purpose. `ChartEncoding` and `ChartSpec` validate
channel, type, mark and stack in their own `__post_init__`; typing them here would move the
refusal back into the adapter, and a model error would become an exception where it should be a
`Rejection` with a reason. The repository already holds this shape in `FinancialPlanDraft`.
"""

from dataclasses import dataclass

from finguard_orchestrator.domain.models.chart import ChartSpec


@dataclass(frozen=True)
class ProposedSort:
    mode: str  # input | ascending | descending -- validated by EncodingSort, not here
    field: str | None = None


@dataclass(frozen=True)
class ProposedEncoding:
    channel: str
    field: str
    type: str
    sort: ProposedSort | None = None


@dataclass(frozen=True)
class VisualPlanDraft:
    mark: str
    encodings: tuple[ProposedEncoding, ...]
    sort: str | None = None
    sort_field: str | None = None
    stack: str | None = None
    limit: int | None = None
    rationale: str | None = None


@dataclass(frozen=True)
class Rejection:
    """Why a draft is not a chart. `rule` is the verifier's own name for the check, so a
    rejection rate can be read per rule, and `reason` is a sentence in the service's vocabulary."""

    rule: str
    reason: str


def canonical(spec: ChartSpec) -> tuple[object, ...]:
    """The part of a spec that is a visual *decision*.

    Titles are presentation, and the order encodings were appended in is an accident of which
    rule ran last. Everything else -- mark, which field sits on which channel with what type and
    what per-encoding order, stack, the chart-wide sort, the limit -- is what `changed_from_baseline`
    means by "different".
    """
    encodings = tuple(
        sorted(
            (
                item.channel,
                item.field,
                item.type,
                (item.sort.mode, item.sort.field) if item.sort is not None else None,
            )
            for item in spec.encodings
        )
    )
    return (spec.mark, encodings, spec.stack, spec.sort, spec.sort_field, spec.limit)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --directory apps/orchestrator pytest tests/test_visual_plan.py -v`
Expected: PASS, 4 tests.

- [ ] **Step 5: Gates, then commit**

```bash
git add apps/orchestrator/src/finguard_orchestrator/domain/models/visual_plan.py apps/orchestrator/tests/test_visual_plan.py
git commit -m "feat(visual): the draft the model proposes, and the equality a baseline comparison uses

Plain strings on purpose: ChartSpec refuses, the adapter does not. canonical() is
what changed_from_baseline compares, so retitling an axis or appending encodings in
another order cannot count as a different decision."
```

---

### Task 2: the port, and a fake that can be scripted

**Files:**
- Create: `domain/ports/visual_planner.py`
- Create: `tests/support/visual_planners.py`
- Test: `tests/test_visual_planner_port.py`

**Interfaces:**
- Consumes: `VisualPlanDraft` (Task 1), `VisualProfile`, `PresentationContext` from `chart.py`.
- Produces: `VisualPlannerPort` with `async def plan(self, question: str, profile: VisualProfile, presentation: PresentationContext, *, budget_seconds: float) -> VisualPlanDraft | None`; test helper `ScriptedVisualPlanner(outcome)` where `outcome` is a `VisualPlanDraft`, `None`, an exception instance to raise, or a float seconds to sleep before returning `None`; it records `received: list[tuple[str, VisualProfile, PresentationContext, float]]`. The same module exports `BREAKDOWN_PROFILE` (a two-row SUM breakdown profile) and `VERTICAL_DRAFT` (the floor's ranked bar turned vertical — valid and semantically different), shared by Tasks 4 and 5.

- [ ] **Step 1: Write the failing test**

```python
import asyncio

import pytest

from finguard_orchestrator.domain.models.chart import DEFAULT_PRESENTATION, VisualDataset, VisualRow
from finguard_orchestrator.domain.models.visual_plan import ProposedEncoding, VisualPlanDraft
from finguard_orchestrator.domain.ports.visual_planner import VisualPlannerPort
from finguard_orchestrator.domain.services.visual_profile import visual_profile_of
from tests.support.visual_planners import ScriptedVisualPlanner


def _profile():
    return visual_profile_of(
        VisualDataset(metric="SUM", rows=(VisualRow("A", 100), VisualRow("B", 50))),
        total_minor=None,
        group_by="NONE",
    )


async def test_the_scripted_planner_conforms_to_the_port_and_records_what_it_saw() -> None:
    draft = VisualPlanDraft("bar", (ProposedEncoding("y", "dimension", "nominal"),))
    planner = ScriptedVisualPlanner(draft)
    assert isinstance(planner, VisualPlannerPort)

    got = await planner.plan("¿en qué gasto más?", _profile(), DEFAULT_PRESENTATION, budget_seconds=2.0)

    assert got is draft
    assert planner.received[0][0] == "¿en qué gasto más?"
    assert planner.received[0][3] == 2.0


async def test_the_scripted_planner_can_abstain_raise_or_stall() -> None:
    assert await ScriptedVisualPlanner(None).plan("q", _profile(), DEFAULT_PRESENTATION, budget_seconds=1) is None
    with pytest.raises(RuntimeError):
        await ScriptedVisualPlanner(RuntimeError("down")).plan("q", _profile(), DEFAULT_PRESENTATION, budget_seconds=1)
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(
            ScriptedVisualPlanner(0.2).plan("q", _profile(), DEFAULT_PRESENTATION, budget_seconds=0.05),
            timeout=0.05,
        )
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --directory apps/orchestrator pytest tests/test_visual_planner_port.py -v`
Expected: FAIL with `ModuleNotFoundError` for `domain.ports.visual_planner`.

- [ ] **Step 3: Write the port and the fake**

`domain/ports/visual_planner.py`:

```python
"""The one contract a visual planner fulfils. See the design's Section B."""

from typing import Protocol, runtime_checkable

from finguard_orchestrator.domain.models.chart import PresentationContext, VisualProfile
from finguard_orchestrator.domain.models.visual_plan import VisualPlanDraft


@runtime_checkable
class VisualPlannerPort(Protocol):
    async def plan(
        self,
        question: str,
        profile: VisualProfile,
        presentation: PresentationContext,
        *,
        budget_seconds: float,
    ) -> VisualPlanDraft | None:
        """Propose a chart for this profile, or return `None` to abstain.

        `None` means abstention and nothing else. A provider that cannot answer raises
        `ProviderFailedError`; a call that outlives `budget_seconds` is cancelled by the policy,
        which is the only place that holds the floor to fall back to. The question is an argument
        and never a profile field: the model sees it, nothing that round-trips does.
        """
        ...
```

`tests/support/visual_planners.py`:

```python
"""A visual planner a test can script: a draft, an abstention, a failure or a stall."""

import asyncio
from dataclasses import dataclass, field

from finguard_orchestrator.domain.models.chart import (
    PresentationContext,
    VisualDataset,
    VisualProfile,
    VisualRow,
)
from finguard_orchestrator.domain.models.visual_plan import (
    ProposedEncoding,
    ProposedSort,
    VisualPlanDraft,
)
from finguard_orchestrator.domain.services.visual_profile import visual_profile_of

# Shared by the policy and seam tests, so a change to "what a breakdown looks like" is one edit.
BREAKDOWN_PROFILE = visual_profile_of(
    VisualDataset(metric="SUM", rows=(VisualRow("ALQUILER", 51_300), VisualRow("OCIO", 4_000))),
    total_minor=None,
    group_by="NONE",
)
# The floor draws a horizontal ranked bar; this is the same chart turned vertical -- valid, and
# semantically different, which is what changed_from_baseline exists to see.
VERTICAL_DRAFT = VisualPlanDraft(
    "bar",
    (
        ProposedEncoding("x", "dimension", "nominal", ProposedSort("descending", "value_minor")),
        ProposedEncoding("y", "value_minor", "quantitative"),
    ),
)


@dataclass
class ScriptedVisualPlanner:
    outcome: VisualPlanDraft | BaseException | float | None
    received: list[tuple[str, VisualProfile, PresentationContext, float]] = field(default_factory=list)

    async def plan(
        self,
        question: str,
        profile: VisualProfile,
        presentation: PresentationContext,
        *,
        budget_seconds: float,
    ) -> VisualPlanDraft | None:
        self.received.append((question, profile, presentation, budget_seconds))
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        if isinstance(self.outcome, float):
            await asyncio.sleep(self.outcome)
            return None
        return self.outcome
```

If `tests/support/` has no `__init__.py`, check how `tests/support/episodes.py` is imported by `test_conversation_corpus.py` and follow that exactly.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run --directory apps/orchestrator pytest tests/test_visual_planner_port.py -v`
Expected: PASS, 2 tests.

- [ ] **Step 5: Gates, then commit**

```bash
git add apps/orchestrator/src/finguard_orchestrator/domain/ports/visual_planner.py apps/orchestrator/tests/support/visual_planners.py apps/orchestrator/tests/test_visual_planner_port.py
git commit -m "feat(visual): the planner port, async and value-free, and a fake a test can script"
```

---

### Task 3: the verifier — refuses, never repairs

**Files:**
- Create: `domain/services/visual_plan_verifier.py`
- Test: `tests/test_visual_plan_verifier.py`

**Interfaces:**
- Consumes: Task 1 types; `ChartSpec`, `ChartEncoding`, `EncodingSort`, `VisualProfile`, `VisualField`, `Mark`, `Channel`, `Stack`, `SortMode`, `FieldType` from `chart.py`; `DeterministicVisualPlanner`, `visual_profile_of`.
- Produces: `verify(draft: VisualPlanDraft, profile: VisualProfile) -> ChartSpec | Rejection`.

The rules, in the order they run (spec Section D). Each is a function `-> Rejection | None`; the first rejection wins, so a test can name the rule it expects.

| order | rule name | refuses |
| --- | --- | --- |
| 1 | `structure` | anything `ChartEncoding`/`ChartSpec.__post_init__` raises on (unknown mark/channel/type/stack, duplicate channels, stacked donut, `limit` outside 1..100, `sort_field` without `sort`, stack without a quantitative axis) |
| 2 | `fields` | a field the profile does not have |
| 3 | `types` | `proposed.type != profile_field.type` |
| 4 | `matrix` | a (role, channel) pair outside the table: `dimension` nominal → x,y,color,tooltip; `dimension` temporal → x,y,tooltip; `measure` → x,y,theta,tooltip; `series` → color,xOffset,yOffset,tooltip |
| 5 | `mark` | per mark: `bar` dimension and measure on opposite axes; `line`/`area` a temporal dimension on one axis and the measure on the other; `point` `occurred_at` temporal on x, nominal dimension on y, measure only in tooltip; `donut` measure in theta, one nominal dimension in color, no x/y/offsets |
| 6 | `donut_claims` | a donut when not (`positive` and `mutually_exclusive` and `verified_total`) |
| 7 | `grouped` | series present on a categorical bar without (offset on the dimension's axis and `stack == "disabled"`); or `color` and the offset naming different fields; or an offset whose field is not a `series` |
| 8 | `limit` | `limit` unless mark is `bar`, no series, and the dimension's `order == "MEASURE_DESC"` |
| 9 | `order` | per field: `MEASURE_DESC` needs a descending sort by the measure (per-encoding or chart-wide); `INPUT` refuses any sort other than `input`; `TEMPORAL` refuses a per-encoding sort naming another field; `NONE` refuses a sort on that field |

- [ ] **Step 1: Write the failing tests**

```python
"""One test per rule, plus the one that matters most: the floor's own chart verifies."""

from finguard_orchestrator.domain.models.chart import (
    DEFAULT_PRESENTATION,
    ChartSpec,
    VisualDataset,
    VisualRow,
)
from finguard_orchestrator.domain.models.visual_plan import (
    ProposedEncoding as E,
    ProposedSort as S,
    Rejection,
    VisualPlanDraft as D,
    canonical,
)
from finguard_orchestrator.domain.services.deterministic_visual_planner import (
    DeterministicVisualPlanner,
)
from finguard_orchestrator.domain.services.visual_plan_verifier import verify
from finguard_orchestrator.domain.services.visual_profile import visual_profile_of


def breakdown():
    return visual_profile_of(
        VisualDataset(metric="SUM", rows=(VisualRow("ALQUILER", 51_300), VisualRow("OCIO", 4_000))),
        total_minor=None, group_by="NONE",
    )


def monthly():
    return visual_profile_of(
        VisualDataset(metric="SUM", rows=(VisualRow("2026-01", 100), VisualRow("2026-02", 200))),
        total_minor=None, group_by="MONTH",
    )


def comparison():
    return visual_profile_of(
        VisualDataset(metric="SUM", rows=(
            VisualRow("ALQUILER", 100, series="2026-07"), VisualRow("ALQUILER", 90, series="2026-06"),
            VisualRow("OCIO", 50, series="2026-07"), VisualRow("OCIO", 70, series="2026-06"),
        )),
        total_minor=None, group_by="NONE",
    )


def share():
    return visual_profile_of(
        VisualDataset(metric="RATIO_SHARE", rows=(VisualRow("Parte", 70), VisualRow("Resto", 30))),
        total_minor=100, group_by="NONE",
    )


def ranked_bar(**overrides):
    fields = dict(
        mark="bar",
        encodings=(
            E("y", "dimension", "nominal", S("descending", "value_minor")),
            E("x", "value_minor", "quantitative"),
        ),
    )
    fields.update(overrides)
    return D(**fields)


def rule_of(result) -> str | None:
    return result.rule if isinstance(result, Rejection) else None


def test_the_floors_own_chart_verifies_as_itself() -> None:
    """If the verifier refused what the floor draws, the baseline would be uncomparable."""
    profile = breakdown()
    floor = DeterministicVisualPlanner().plan(profile, DEFAULT_PRESENTATION)
    assert floor is not None
    draft = D(
        floor.mark,
        tuple(E(e.channel, e.field, e.type, S(e.sort.mode, e.sort.field) if e.sort else None)
              for e in floor.encodings),
        sort=floor.sort, sort_field=floor.sort_field, stack=floor.stack, limit=floor.limit,
    )
    verified = verify(draft, profile)
    assert isinstance(verified, ChartSpec)
    assert canonical(verified) == canonical(floor)


def test_structure_is_the_first_rule() -> None:
    assert rule_of(verify(ranked_bar(mark="sideways"), breakdown())) == "structure"
    assert rule_of(verify(ranked_bar(encodings=(E("zOffset", "dimension", "nominal"),)), breakdown())) == "structure"


def test_an_absent_field_and_a_wrong_type_are_refused() -> None:
    assert rule_of(verify(ranked_bar(encodings=(
        E("y", "categoria", "nominal"), E("x", "value_minor", "quantitative"))), breakdown())) == "fields"
    assert rule_of(verify(ranked_bar(encodings=(
        E("y", "dimension", "quantitative"), E("x", "value_minor", "quantitative"))), breakdown())) == "types"


def test_the_matrix_refuses_a_measure_in_color() -> None:
    draft = D("bar", (E("y", "dimension", "nominal"), E("x", "value_minor", "quantitative"),
                      E("color", "value_minor", "quantitative")))
    assert rule_of(verify(draft, breakdown())) == "matrix"


def test_a_bar_needs_opposite_axes_and_a_line_needs_time() -> None:
    same_axis = D("bar", (E("x", "dimension", "nominal"), E("y", "value_minor", "quantitative"),
                          E("tooltip", "dimension", "nominal")))
    assert isinstance(verify(same_axis, breakdown()), ChartSpec)      # x/y either way round is fine
    both_on_x = D("bar", (E("x", "dimension", "nominal"), E("tooltip", "value_minor", "quantitative")))
    assert rule_of(verify(both_on_x, breakdown())) == "mark"
    line_over_categories = D("line", (E("x", "dimension", "nominal"), E("y", "value_minor", "quantitative")))
    assert rule_of(verify(line_over_categories, breakdown())) == "mark"
    line_over_months = D("line", (E("x", "dimension", "temporal"), E("y", "value_minor", "quantitative")))
    assert isinstance(verify(line_over_months, monthly()), ChartSpec)


def test_a_donut_needs_the_three_claims() -> None:
    donut = D("donut", (E("theta", "value_minor", "quantitative"), E("color", "dimension", "nominal")))
    assert isinstance(verify(donut, share()), ChartSpec)
    assert rule_of(verify(donut, breakdown())) == "donut_claims"     # no verified total


def test_a_grouped_comparison_needs_offset_and_disabled_stack() -> None:
    grouped = D("bar", (
        E("y", "dimension", "nominal", S("descending", "value_minor")),
        E("x", "value_minor", "quantitative"),
        E("color", "series", "nominal", S("input")),
        E("yOffset", "series", "nominal", S("input")),
    ), stack="disabled")
    assert isinstance(verify(grouped, comparison()), ChartSpec)
    assert rule_of(verify(D(grouped.mark, grouped.encodings, stack=None), comparison())) == "grouped"
    wrong_axis = D(grouped.mark, grouped.encodings[:3] + (E("xOffset", "series", "nominal", S("input")),),
                   stack="disabled")
    assert rule_of(verify(wrong_axis, comparison())) == "grouped"


def test_limit_belongs_to_one_family() -> None:
    assert isinstance(verify(ranked_bar(limit=5), breakdown()), ChartSpec)
    assert rule_of(verify(D("line", (E("x", "dimension", "temporal"), E("y", "value_minor", "quantitative")),
                              limit=5), monthly())) == "limit"


def test_order_follows_the_profile_not_the_type() -> None:
    frequency = visual_profile_of(
        VisualDataset(metric="FREQUENCY", rows=(VisualRow("lunes", 4), VisualRow("martes", 9))),
        total_minor=None, group_by="NONE",
    )
    by_value = D("bar", (E("x", "dimension", "nominal", S("descending", "value_minor")),
                         E("y", "value_minor", "quantitative")))
    assert rule_of(verify(by_value, frequency)) == "order"
    in_order = D("bar", (E("x", "dimension", "nominal", S("input")), E("y", "value_minor", "quantitative")))
    assert isinstance(verify(in_order, frequency), ChartSpec)
    unranked = D("bar", (E("y", "dimension", "nominal"), E("x", "value_minor", "quantitative")))
    assert rule_of(verify(unranked, breakdown())) == "order"        # MEASURE_DESC wants the ranking
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --directory apps/orchestrator pytest tests/test_visual_plan_verifier.py -v`
Expected: FAIL with `ModuleNotFoundError` for `visual_plan_verifier`.

- [ ] **Step 3: Write the verifier**

```python
"""The domain authorises what the model proposed, or says which rule it broke. Never repairs.

A repaired plan is a disagreement nobody can see -- `plan_verifier`'s reason, applied to charts.
Each rule is a function returning `Rejection | None`; the first rejection wins, so a rejection
rate can be read per rule and a test can name the rule it expects.
"""

from collections.abc import Callable

from finguard_orchestrator.domain.models.chart import (
    ChartEncoding,
    ChartSpec,
    EncodingSort,
    VisualField,
    VisualProfile,
)
from finguard_orchestrator.domain.models.visual_plan import Rejection, VisualPlanDraft

_MATRIX: dict[tuple[str, str], frozenset[str]] = {
    ("dimension", "nominal"): frozenset({"x", "y", "color", "tooltip"}),
    ("dimension", "temporal"): frozenset({"x", "y", "tooltip"}),
    ("measure", "quantitative"): frozenset({"x", "y", "theta", "tooltip"}),
    ("series", "nominal"): frozenset({"color", "xOffset", "yOffset", "tooltip"}),
}
_AXES = frozenset({"x", "y"})
_OFFSETS = frozenset({"xOffset", "yOffset"})
_OFFSET_FOR = {"x": "xOffset", "y": "yOffset"}


def verify(draft: VisualPlanDraft, profile: VisualProfile) -> ChartSpec | Rejection:
    built = _structure(draft)
    if isinstance(built, Rejection):
        return built
    for rule in (_fields, _types, _matrix, _mark, _donut_claims, _grouped, _limit, _order):
        rejected = rule(built, profile)
        if rejected is not None:
            return rejected
    return built


def _structure(draft: VisualPlanDraft) -> ChartSpec | Rejection:
    try:
        encodings = tuple(
            ChartEncoding(
                item.channel,  # type: ignore[arg-type]  -- the point: ChartEncoding refuses
                item.field,
                item.type,  # type: ignore[arg-type]
                sort=(
                    EncodingSort(item.sort.mode, item.sort.field)  # type: ignore[arg-type]
                    if item.sort is not None
                    else None
                ),
            )
            for item in draft.encodings
        )
        return ChartSpec(
            mark=draft.mark,  # type: ignore[arg-type]
            encodings=encodings,
            stack=draft.stack,  # type: ignore[arg-type]
            sort=draft.sort,  # type: ignore[arg-type]
            sort_field=draft.sort_field,
            limit=draft.limit,
        )
    except ValueError as exc:
        return Rejection("structure", str(exc))


def _fields(spec: ChartSpec, profile: VisualProfile) -> Rejection | None:
    missing = sorted(spec.fields() - profile.field_names())
    return Rejection("fields", f"unknown field {missing[0]}") if missing else None


def _types(spec: ChartSpec, profile: VisualProfile) -> Rejection | None:
    for item in spec.encodings:
        held = profile.field(item.field)
        if held is not None and held.type != item.type:
            return Rejection("types", f"{item.field} is {held.type}, not {item.type}")
    return None


def _matrix(spec: ChartSpec, profile: VisualProfile) -> Rejection | None:
    for item in spec.encodings:
        held = profile.field(item.field)
        assert held is not None  # _fields ran first
        allowed = _MATRIX.get((held.role, held.type), frozenset())
        if item.channel not in allowed:
            return Rejection("matrix", f"{held.role} {held.type} cannot take {item.channel}")
    return None


def _on(spec: ChartSpec, channel: str) -> ChartEncoding | None:
    return next((item for item in spec.encodings if item.channel == channel), None)


def _mark(spec: ChartSpec, profile: VisualProfile) -> Rejection | None:
    x, y = _on(spec, "x"), _on(spec, "y")
    roles = {item.field: profile.field(item.field) for item in spec.encodings}

    def role_on(item: ChartEncoding | None) -> str | None:
        return roles[item.field].role if item is not None and roles[item.field] else None

    if spec.mark == "bar":
        if {role_on(x), role_on(y)} != {"dimension", "measure"}:
            return Rejection("mark", "a bar puts the dimension and the measure on opposite axes")
    elif spec.mark in {"line", "area"}:
        axes = [item for item in (x, y) if item is not None]
        if len(axes) != 2 or not any(i.type == "temporal" for i in axes) or {role_on(x), role_on(y)} != {"dimension", "measure"}:
            return Rejection("mark", f"a {spec.mark} needs a temporal dimension on one axis and the measure on the other")
    elif spec.mark == "point":
        if x is None or x.field != "occurred_at" or x.type != "temporal" or role_on(y) != "dimension":
            return Rejection("mark", "a point chart puts occurred_at on x and the dimension on y")
        if any(item.channel in _AXES and roles[item.field] and roles[item.field].role == "measure" for item in spec.encodings):
            return Rejection("mark", "a point chart carries the amount only in tooltip")
    elif spec.mark == "donut":
        theta, color = _on(spec, "theta"), _on(spec, "color")
        if role_on(theta) != "measure" or role_on(color) != "dimension" or color.type != "nominal":
            return Rejection("mark", "a donut puts the measure in theta and one nominal dimension in color")
        if x is not None or y is not None or any(item.channel in _OFFSETS for item in spec.encodings):
            return Rejection("mark", "a donut has no axes and no offsets")
    return None


def _donut_claims(spec: ChartSpec, profile: VisualProfile) -> Rejection | None:
    if spec.mark != "donut":
        return None
    if not (profile.positive and profile.mutually_exclusive and profile.verified_total):
        return Rejection("donut_claims", "a donut asserts positive, exclusive parts of a verified whole")
    return None


def _grouped(spec: ChartSpec, profile: VisualProfile) -> Rejection | None:
    series = {item.name for item in profile.role("series")}
    offsets = [item for item in spec.encodings if item.channel in _OFFSETS]
    for item in offsets:
        if item.field not in series:
            return Rejection("grouped", "an offset takes only a series field")
    color = _on(spec, "color")
    if offsets and color is not None and color.field != offsets[0].field:
        return Rejection("grouped", "color and the offset must name the same series")
    uses_series = any(item.field in series for item in spec.encodings if item.channel != "tooltip")
    if spec.mark == "bar" and uses_series and not profile.temporal():
        dimension = next((i for i in spec.encodings if i.channel in _AXES and profile.field(i.field).role == "dimension"), None)
        wanted = _OFFSET_FOR[dimension.channel] if dimension is not None else None
        if wanted is None or _on(spec, wanted) is None:
            return Rejection("grouped", "a grouped comparison needs the offset on the dimension's axis")
        if spec.stack != "disabled":
            return Rejection("grouped", "a comparison is grouped, never stacked: stack must be disabled")
    return None


def _limit(spec: ChartSpec, profile: VisualProfile) -> Rejection | None:
    if spec.limit is None:
        return None
    dimension = next((f for f in profile.role("dimension") if f.type == "nominal"), None)
    if spec.mark != "bar" or profile.role("series") or dimension is None or dimension.order != "MEASURE_DESC":
        return Rejection("limit", "limit is a ranking transform: categorical bar, single series, MEASURE_DESC only")
    return None


def _order(spec: ChartSpec, profile: VisualProfile) -> Rejection | None:
    measure = profile.role("measure")
    measure_name = measure[0].name if measure else None
    for item in spec.encodings:
        if item.channel == "tooltip":
            continue
        held = profile.field(item.field)
        if held is None or held.role != "dimension":
            continue
        sort = item.sort
        chart_wide = spec.sort is not None and (spec.sort_field == item.field or spec.sort_field is None)
        if held.order == "MEASURE_DESC":
            ranked = sort is not None and sort.mode == "descending" and sort.field == measure_name
            ranked_wide = spec.sort == "descending" and spec.sort_field == measure_name
            if not (ranked or ranked_wide):
                return Rejection("order", f"{item.field} ranks by magnitude: descending by {measure_name}")
        elif held.order == "INPUT":
            if (sort is not None and sort.mode != "input") or (chart_wide and spec.sort != "input"):
                return Rejection("order", f"{item.field} keeps the producer's order")
        elif held.order == "TEMPORAL":
            if sort is not None and sort.field not in (None, item.field):
                return Rejection("order", f"{item.field} is ordered by time, not by {sort.field}")
        elif held.order == "NONE" and sort is not None:
            return Rejection("order", f"ordering does not apply to {item.field} on a {spec.mark}")
    return None
```

Read `ChartSpec.fields()` and `VisualProfile.field_names()` before relying on them — both exist on `23e7efd`; if `fields()` has a different name, use the real one and say so.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run --directory apps/orchestrator pytest tests/test_visual_plan_verifier.py -v`
Expected: PASS, 9 tests. `test_the_floors_own_chart_verifies_as_itself` is the one that must pass first — if it does not, the verifier is stricter than the floor and the rules, not the floor, need reading.

- [ ] **Step 5: Gates, then commit**

```bash
git add apps/orchestrator/src/finguard_orchestrator/domain/services/visual_plan_verifier.py apps/orchestrator/tests/test_visual_plan_verifier.py
git commit -m "feat(visual): the verifier authorises a draft or names the rule it broke, and never repairs"
```

---

### Task 4: the policy — both planners at once, four modes, a budget

**Files:**
- Create: `domain/services/visual_planning_policy.py`
- Modify: `domain/services/deterministic_visual_planner.py` (`VisualDecision` gains fields)
- Test: `tests/test_visual_planning_policy.py`

**Interfaces:**
- Consumes: Tasks 1-3; `DeterministicVisualPlanner`, `VisualDecision`.
- Produces: `VisualMode = Literal["deterministic", "shadow", "hybrid", "promoted"]`; `FallbackReason = Literal["abstention", "timeout", "provider_error", "invalid_proposal"]`; `VisualPlanningPolicy(model: VisualPlannerPort | None, fallback: DeterministicVisualPlanner, verifier: Callable[[VisualPlanDraft, VisualProfile], ChartSpec | Rejection], mode: VisualMode, budget_seconds: float)` with `async def decide(self, question: str, profile: VisualProfile, presentation: PresentationContext) -> VisualDecision | None`. `VisualDecision` gains `proposed: bool = False`, `valid: bool = False`, `accepted: bool = False`, `changed_from_baseline: bool | None = None`, `latency_ms: int | None = None`, `proposed_spec: ChartSpec | None = None`, `rejected_rule: str | None = None`.

**Rulings the spec leaves open, decided here:**
- `hybrid` accepts any proposal the verifier passes; its "gates" are the verifier. `promoted` differs in one operational way: the floor is computed **only** when the model fails, so `changed_from_baseline` is `None` there — a mode that trusts the model does not pay for the baseline on every turn. Both fall back to the floor identically.
- The budget is enforced by the policy with `asyncio.wait_for`, because only the policy holds the floor to fall back to; the adapter receives `budget_seconds` and may ignore it.
- A proposal that the model returns after the verifier refuses it is `fallback_reason="invalid_proposal"` with `rejected_rule` set; the proposal is still recorded in `proposed_spec` **only if** it built a `ChartSpec` (structure passed) — a structurally broken draft has no spec to record.

- [ ] **Step 1: Write the failing tests**

```python
"""Every row of the design's mode table, plus the budget and the four fallback reasons."""

import asyncio

import pytest

from finguard_orchestrator.domain.errors import ProviderFailedError
from finguard_orchestrator.domain.models.chart import DEFAULT_PRESENTATION, VisualDataset, VisualRow
from finguard_orchestrator.domain.models.visual_plan import ProposedEncoding as E, ProposedSort as S, VisualPlanDraft as D, canonical
from finguard_orchestrator.domain.services.deterministic_visual_planner import DeterministicVisualPlanner
from finguard_orchestrator.domain.services.visual_plan_verifier import verify
from finguard_orchestrator.domain.services.visual_planning_policy import VisualPlanningPolicy
from finguard_orchestrator.domain.services.visual_profile import visual_profile_of
from tests.support.visual_planners import (
    BREAKDOWN_PROFILE as PROFILE,
    ScriptedVisualPlanner,
    VERTICAL_DRAFT as VERTICAL,
)

SIDEWAYS = D("sideways", (E("x", "dimension", "nominal"),))


def policy(model, mode, budget=1.0):
    return VisualPlanningPolicy(model=model, fallback=DeterministicVisualPlanner(), verifier=verify, mode=mode, budget_seconds=budget)


async def test_deterministic_never_runs_the_model() -> None:
    model = ScriptedVisualPlanner(VERTICAL)
    decision = await policy(model, "deterministic").decide("q", PROFILE, DEFAULT_PRESENTATION)
    assert model.received == []
    assert decision.source == "deterministic" and decision.proposed is False and decision.accepted is False


async def test_shadow_runs_both_and_shows_the_floor() -> None:
    decision = await policy(ScriptedVisualPlanner(VERTICAL), "shadow").decide("q", PROFILE, DEFAULT_PRESENTATION)
    floor = DeterministicVisualPlanner().plan(PROFILE, DEFAULT_PRESENTATION)
    assert canonical(decision.spec) == canonical(floor)             # the user gets the floor
    assert decision.proposed and decision.valid and not decision.accepted
    assert decision.source == "deterministic" and decision.fallback_reason is None
    assert decision.changed_from_baseline is True                   # and the comparison was collected
    assert canonical(decision.proposed_spec) == canonical(verify(VERTICAL, PROFILE))
    assert decision.latency_ms is not None


async def test_hybrid_shows_a_valid_proposal() -> None:
    decision = await policy(ScriptedVisualPlanner(VERTICAL), "hybrid").decide("q", PROFILE, DEFAULT_PRESENTATION)
    assert decision.accepted and decision.source == "llm"
    assert canonical(decision.spec) == canonical(verify(VERTICAL, PROFILE))


async def test_hybrid_falls_back_on_a_rejected_proposal_and_names_the_rule() -> None:
    decision = await policy(ScriptedVisualPlanner(SIDEWAYS), "hybrid").decide("q", PROFILE, DEFAULT_PRESENTATION)
    assert decision.source == "deterministic" and decision.fallback_reason == "invalid_proposal"
    assert decision.rejected_rule == "structure" and decision.proposed_spec is None


@pytest.mark.parametrize(
    ("outcome", "reason"),
    [(None, "abstention"), (ProviderFailedError("down"), "provider_error"), (0.5, "timeout")],
)
async def test_the_three_failures_are_told_apart(outcome, reason) -> None:
    decision = await policy(ScriptedVisualPlanner(outcome), "hybrid", budget=0.05).decide("q", PROFILE, DEFAULT_PRESENTATION)
    assert decision.source == "deterministic" and decision.fallback_reason == reason
    # A timeout never learns whether the provider was reached; the other two did reach it.
    assert decision.proposed is (reason != "timeout")


async def test_promoted_does_not_pay_for_the_baseline() -> None:
    decision = await policy(ScriptedVisualPlanner(VERTICAL), "promoted").decide("q", PROFILE, DEFAULT_PRESENTATION)
    assert decision.accepted and decision.changed_from_baseline is None


async def test_a_single_row_never_reaches_the_policy() -> None:
    """The eligibility guard lives in the application; the policy still refuses to invent a chart
    the floor would not draw, and returns None like the floor does."""
    one = visual_profile_of(VisualDataset(metric="SUM", rows=(VisualRow("A", 1),)), total_minor=None, group_by="NONE")
    model = ScriptedVisualPlanner(VERTICAL)
    assert await policy(model, "hybrid").decide("q", one, DEFAULT_PRESENTATION) is None
    assert model.received == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --directory apps/orchestrator pytest tests/test_visual_planning_policy.py -v`
Expected: FAIL with `ModuleNotFoundError` for `visual_planning_policy`.

- [ ] **Step 3: Extend `VisualDecision`, then write the policy**

In `deterministic_visual_planner.py`, `VisualDecision` gains, after `fallback_reason`:

```python
    proposed: bool = False
    """An attempt was made: the model was called and returned a draft (or abstained)."""
    valid: bool = False
    """The draft passed the verifier."""
    accepted: bool = False
    """What the user sees is the model's chart. Implies `source == "llm"`."""
    changed_from_baseline: bool | None = None
    """The verified proposal differs from the floor on `canonical()`. None when the floor was
    not computed (promoted) or there was no verified proposal to compare."""
    latency_ms: int | None = None
    proposed_spec: ChartSpec | None = None
    """The verified proposal, whatever was shown -- so suitability can be annotated afterwards
    from the record rather than reconstructed from a `changed` flag."""
    rejected_rule: str | None = None
```

and `__post_init__` gains: `if self.accepted != (self.source == "llm"): raise ValueError("accepted means the model's chart is what is shown")`.

`domain/services/visual_planning_policy.py`:

```python
"""The floor is availability, fallback and experimental baseline at once -- so it is never the
port's default implementation, and this is the one place that holds both planners.

`None` from the port is abstention and nothing else; the other three failures arrive as a timeout
the policy imposes, a `ProviderFailedError`, or a `Rejection` from the verifier. Each becomes a
different `fallback_reason`, because a single value for all four would make "why did it fall back"
unwritable -- `PlanningOutcome`'s own argument.
"""

import asyncio
import time
from collections.abc import Callable
from typing import Literal

from finguard_orchestrator.domain.errors import ProviderFailedError
from finguard_orchestrator.domain.models.chart import ChartSpec, PresentationContext, VisualProfile
from finguard_orchestrator.domain.models.visual_plan import Rejection, VisualPlanDraft, canonical
from finguard_orchestrator.domain.ports.visual_planner import VisualPlannerPort
from finguard_orchestrator.domain.services.deterministic_visual_planner import (
    DeterministicVisualPlanner,
    VisualDecision,
)

VisualMode = Literal["deterministic", "shadow", "hybrid", "promoted"]
FallbackReason = Literal["abstention", "timeout", "provider_error", "invalid_proposal"]
Verifier = Callable[[VisualPlanDraft, VisualProfile], ChartSpec | Rejection]


class VisualPlanningPolicy:
    def __init__(
        self,
        *,
        model: VisualPlannerPort | None,
        fallback: DeterministicVisualPlanner,
        verifier: Verifier,
        mode: VisualMode,
        budget_seconds: float,
    ) -> None:
        self._model = model
        self._fallback = fallback
        self._verifier = verifier
        self._mode = mode
        self._budget = budget_seconds

    async def decide(
        self, question: str, profile: VisualProfile, presentation: PresentationContext
    ) -> VisualDecision | None:
        floor = None
        if self._mode != "promoted":
            floor = self._fallback.plan(profile, presentation)
            if floor is None:
                return None  # the floor declines (one row, no measure): nothing to fall back to
        if self._mode == "deterministic" or self._model is None:
            assert floor is not None
            return VisualDecision(spec=floor, profile=profile, source="deterministic")

        started = time.monotonic()
        proposed_spec: ChartSpec | None = None
        rejected: Rejection | None = None
        reason: FallbackReason | None = None
        proposed = False
        try:
            draft = await asyncio.wait_for(
                self._model.plan(question, profile, presentation, budget_seconds=self._budget),
                timeout=self._budget,
            )
            proposed = True
            if draft is None:
                reason = "abstention"
            else:
                verified = self._verifier(draft, profile)
                if isinstance(verified, Rejection):
                    rejected, reason = verified, "invalid_proposal"
                else:
                    proposed_spec = verified
        except asyncio.TimeoutError:
            reason = "timeout"
        except ProviderFailedError:
            proposed = True
            reason = "provider_error"
        latency_ms = int((time.monotonic() - started) * 1000)

        if floor is None and proposed_spec is None:
            floor = self._fallback.plan(profile, presentation)
            if floor is None:
                return None
        changed = (
            canonical(proposed_spec) != canonical(floor)
            if proposed_spec is not None and floor is not None
            else None
        )
        accept = proposed_spec is not None and self._mode in {"hybrid", "promoted"}
        shown = proposed_spec if accept else floor
        assert shown is not None
        return VisualDecision(
            spec=shown,
            profile=profile,
            source="llm" if accept else "deterministic",
            fallback_reason=reason,
            proposed=proposed,
            valid=proposed_spec is not None,
            accepted=accept,
            changed_from_baseline=changed,
            latency_ms=latency_ms,
            proposed_spec=proposed_spec,
            rejected_rule=rejected.rule if rejected is not None else None,
        )
```

`fallback_reason` is simply `reason`, and that is Section E's rule falling out of the shape rather than a special case: in `shadow` a valid proposal has `reason is None` because nothing fell back, and a failed attempt carries its reason even though the floor was going to be shown anyway. `test_shadow_runs_both_and_shows_the_floor` asserts the first half, the parametrised test the second. `proposed` stays `False` on a timeout because the policy cannot know whether the provider was ever reached; on an abstention or a `ProviderFailedError` it was.

- [ ] **Step 4: Run to verify it passes**

Run: `uv run --directory apps/orchestrator pytest tests/test_visual_planning_policy.py tests/test_deterministic_visual_planner.py -v`
Expected: PASS. Existing `VisualDecision` constructions still work because every new field has a default; if any existing test constructs `VisualDecision(..., source="llm")` without `accepted=True`, it now raises — that is the new invariant doing its job, and the fix is the test's construction, not the invariant.

- [ ] **Step 5: Gates, then commit (two commits)**

```bash
git add apps/orchestrator/src/finguard_orchestrator/domain/services/deterministic_visual_planner.py
git commit -m "feat(visual): the decision carries what was proposed, not only what was shown"
git add apps/orchestrator/src/finguard_orchestrator/domain/services/visual_planning_policy.py apps/orchestrator/tests/test_visual_planning_policy.py
git commit -m "feat(visual): the policy holds both planners, four modes, a budget and four fallback reasons"
```

---

### Task 5: the application seam — async, recorded, injected

**Files:**
- Modify: `observability/decisions.py` (`Step.VISUAL_PLANNER`)
- Modify: `application/queries/ask_financial_question.py` (`_decided_visual`, handler `__init__`, the `stream` call site)
- Modify: `bootstrap/config.py`, `bootstrap/main.py`
- Modify: `tests/test_visualization_stage1.py`, `tests/test_deterministic_visual_planner.py` (call sites that invoke `_decided_visual` directly become `await`s; assertions unchanged)
- Test: `tests/test_visual_decision_seam.py`

**Interfaces:**
- Consumes: Task 4 `VisualPlanningPolicy`, `VisualMode`.
- Produces: `async def _decided_visual(result, policy: VisualPlanningPolicy, presentation, question: str) -> AskFinancialQuestionResult`; `AskFinancialQuestionHandler(..., visual_policy: VisualPlanningPolicy | None = None)`; settings `visual_planner: Literal["deterministic","shadow","hybrid","promoted"] = "deterministic"` and `visual_planner_budget_seconds: float = 6.0`.

**The record.** One `record()` per turn that entered the policy, plus one structured log line with the seven fields and both canonical specs:

| situation | verdict | changed | field | was | now | detail |
| --- | --- | --- | --- | --- | --- | --- |
| mode deterministic | AGREED | False | `chart` | — | `deterministic` | `not_run` |
| shadow, valid, same as floor | AGREED | False | `chart` | `deterministic` | `deterministic` | `shadow:same` |
| shadow, valid, different | AGREED | False | `chart` | `deterministic` | `deterministic` | `shadow:changed` |
| shadow/hybrid/promoted, failed | FAILED | False | `chart` | — | `deterministic` | the `fallback_reason`, or `invalid_proposal:<rule>` |
| hybrid/promoted accepted | OVERRODE | True | `chart` | `deterministic` | `llm` | `accepted` |

`changed` is the headline "did this alter what the turn answered" — only an accepted model chart does. Every value is service vocabulary; nothing holder-written enters.

- [ ] **Step 1: Write the failing test**

```python
"""The seam records a decision per mode, and the default policy leaves every existing test alone."""

from finguard_orchestrator.domain.models.chart import DEFAULT_PRESENTATION
from finguard_orchestrator.domain.services.deterministic_visual_planner import DeterministicVisualPlanner
from finguard_orchestrator.domain.services.visual_plan_verifier import verify
from finguard_orchestrator.domain.services.visual_planning_policy import VisualPlanningPolicy
from finguard_orchestrator.observability.decisions import Step, Verdict, recording_turn
from finguard_orchestrator.application.queries.ask_financial_question import _decided_visual
from tests.support.visual_planners import ScriptedVisualPlanner
from tests.support.visual_planners import BREAKDOWN_PROFILE as PROFILE, VERTICAL_DRAFT as VERTICAL
from tests.test_visualization_stage1 import _result  # the existing result builder


def _policy(model, mode):
    return VisualPlanningPolicy(model=model, fallback=DeterministicVisualPlanner(), verifier=verify, mode=mode, budget_seconds=1.0)


async def test_shadow_records_agreed_with_the_comparison_in_detail() -> None:
    with recording_turn() as turn:
        decided = await _decided_visual(_result(), _policy(ScriptedVisualPlanner(VERTICAL), "shadow"), DEFAULT_PRESENTATION, "¿en qué gasto más?")
    (decision,) = turn.sealed().where(Step.VISUAL_PLANNER)
    assert decision.verdict is Verdict.AGREED and decision.changed is False
    assert decision.now == "deterministic" and decision.detail == "shadow:changed"
    assert decided.visual_decision.accepted is False


async def test_hybrid_records_the_override() -> None:
    with recording_turn() as turn:
        await _decided_visual(_result(), _policy(ScriptedVisualPlanner(VERTICAL), "hybrid"), DEFAULT_PRESENTATION, "q")
    (decision,) = turn.sealed().where(Step.VISUAL_PLANNER)
    assert decision.verdict is Verdict.OVERRODE and decision.changed is True and decision.now == "llm"


async def test_a_failure_records_failed_with_the_reason() -> None:
    with recording_turn() as turn:
        await _decided_visual(_result(), _policy(ScriptedVisualPlanner(None), "hybrid"), DEFAULT_PRESENTATION, "q")
    (decision,) = turn.sealed().where(Step.VISUAL_PLANNER)
    assert decision.verdict is Verdict.FAILED and decision.detail == "abstention"


async def test_the_question_never_enters_the_record() -> None:
    with recording_turn() as turn:
        await _decided_visual(_result(), _policy(ScriptedVisualPlanner(VERTICAL), "shadow"), DEFAULT_PRESENTATION, "gasté 734291 en Mercadona")
    for decision in turn.sealed().where(Step.VISUAL_PLANNER):
        for slot in (decision.field, decision.was, decision.now, decision.detail):
            assert slot is None or ("734291" not in slot and "Mercadona" not in slot)
```

Read `_result` in `test_visualization_stage1.py` first: it may need a `series`/`group_by` argument to produce a two-row dataset; use whatever it already offers.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --directory apps/orchestrator pytest tests/test_visual_decision_seam.py -v`
Expected: FAIL — `_decided_visual` is sync and takes `floor`, `Step.VISUAL_PLANNER` does not exist.

- [ ] **Step 3: Implement**

`observability/decisions.py`, after `OUTCOME` in `Step`:

```python
    VISUAL_PLANNER = "visual_planner"
    """Which chart was shown and by whom, and -- in shadow -- whether the model's differed."""
```

The `Step` docstring says an eleventh seam "should be a diff somebody has to justify". The commit body is that justification: a defect can hide here (a model chart shown that the floor would not have drawn) and the design's Section F names this seam.

`ask_financial_question.py` — replace `_decided_visual`:

```python
async def _decided_visual(
    result: AskFinancialQuestionResult,
    policy: VisualPlanningPolicy,
    presentation: PresentationContext,
    question: str,
) -> AskFinancialQuestionResult:
    """Decide the chart once, in the application, where a model call is able to live.

    (docstring body unchanged from today, plus:) `question` is passed through to the policy and
    nowhere else: it reaches the model the way the financial planner already sends it, and never
    the record below, whose every slot is service vocabulary.
    """
    dataset = result.visual_dataset
    if dataset is None or len(dataset.rows) < 2:
        return result
    context = result.plan_context
    profile = visual_profile_of(
        dataset,
        total_minor=result.total_minor,
        group_by=context.group_by if context is not None else None,
        plan_limited=context is not None and context.group_limit is not None,
        plan_metric=context.metric if context is not None else None,
    )
    decision = await policy.decide(question, profile, presentation)
    if decision is None:
        return result
    _record_visual(decision)
    return replace(result, visual_decision=decision)


def _record_visual(decision: VisualDecision) -> None:
    if not decision.proposed and decision.fallback_reason is None:
        record(Step.VISUAL_PLANNER, Verdict.AGREED, changed=False, field="chart",
               now="deterministic", detail="not_run")
    elif decision.accepted:
        record(Step.VISUAL_PLANNER, Verdict.OVERRODE, changed=True, field="chart",
               was="deterministic", now="llm", detail="accepted")
    elif decision.fallback_reason is not None:
        detail = decision.fallback_reason
        if decision.rejected_rule is not None:
            detail = f"invalid_proposal:{decision.rejected_rule}"
        record(Step.VISUAL_PLANNER, Verdict.FAILED, changed=False, field="chart",
               now="deterministic", detail=detail)
    else:
        record(Step.VISUAL_PLANNER, Verdict.AGREED, changed=False, field="chart",
               was="deterministic", now="deterministic",
               detail="shadow:changed" if decision.changed_from_baseline else "shadow:same")
    logger.info(
        "visual_planner.decision",
        extra={
            "proposed": decision.proposed, "valid": decision.valid, "accepted": decision.accepted,
            "changed_from_baseline": decision.changed_from_baseline,
            "selected_source": decision.source, "fallback_reason": decision.fallback_reason,
            "latency_ms": decision.latency_ms,
            "proposed_spec": canonical(decision.proposed_spec) if decision.proposed_spec else None,
            "shown_spec": canonical(decision.spec),
        },
    )
```

Imports: `VisualPlanningPolicy` from `domain.services.visual_planning_policy`, `canonical` from `domain.models.visual_plan`, `Step`, `Verdict`, `record` from `observability.decisions` (the module already imports `record` — check).

Handler `__init__`: add keyword-only `visual_policy: VisualPlanningPolicy | None = None` and replace `self._visual_floor = DeterministicVisualPlanner()` with

```python
        self._visual_policy = visual_policy or VisualPlanningPolicy(
            model=None, fallback=DeterministicVisualPlanner(), verifier=verify,
            mode="deterministic", budget_seconds=0.0,
        )
```

The `stream` call site becomes `step = await _decided_visual(step, self._visual_policy, query.presentation, query.sentence)` — read the query's attribute name for the sentence (`AskFinancialQuestionQuery.sentence` per `evaluate_planning.py`).

`bootstrap/config.py`, beside `turn_interpreter`:

```python
    # The visual planner's ladder, the same four rungs the turn interpreter climbs. `shadow` runs
    # the model beside the floor and shows the floor, which is how the baseline comparison is
    # collected before anything is risked. Promotion is a measured decision (docs/next.md), not a
    # default: this stays `deterministic` until three runs of `just evaluate-visual` say otherwise.
    visual_planner: Literal["deterministic", "shadow", "hybrid", "promoted"] = "deterministic"
    # Spent before the floor answers, so it is added latency on every turn the model runs. Set from
    # the P95 of `latency_ms` in the evaluation, not chosen; generous here so shadow measures the
    # model rather than the budget.
    visual_planner_budget_seconds: float = 6.0
```

`bootstrap/main.py`, before `asking = AskFinancialQuestionHandler(`:

```python
    visual_model: VisualPlannerPort | None = None
    if settings.visual_planner != "deterministic":
        visual_model = LlmVisualPlanner(
            planner_provider,
            model=settings.planner_model,
            temperature=settings.planner_temperature,
            max_output_tokens=settings.planner_max_output_tokens,
        )
    visual_policy = VisualPlanningPolicy(
        model=visual_model,
        fallback=DeterministicVisualPlanner(),
        verifier=verify,
        mode=settings.visual_planner,
        budget_seconds=settings.visual_planner_budget_seconds,
    )
```

and pass `visual_policy=visual_policy` to the handler. `LlmVisualPlanner` is Task 6 — **Task 5 lands with `visual_model` wiring commented out and a `# Task 6` marker is NOT acceptable**; instead land Task 6 before wiring `main.py`, i.e. do Task 5's `main.py` edit as the last step of Task 6. Task 5 ends with the handler default and the settings.

Existing tests calling `_decided_visual(step, floor, presentation)` directly: change each call to `await _decided_visual(step, _default_policy(), presentation, "")` where `_default_policy()` builds the deterministic policy as the handler does. Assertions unchanged.

- [ ] **Step 4: Run**

Run: `uv run --directory apps/orchestrator pytest tests/test_visual_decision_seam.py tests/test_visualization_stage1.py tests/test_deterministic_visual_planner.py tests/test_decisions.py -v`
Expected: PASS. `test_decisions.py` sweeps the record for the `was/now` invariant — the new rows satisfy it (`was == now` on AGREED rows, `was != now` on OVERRODE).

- [ ] **Step 5: Gates, then commit (three commits)**

```bash
git add apps/orchestrator/src/finguard_orchestrator/observability/decisions.py
git commit -m "feat(otel): an eleventh seam, visual_planner, because a chart the floor would not draw can be shown here"
git add apps/orchestrator/src/finguard_orchestrator/application/queries/ask_financial_question.py apps/orchestrator/tests/test_visual_decision_seam.py apps/orchestrator/tests/test_visualization_stage1.py apps/orchestrator/tests/test_deterministic_visual_planner.py
git commit -m "feat(visual): the seam awaits the policy and records one decision per turn"
git add apps/orchestrator/src/finguard_orchestrator/bootstrap/config.py
git commit -m "feat(config): the visual planner's mode and budget, deterministic until measured"
```

---

### Task 6: the adapter — a value-free payload, a schema from the domain, one repair

**Files:**
- Create: `infrastructure/planning/visual_prompt.py`
- Create: `infrastructure/planning/llm_visual_planner.py`
- Modify: `bootstrap/main.py` (the wiring from Task 5)
- Test: `tests/test_llm_visual_planner.py`

**Interfaces:**
- Consumes: `LlmProviderPort`, `EchoLlmProvider`, `ProviderFailedError`, `_STRUCTURE_REPAIR` (import it from `llm_query_planner.py` rather than copying it), Task 1-2 types, `get_args(Mark|Channel|FieldType|Stack|SortMode)`.
- Produces: `profile_payload(profile, presentation) -> dict[str, object]`; `build_visual_prompt(question, profile, presentation) -> str`; `visual_schema() -> dict[str, Any]`; `LlmVisualPlanner(provider, *, model, temperature, max_output_tokens)` implementing `VisualPlannerPort`, exposing `last_prompt: str | None` for tests.

- [ ] **Step 1: Write the failing tests**

```python
"""What the model sees, what it may answer, and the one repair the financial planner also makes."""

import json

import pytest

from finguard_orchestrator.domain.errors import ProviderFailedError
from finguard_orchestrator.domain.models.chart import (
    DEFAULT_PRESENTATION, Channel, FieldType, Mark, SortMode, Stack, VisualDataset, VisualRow,
)
from typing import get_args
from finguard_orchestrator.domain.services.visual_profile import visual_profile_of
from finguard_orchestrator.infrastructure.llm.echo_llm_provider import EchoLlmProvider
from finguard_orchestrator.infrastructure.planning.llm_visual_planner import LlmVisualPlanner
from finguard_orchestrator.infrastructure.planning.llm_query_planner import _STRUCTURE_REPAIR
from finguard_orchestrator.infrastructure.planning.visual_prompt import profile_payload, visual_schema

PROFILE = visual_profile_of(
    VisualDataset(metric="SUM", rows=(VisualRow("Comida", 193_847), VisualRow("Transporte", 278_413))),
    total_minor=734_291, group_by="NONE",
)


class RecordingProvider:
    name = "recording"

    def __init__(self, answers: list[str | BaseException]) -> None:
        self.answers, self.prompts = list(answers), []

    async def complete(self, prompt, *, model, temperature, max_output_tokens, schema=None):
        self.prompts.append(prompt)
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer


def test_the_payload_carries_shape_and_never_values() -> None:
    """Spec B: sentinels prove three values; the key sets prove that rows, amounts and labels have
    no key to travel under, which is the strong half."""
    payload = profile_payload(PROFILE, DEFAULT_PRESENTATION)
    flat = json.dumps(payload)
    for sentinel in ("734291", "193847", "278413", "Comida", "Transporte"):
        assert sentinel not in flat
    assert set(payload) <= {"fields", "metric", "unit", "positive", "mutually_exclusive",
                            "verified_total", "comparison", "row_count", "presentation"}
    for field in payload["fields"]:
        assert set(field) <= {"name", "type", "role", "cardinality", "order"}
    assert set(payload["presentation"]) == {"viewport", "max_visible_rows"}


def test_the_schema_is_built_from_the_domain_literals() -> None:
    schema = visual_schema()
    props = schema["properties"]
    assert set(props["mark"]["enum"]) == set(get_args(Mark))
    encoding = props["encodings"]["items"]["properties"]
    assert set(encoding["channel"]["enum"]) == set(get_args(Channel))
    assert set(encoding["type"]["enum"]) == set(get_args(FieldType))
    assert set(encoding["sort"]["properties"]["mode"]["enum"]) == set(get_args(SortMode))
    assert set(props["stack"]["enum"]) == set(get_args(Stack))


async def test_echo_yields_a_parseable_draft_the_verifier_can_refuse() -> None:
    """The floor any real model must beat: structurally valid, semantically empty."""
    planner = LlmVisualPlanner(EchoLlmProvider(), model="echo", temperature=0.0, max_output_tokens=256)
    draft = await planner.plan("q", PROFILE, DEFAULT_PRESENTATION, budget_seconds=1.0)
    assert draft is not None and draft.mark == get_args(Mark)[0] and draft.encodings == ()


async def test_a_valid_answer_becomes_a_draft_and_the_question_is_in_the_prompt() -> None:
    answer = json.dumps({"mark": "bar", "encodings": [
        {"channel": "y", "field": "dimension", "type": "nominal", "sort": {"mode": "descending", "field": "value_minor"}},
        {"channel": "x", "field": "value_minor", "type": "quantitative"}], "stack": None, "limit": None})
    provider = RecordingProvider([answer])
    planner = LlmVisualPlanner(provider, model="m", temperature=0.0, max_output_tokens=256)
    draft = await planner.plan("¿en qué gasto más?", PROFILE, DEFAULT_PRESENTATION, budget_seconds=1.0)
    assert draft.mark == "bar" and draft.encodings[0].sort.field == "value_minor"
    assert "¿en qué gasto más?" in provider.prompts[0]
    assert "193847" not in provider.prompts[0]


async def test_one_repair_then_a_provider_error() -> None:
    provider = RecordingProvider(["not json", "still not json"])
    planner = LlmVisualPlanner(provider, model="m", temperature=0.0, max_output_tokens=256)
    with pytest.raises(ProviderFailedError):
        await planner.plan("q", PROFILE, DEFAULT_PRESENTATION, budget_seconds=1.0)
    assert len(provider.prompts) == 2
    assert provider.prompts[1] == provider.prompts[0] + _STRUCTURE_REPAIR   # the same repair, once


async def test_an_explicit_abstention_is_none() -> None:
    provider = RecordingProvider([json.dumps({"mark": None, "encodings": []})])
    planner = LlmVisualPlanner(provider, model="m", temperature=0.0, max_output_tokens=256)
    assert await planner.plan("q", PROFILE, DEFAULT_PRESENTATION, budget_seconds=1.0) is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --directory apps/orchestrator pytest tests/test_llm_visual_planner.py -v`
Expected: FAIL with `ModuleNotFoundError` for `visual_prompt`.

- [ ] **Step 3: Write the prompt module and the adapter**

`infrastructure/planning/visual_prompt.py`:

```python
"""What the visual model sees: the dataset's shape, the question, the circumstances -- no values.

`profile_payload` is the object the structural test in `test_llm_visual_planner.py` pins: its key
set is the proof that rows, amounts and labels cannot travel. The schema is built from the domain
literals with `get_args`, never written out -- this repository removed three hand-copies of
`Channel` after one went stale in a validator.
"""

import json
from typing import Any, get_args

from finguard_orchestrator.domain.models.chart import (
    Channel, FieldType, Mark, PresentationContext, SortMode, Stack, VisualProfile,
)


def profile_payload(profile: VisualProfile, presentation: PresentationContext) -> dict[str, object]:
    return {
        "fields": [
            {"name": f.name, "type": f.type, "role": f.role, "cardinality": f.cardinality, "order": f.order}
            for f in profile.fields
        ],
        "metric": profile.measure_metric(),
        "unit": profile.unit,
        "positive": profile.positive,
        "mutually_exclusive": profile.mutually_exclusive,
        "verified_total": profile.verified_total,
        "comparison": profile.comparison,
        "row_count": profile.row_count,
        "presentation": {"viewport": presentation.viewport, "max_visible_rows": presentation.max_visible_rows},
    }


def visual_schema() -> dict[str, Any]:
    sort = {
        "type": "object",
        "properties": {"mode": {"enum": list(get_args(SortMode))}, "field": {"type": ["string", "null"]}},
        "required": ["mode"],
    }
    return {
        "type": "object",
        "properties": {
            "mark": {"enum": [*get_args(Mark), None]},
            "encodings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "channel": {"enum": list(get_args(Channel))},
                        "field": {"type": "string"},
                        "type": {"enum": list(get_args(FieldType))},
                        "sort": {**sort, "type": ["object", "null"]},
                    },
                    "required": ["channel", "field", "type"],
                },
            },
            "sort": {"enum": [*get_args(SortMode), None]},
            "sort_field": {"type": ["string", "null"]},
            "stack": {"enum": [*get_args(Stack), None]},
            "limit": {"type": ["integer", "null"], "minimum": 1, "maximum": 100},
            "rationale": {"type": ["string", "null"]},
        },
        "required": ["mark", "encodings"],
    }


_RULES = """You choose how a financial answer is drawn. You never see the numbers: only the shape.
Answer with one JSON object matching the schema, or {"mark": null, "encodings": []} to abstain.
Field names must come from `fields`; each encoding's `type` must equal that field's `type`.
Roles: a dimension goes on x, y or color; a measure on x, y or theta; a series on color and the
offset (xOffset for a dimension on x, yOffset for a dimension on y). A comparison (comparison=true)
is grouped, never stacked: put the series on color and the matching offset, and set stack to
"disabled". A dimension with order MEASURE_DESC ranks by the measure (descending); order INPUT keeps
the given order (sort "input"); order TEMPORAL is a line or area over time. A donut needs positive,
mutually_exclusive and verified_total all true. limit only on a ranked categorical bar with no
series. Prefer the chart a careful analyst would draw for this question; do not decorate."""


def build_visual_prompt(question: str, profile: VisualProfile, presentation: PresentationContext) -> str:
    return (
        f"{_RULES}\n\nQuestion (verbatim, may be in Spanish): {question}\n\n"
        f"Shape of the result:\n{json.dumps(profile_payload(profile, presentation), ensure_ascii=False)}\n"
    )
```

`infrastructure/planning/llm_visual_planner.py`:

```python
"""Implements VisualPlannerPort against the model already loaded, with a different prompt.

Mirrors `LlmQueryPlanner`: one call, one structure repair with the same `_STRUCTURE_REPAIR`
suffix, and a `ProviderFailedError` when the provider will not honour the schema twice -- which is
a provider failure, not an abstention, and the policy records it as one. `budget_seconds` is
accepted and unused here: the policy enforces it with `wait_for`, because only the policy holds the
floor to fall back to.
"""

import json
from typing import Any

from finguard_orchestrator.domain.errors import ProviderFailedError
from finguard_orchestrator.domain.models.chart import PresentationContext, VisualProfile
from finguard_orchestrator.domain.models.visual_plan import ProposedEncoding, ProposedSort, VisualPlanDraft
from finguard_orchestrator.domain.ports.llm_provider import LlmProviderPort
from finguard_orchestrator.infrastructure.planning.llm_query_planner import _STRUCTURE_REPAIR
from finguard_orchestrator.infrastructure.planning.visual_prompt import build_visual_prompt, visual_schema


class LlmVisualPlanner:
    def __init__(self, provider: LlmProviderPort, *, model: str, temperature: float, max_output_tokens: int) -> None:
        self._provider = provider
        self._model = model
        self._temperature = temperature
        self._max_output_tokens = max_output_tokens
        self._schema = visual_schema()
        self.last_prompt: str | None = None

    async def plan(
        self, question: str, profile: VisualProfile, presentation: PresentationContext, *, budget_seconds: float
    ) -> VisualPlanDraft | None:
        prompt = build_visual_prompt(question, profile, presentation)
        self.last_prompt = prompt
        answer = await self._complete(prompt)
        parsed = _parse(answer)
        if parsed is None:
            answer = await self._complete(prompt + _STRUCTURE_REPAIR)
            parsed = _parse(answer)
            if parsed is None:
                raise ProviderFailedError("visual planner: invalid structure twice")
        return _draft(parsed)

    async def _complete(self, prompt: str) -> str:
        return await self._provider.complete(
            prompt, model=self._model, temperature=self._temperature,
            max_output_tokens=self._max_output_tokens, schema=self._schema,
        )


def _parse(answer: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(answer)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) and "encodings" in parsed else None


def _draft(parsed: dict[str, Any]) -> VisualPlanDraft | None:
    if parsed.get("mark") is None:
        return None  # abstention, the one meaning None has
    encodings = tuple(
        ProposedEncoding(
            str(item.get("channel")), str(item.get("field")), str(item.get("type")),
            ProposedSort(str(item["sort"].get("mode")), item["sort"].get("field"))
            if isinstance(item.get("sort"), dict) else None,
        )
        for item in parsed.get("encodings", []) if isinstance(item, dict)
    )
    return VisualPlanDraft(
        mark=str(parsed["mark"]), encodings=encodings,
        sort=parsed.get("sort"), sort_field=parsed.get("sort_field"), stack=parsed.get("stack"),
        limit=parsed.get("limit") if isinstance(parsed.get("limit"), int) else None,
        rationale=parsed.get("rationale") if isinstance(parsed.get("rationale"), str) else None,
    )
```

`_STRUCTURE_REPAIR` is private by name; importing it is the honest choice over a second copy — if the reviewer objects, promote it to `STRUCTURE_REPAIR` in `llm_query_planner.py` and import that, in this same commit.

Then the `bootstrap/main.py` wiring from Task 5 Step 3, now that `LlmVisualPlanner` exists.

- [ ] **Step 4: Run**

Run: `uv run --directory apps/orchestrator pytest tests/test_llm_visual_planner.py -v` then the full gates (the `main.py` change is exercised by `tests/test_bootstrap*.py` if present — check they still construct the app).
Expected: PASS, 6 tests.

- [ ] **Step 5: Commit (two commits)**

```bash
git add apps/orchestrator/src/finguard_orchestrator/infrastructure/planning/visual_prompt.py apps/orchestrator/src/finguard_orchestrator/infrastructure/planning/llm_visual_planner.py apps/orchestrator/tests/test_llm_visual_planner.py
git commit -m "feat(visual): the model sees the shape and the question, never a value, and answers a bounded draft"
git add apps/orchestrator/src/finguard_orchestrator/bootstrap/main.py
git commit -m "feat(bootstrap): the visual policy is wired with the planner already loaded, in whatever mode settings say"
```

---

### Task 7: the evaluation — cases, judge, runner, recipe

**Files:**
- Modify: `scripts/export_chart_fixtures.py` (factor `FAMILY_DATASETS`)
- Create: `evaluation/visual/cases.yaml`
- Create: `scripts/evaluate_visual.py`
- Modify: `justfile` (recipe `evaluate-visual`)
- Test: `tests/test_evaluate_visual.py`, `tests/test_export_chart_fixtures.py` (unchanged assertions; the staleness test proves the refactor moved nothing)

**Interfaces:**
- Consumes: Tasks 1-6; `EvaluationProvider`/`_provider`/`_run_manifest` patterns from `evaluate_planning.py` (import `_provider` and `_run_manifest` rather than copying them; if they are private, promote and import).
- Produces: `FAMILY_DATASETS: dict[str, tuple[VisualDataset, dict[str, object]]]` in the exporter (name → dataset and the `_family` kwargs); `scripts/evaluate_visual.py` with `load_cases(path) -> list[Case]`, `judge(case, decision, results) -> None`, `stability(cases, results) -> Stability`, `async run(args)`; `just evaluate-visual model="qwen3.5:9b" provider="ollama"`.

**The cases file** — every case names a dataset from `FAMILY_DATASETS` (or `none` for the no-chart cases, which carry their own one-row dataset), a question, and an annotation. `suitable_marks` is the human judgement the spec says suitability needs; `visual_instruction: true` marks a paraphrase that *should* differ.

```yaml
# Visual evaluation. Three numbers and none of them is "it worked" -- see the spec, Section G.
# `dataset` names a shape in scripts/export_chart_fixtures.py FAMILY_DATASETS; the shape is what
# the model sees, the question is the only other input. `suitable_marks` is annotated by hand.
- id: breakdown_where_most
  group: breakdown
  dataset: breakdown
  question: ¿En qué he gastado más este mes?
  suitable_marks: [bar]
- id: breakdown_where_most_p1
  group: breakdown
  dataset: breakdown
  question: Dime dónde se me va más dinero este mes
  paraphrase_of: breakdown_where_most
  suitable_marks: [bar]
- id: breakdown_where_most_p2
  group: breakdown
  dataset: breakdown
  question: ¿Cuáles son mis mayores gastos del mes?
  paraphrase_of: breakdown_where_most
  suitable_marks: [bar]
- id: breakdown_as_donut
  group: breakdown
  dataset: breakdown
  question: ¿En qué he gastado más este mes? Enséñamelo como un donut
  paraphrase_of: breakdown_where_most
  visual_instruction: true
  suitable_marks: [donut, bar]     # the profile has no verified total, so bar is what a careful analyst falls back to
- id: temporal_evolution
  group: temporal
  dataset: temporal
  question: ¿Cómo ha evolucionado mi gasto mes a mes?
  suitable_marks: [line, area, bar]
- id: temporal_evolution_p1
  group: temporal
  dataset: temporal
  question: Muéstrame el gasto de cada mes
  paraphrase_of: temporal_evolution
  suitable_marks: [line, area, bar]
- id: cumulative_running
  group: cumulative
  dataset: cumulative
  question: ¿Cuánto llevo acumulado mes a mes?
  suitable_marks: [area, line]
- id: frequency_weekday
  group: frequency
  dataset: frequency
  question: ¿Qué día de la semana gasto más veces?
  suitable_marks: [bar]
- id: recurrence_streams
  group: recurrence
  dataset: recurrence
  question: ¿Qué gastos recurrentes tengo?
  suitable_marks: [point]
- id: share_of_total
  group: ratio_share
  dataset: ratio_share
  question: ¿Qué parte de mis ingresos se va en alquiler?
  suitable_marks: [bar, donut]
- id: comparison_two_months
  group: comparison
  dataset: categorical_comparison
  question: Compara mis gastos de este mes con los del anterior por categoría
  suitable_marks: [bar]
- id: comparison_two_months_p1
  group: comparison
  dataset: categorical_comparison
  question: ¿En qué categorías he gastado más que el mes pasado?
  paraphrase_of: comparison_two_months
  suitable_marks: [bar]
- id: ranking_top3
  group: ranking
  dataset: breakdown
  plan_limited: true
  question: ¿Cuáles son mis tres mayores gastos?
  suitable_marks: [bar]
- id: no_chart_single_figure
  group: no_chart
  dataset: none
  question: ¿Cuánto he gastado este mes?
  expect_chart: false
- id: no_chart_balance
  group: no_chart
  dataset: none
  question: ¿Cuánto dinero tengo?
  expect_chart: false
```

**What the runner measures**, per case and per group, over one run — and the report says in its header that one run is not a number:

| measure | how |
| --- | --- |
| validity | `decision.valid` over cases that entered the policy |
| suitability | `decision.proposed_spec.mark in suitable_marks` over valid proposals; a `None` proposal on a `no_chart` case counts as suitable (the guard kept it out) |
| changed_from_baseline | `decision.changed_from_baseline` over valid proposals; and the figure that carries the argument, `valid and suitable and changed` |
| stability | for each `paraphrase_of` pair **without** `visual_instruction`, `canonical(proposed_spec)` equal on both sides; with `visual_instruction`, whether they differ (sensitivity) |
| latency | P50 / P95 of `latency_ms`; the P95 is the budget candidate |
| fallback reasons | a count per `fallback_reason` and per `rejected_rule` |

- [ ] **Step 1: Write the failing tests** (`tests/test_evaluate_visual.py`)

```python
"""The judge and the stability check, against scripted decisions -- no model in tests."""

import importlib.util
import sys
from pathlib import Path

from finguard_orchestrator.domain.models.chart import ChartEncoding, ChartSpec, EncodingSort
from finguard_orchestrator.domain.services.deterministic_visual_planner import VisualDecision

_SPEC = importlib.util.spec_from_file_location(
    "evaluate_visual", Path(__file__).resolve().parents[1] / "scripts" / "evaluate_visual.py"
)
evaluate_visual = importlib.util.module_from_spec(_SPEC)
sys.modules["evaluate_visual"] = evaluate_visual
_SPEC.loader.exec_module(evaluate_visual)

from scripts.export_chart_fixtures import FAMILY_DATASETS  # noqa: E402  -- via the same loader if needed


def _bar(orientation: str = "y") -> ChartSpec:
    other = "x" if orientation == "y" else "y"
    return ChartSpec("bar", (
        ChartEncoding(orientation, "dimension", "nominal", sort=EncodingSort("descending", "value_minor")),
        ChartEncoding(other, "value_minor", "quantitative"),
    ))


def _decision(spec, *, valid=True, changed=True, ms=1200, reason=None):
    return VisualDecision(spec=_bar(), profile=evaluate_visual.PROFILES["breakdown"], source="deterministic",
                          proposed=True, valid=valid, changed_from_baseline=changed if valid else None,
                          proposed_spec=spec if valid else None, latency_ms=ms, fallback_reason=reason)


def test_every_case_names_a_known_dataset_and_a_resolvable_twin() -> None:
    cases = evaluate_visual.load_cases(Path("evaluation/visual/cases.yaml"))
    ids = {c.id for c in cases}
    for case in cases:
        assert case.dataset == "none" or case.dataset in FAMILY_DATASETS
        assert case.paraphrase_of is None or case.paraphrase_of in ids
        assert case.expect_chart is False or case.suitable_marks


def test_the_judge_counts_validity_suitability_and_the_figure_that_matters() -> None:
    cases = evaluate_visual.load_cases(Path("evaluation/visual/cases.yaml"))
    by_id = {c.id: c for c in cases}
    results = evaluate_visual.Results()
    evaluate_visual.judge(by_id["breakdown_where_most"], _decision(_bar("x")), results)          # valid, suitable, changed
    evaluate_visual.judge(by_id["breakdown_where_most"], _decision(_bar("x"), changed=False), results)
    evaluate_visual.judge(by_id["breakdown_where_most"], _decision(None, valid=False, reason="invalid_proposal"), results)
    assert results.entered == 3 and results.valid == 2 and results.suitable == 2
    assert results.valid_suitable_changed == 1
    assert results.fallback_reasons == {"invalid_proposal": 1}


def test_stability_holds_for_equivalent_paraphrases_and_expects_a_difference_for_an_instruction() -> None:
    cases = evaluate_visual.load_cases(Path("evaluation/visual/cases.yaml"))
    proposals = {
        "breakdown_where_most": _bar("y"), "breakdown_where_most_p1": _bar("y"),
        "breakdown_where_most_p2": _bar("x"),                 # a break
        "breakdown_as_donut": _bar("y"),                      # instruction ignored: insensitive
    }
    stability = evaluate_visual.stability(cases, proposals)
    assert stability.pairs == 2 and stability.held == 1
    assert stability.instructed == 1 and stability.sensitive == 0


def test_a_no_chart_case_is_suitable_when_nothing_was_drawn() -> None:
    cases = evaluate_visual.load_cases(Path("evaluation/visual/cases.yaml"))
    results = evaluate_visual.Results()
    evaluate_visual.judge(next(c for c in cases if c.id == "no_chart_single_figure"), None, results)
    assert results.no_chart_total == 1 and results.no_chart_held == 1
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run --directory apps/orchestrator pytest tests/test_evaluate_visual.py -v`
Expected: FAIL — `scripts/evaluate_visual.py` does not exist.

- [ ] **Step 3: Factor the exporter, write the cases, write the runner**

In `export_chart_fixtures.py`, lift each `_family(...)` call's dataset and kwargs into a module-level

```python
FAMILY_DATASETS: dict[str, tuple[VisualDataset, dict[str, Any]]] = {
    "breakdown": (VisualDataset(metric="SUM", rows=(...)), {"group_by": "NONE"}),
    "temporal": (VisualDataset(...), {"group_by": "MONTH"}),
    ...  # one entry per existing family, values moved verbatim
}
```

and `build_chart_families()` becomes `[_family(name, dataset, **kwargs) for name, (dataset, kwargs) in FAMILY_DATASETS.items()] + [_donut_family()]`. The staleness test must still pass with a **byte-identical fixture** — that is the proof the move changed nothing; run it before committing and paste the diff (`git diff --stat apps/web/src/__fixtures__/chart-families.json` → nothing).

`scripts/evaluate_visual.py` (the parts the tests pin; the `run()` mirrors `evaluate_planning.run`'s provider and manifest handling):

```python
"""Does the model choose a chart a person would have chosen, and does it differ from the floor?

One run is not a number: the planner is not bit-reproducible at temperature 0 (`--repeat`'s own
help says so), and a contended GPU changes its answers. Run three times with the GPU alone and take
the minimum, the way `calibrate_robustness_gate.py` does for robustness.

    uv run python scripts/evaluate_visual.py --provider ollama --model qwen3.5:9b
"""

import argparse
import asyncio
import json
import statistics
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from export_chart_fixtures import FAMILY_DATASETS  # noqa: E402

from finguard_orchestrator.domain.models.chart import (  # noqa: E402
    DEFAULT_PRESENTATION, VisualDataset, VisualProfile, VisualRow,
)
from finguard_orchestrator.domain.models.visual_plan import canonical  # noqa: E402
from finguard_orchestrator.domain.services.deterministic_visual_planner import (  # noqa: E402
    DeterministicVisualPlanner, VisualDecision,
)
from finguard_orchestrator.domain.services.visual_plan_verifier import verify  # noqa: E402
from finguard_orchestrator.domain.services.visual_planning_policy import VisualPlanningPolicy  # noqa: E402
from finguard_orchestrator.domain.services.visual_profile import visual_profile_of  # noqa: E402
from finguard_orchestrator.infrastructure.planning.llm_visual_planner import LlmVisualPlanner  # noqa: E402

PROFILES: dict[str, VisualProfile] = {
    name: visual_profile_of(dataset, total_minor=kwargs.get("total_minor"), group_by=kwargs["group_by"],
                            plan_limited=kwargs.get("plan_limited", False), plan_metric=kwargs.get("plan_metric"))
    for name, (dataset, kwargs) in FAMILY_DATASETS.items()
}
ONE_ROW = VisualDataset(metric="SUM", rows=(VisualRow("Total", 1),))


@dataclass(frozen=True)
class Case:
    id: str
    group: str
    dataset: str
    question: str
    suitable_marks: tuple[str, ...] = ()
    paraphrase_of: str | None = None
    visual_instruction: bool = False
    expect_chart: bool = True
    plan_limited: bool = False


def load_cases(path: Path) -> list[Case]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [Case(**{**item, "suitable_marks": tuple(item.get("suitable_marks", ()))}) for item in raw]


@dataclass
class Results:
    entered: int = 0
    valid: int = 0
    suitable: int = 0
    changed: int = 0
    valid_suitable_changed: int = 0
    latencies: list[int] = field(default_factory=list)
    fallback_reasons: Counter = field(default_factory=Counter)
    rejected_rules: Counter = field(default_factory=Counter)
    no_chart_total: int = 0
    no_chart_held: int = 0
    by_group: dict[str, Counter] = field(default_factory=dict)


def judge(case: Case, decision: VisualDecision | None, results: Results) -> None:
    group = results.by_group.setdefault(case.group, Counter())
    if not case.expect_chart:
        results.no_chart_total += 1
        if decision is None:
            results.no_chart_held += 1
        return
    if decision is None:
        return
    results.entered += 1
    group["entered"] += 1
    if decision.latency_ms is not None:
        results.latencies.append(decision.latency_ms)
    if decision.fallback_reason is not None:
        results.fallback_reasons[decision.fallback_reason] += 1
        if decision.rejected_rule is not None:
            results.rejected_rules[decision.rejected_rule] += 1
    if not decision.valid or decision.proposed_spec is None:
        return
    results.valid += 1
    group["valid"] += 1
    suitable = decision.proposed_spec.mark in case.suitable_marks
    changed = bool(decision.changed_from_baseline)
    results.suitable += suitable
    results.changed += changed
    group["suitable"] += suitable
    if suitable and changed:
        results.valid_suitable_changed += 1
        group["valid_suitable_changed"] += 1


@dataclass
class Stability:
    pairs: int = 0
    held: int = 0
    instructed: int = 0
    sensitive: int = 0
    breaks: list[str] = field(default_factory=list)


def stability(cases: list[Case], proposals: dict[str, Any]) -> Stability:
    out = Stability()
    for case in cases:
        if case.paraphrase_of is None:
            continue
        mine, twin = proposals.get(case.id), proposals.get(case.paraphrase_of)
        if mine is None or twin is None:
            continue
        same = canonical(mine) == canonical(twin)
        if case.visual_instruction:
            out.instructed += 1
            out.sensitive += not same
        else:
            out.pairs += 1
            out.held += same
            if not same:
                out.breaks.append(f"{case.id}: differs from {case.paraphrase_of}")
    return out
```

`run(args)`: build the provider the way `evaluate_planning._provider` does (import it; promote to public if needed), build `LlmVisualPlanner`, a `VisualPlanningPolicy(mode="shadow", budget_seconds=args.budget)`; for each case, profile = `PROFILES[case.dataset]` (or `visual_profile_of(ONE_ROW, total_minor=1, group_by="NONE")` for `none`, which the policy declines because the floor does); `decision = await policy.decide(case.question, profile, DEFAULT_PRESENTATION)`; `judge(case, decision, results)`; collect `proposals[case.id] = decision.proposed_spec`; then `stability(cases, proposals)`; write `evaluation/visual/RESULTS.md` with the table below and `evaluation/visual/last-run.json` with the manifest (`_run_manifest`: model, provider, GPU snapshot, ctx, seed) plus every case's `proposed`/`shown` canonical specs — the reviewer's ask, so suitability can be re-annotated without re-running.

```
| measure | value |
| --- | --- |
| cases entering the policy | N |
| validity | valid / entered |
| suitability | suitable / valid |
| changed_from_baseline | changed / valid |
| **valid, suitable and changed** | k / entered   <- the figure that carries the argument |
| stability under equivalent paraphrase | held / pairs |
| sensitivity to a visual instruction | sensitive / instructed |
| no-chart cases kept out | held / total |
| latency P50 / P95 (ms) | ... / ...   <- P95 is the budget candidate |
| fallback reasons | {...}; rejected rules {...} |
```

`justfile`, beside `evaluate-planning`:

```
# The visual planner in shadow against the annotated cases. Three runs with the GPU alone, then the
# minimum -- one run is not a number. P95 latency is the budget candidate for docs/next.md.
evaluate-visual model="qwen3.5:9b" provider="ollama" budget="6.0":
    uv run --directory apps/orchestrator python scripts/evaluate_visual.py --provider {{ provider }} --model {{ model }} --budget {{ budget }}
```

- [ ] **Step 4: Run**

Run: `uv run --directory apps/orchestrator pytest tests/test_evaluate_visual.py tests/test_export_chart_fixtures.py -v` and `git diff --stat apps/web/src/__fixtures__/chart-families.json` (must print nothing).
Expected: PASS, 4 + existing; fixture unchanged.

- [ ] **Step 5: Commit (three commits)**

```bash
git add apps/orchestrator/scripts/export_chart_fixtures.py
git commit -m "refactor(visual): the family datasets are one table, so the evaluation and the fixtures share a shape"
git add apps/orchestrator/evaluation/visual/cases.yaml apps/orchestrator/scripts/evaluate_visual.py apps/orchestrator/tests/test_evaluate_visual.py
git commit -m "feat(eval): the visual planner measured -- validity, suitability, stability, latency, and the one figure that argues"
git add justfile
git commit -m "build(just): evaluate-visual, three runs and the minimum"
```

---

### Task 8: the record on paper — spec, roadmap, capability matrix

**Files:**
- Modify: `docs/superpowers/specs/2026-09-03-llm-visual-planner-design.md` (Section F: the two spec fields; Section G: the no-chart case)
- Modify: `docs/next.md` (§9: wave 3 landed in shadow; how promotion is decided)
- Modify: `docs/conversational-capabilities.md` (the three visual rows: "prueba conversacional no" stays true — the corpus is still blind — but the evaluation exists; the two stale defects in «Estado del bloque» are closed)

- [ ] **Step 1: Section F** — after the seven-field line, add:

> Plus the **canonicalised proposed and shown specs**, so `changed_from_baseline=true` says what changed *to*, and suitability can be annotated from the record afterwards instead of reconstructed. Neither carries a value: `canonical()` keeps mark, channels, fields, types, orders, stack and limit.

- [ ] **Step 2: Section G** — add a bullet:

> **A result that must carry no chart is a case.** A single figure never enters the policy; the evaluation asserts it stayed out, because a planner that decorates a KPI is failing quietly.

- [ ] **Step 3: `docs/next.md` §9** — under the visual foundation's close, add a dated paragraph: wave 3 landed in `shadow`; the mode is `ORCHESTRATOR_VISUAL_PLANNER`; promotion to `hybrid` requires three runs of `just evaluate-visual` with the GPU alone, validity ≥ the floor's own verification rate (100 %), suitability and the `valid ∧ suitable ∧ changed` figure reported as min over three runs, budget set to the max P95 across the three; the decision recorded in this file with the manifests.

- [ ] **Step 4: capability matrix** — in «Estado del bloque», strike the v5-schema and label-order defects as closed (with the commit SHAs) and add the visual planner row: `Construida en shadow: el modelo propone, el dominio autoriza, el usuario ve el suelo; medida por scripts/evaluate_visual.py; promoción pendiente de tres corridas`.

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/specs/2026-09-03-llm-visual-planner-design.md docs/next.md docs/conversational-capabilities.md
git commit -m "docs(visual): wave 3 lands in shadow, the record carries both specs, and a KPI is a case"
```

---

### Task 9: wave 4 — measure, set the budget, decide the mode (runbook, not a subagent task)

This task needs the GPU alone and the model warm. It is run by a person, and its output is a decision written into `docs/next.md`, not code.

- [ ] **Step 1: Warm the model and check nothing else holds the GPU** — `nvidia-smi` shows only Ollama; `curl -s localhost:11434/api/ps` shows `qwen3.5:9b` loaded. Warm it explicitly with `num_ctx` pinned to the production value, matching `evaluation/conversations/RESULTS-E2E.md`'s own warm-up (a bare call with no `num_ctx` reloads the model at Ollama's default 262k context, which is the mistake both files now warn against the same way):
  ```bash
  curl -s http://127.0.0.1:11434/api/generate \
    -d '{"model":"qwen3.5:9b","prompt":"hola","stream":false,"keep_alive":"60m","options":{"num_ctx":4096}}' >/dev/null
  curl -s http://127.0.0.1:11434/api/ps
  ```
  **Context-size guidance (M-3f).** `ORCHESTRATOR_OLLAMA_CONTEXT_TOKENS=4096` is the production
  setting and what the warm-up above matches. On an 8 GB laptop GPU, `qwen3.5:9b` fits alongside a
  4096-token context only if nothing else pins VRAM; a larger context or a second resident model
  risks CPU offload, which inflates the very P95 this run exists to produce. If `nvidia-smi` shows
  the model partly in system RAM, stop and free the GPU rather than reading the run.
- [ ] **Step 2: Three runs** — `just evaluate-visual` three times (the recipe now defaults `--model` to `qwen3.5:9b`, the shipped and measured planner everywhere else in this repo — pass `model=<other>` to override). Budget is `ORCHESTRATOR_VISUAL_PLANNER_BUDGET_SECONDS` (default `6.0`, itself an unmeasured guess — see `bootstrap/config.py`'s comment on the field); pass `--budget <n>` in `args` to change it. Do **not** set `ORCHESTRATOR_VISUAL_PLANNER=shadow` for this step: the script hardcodes `mode="shadow"` in `evaluate_visual.py`'s `run()` regardless of that setting, so setting it here is a no-op — it matters only for Step 4's flip, below. Keep the three `last-run.json` files as `run1.json`, `run2.json`, `run3.json` under `evaluation/visual/`.
- [ ] **Step 3: Read the minimum** of validity, suitability and `valid ∧ suitable ∧ changed` across the three; the **maximum** P95 latency.
- [ ] **Step 4: Decide**, and write the decision with the numbers into `docs/next.md` §9:
  - validity < 90 % → the prompt or the schema, not the product: fix `visual_prompt.py`, re-run; do not promote.
  - `valid ∧ suitable ∧ changed` = 0 across three runs → the planner is real and decorative for these cases; stay in `shadow`, and say so — a zero is a finding, not a failure to report.
  - otherwise → set `visual_planner_budget_seconds` to the max P95 rounded up to 0.5 s (and update the "not chosen" comment beside it to say what it now is), flip `compose.yaml`'s `ORCHESTRATOR_VISUAL_PLANNER` key from `deterministic` to `hybrid`, restart the composed orchestrator, and confirm the promotion **against the deployed service**, not by re-running `evaluate_visual.py` — that script has no `--mode` flag and always evaluates `shadow`. Drive `just evaluate-e2e demo-token-laura-0001 dev` (or the visual corpus episodes, once Task 8 lands) against the running `hybrid` stack and confirm the answers it accepts match what `shadow`'s three runs called `valid`, with no new fallback reason.
- [ ] **Step 5: Commit** the three run files and the `docs/next.md` decision, one commit; the `compose.yaml` flip is its own commit with the numbers in its body.

---

## Self-review

**Spec coverage.** Section B — port (Task 2), the guarantee and the structural no-values test (Task 6's `test_the_payload_carries_shape_and_never_values`), `PresentationContext` in the payload (Task 6). Section C — draft types and verifier contract (Tasks 1, 3). Section D — every rule is a named function in Task 3 with a test that names it. Section E — four modes, `None` as abstention only, the three failures resolved by the policy, `shadow` shows the floor, budget enforced (Task 4), the eligibility guard stated at the seam (Task 5). Section F — seven fields on `VisualDecision` and in the structured line, `changed_from_baseline` canonicalised, `suitable` **not** a runtime field (Tasks 4, 5), plus the two-spec amendment (Task 8). Section G — validity, suitability, latency and fallback by cause, paraphrase stability by expected equivalence, and the no-chart case (Tasks 7, 8, 9). Sequencing — waves 3 (Tasks 1-8) and 4 (Task 9). Refusals — no raw Vega-Lite (the schema is the draft), no second model (Task 6 uses `settings.planner_model`), no repair (Task 3), no decision in a mapper (Task 5 keeps it in the application), no amount to the model (Task 6's test).

**Placeholder scan.** Task 5 had one ("wire `LlmVisualPlanner` before it exists") — resolved by moving the `main.py` edit into Task 6. Task 7's `run()` is described with the exact provider/manifest imports and the report table rather than full code; the parts the tests pin (`Case`, `load_cases`, `Results`, `judge`, `Stability`, `stability`, `PROFILES`) are written in full. Task 9 is a runbook by design.

**Type consistency.** `VisualPlannerPort.plan(question, profile, presentation, *, budget_seconds)` in Tasks 2, 4, 6, 7. `verify(draft, profile) -> ChartSpec | Rejection` in Tasks 3, 4, 5, 7. `VisualPlanningPolicy(model=, fallback=, verifier=, mode=, budget_seconds=)` and `.decide(question, profile, presentation)` in Tasks 4, 5, 7. `VisualDecision` fields `proposed, valid, accepted, changed_from_baseline, latency_ms, proposed_spec, rejected_rule` in Tasks 4, 5, 7. `canonical(spec)` in Tasks 1, 4, 5, 7. `FAMILY_DATASETS[name] -> (VisualDataset, kwargs)` in Task 7 only. `Step.VISUAL_PLANNER` in Task 5 only.

**One risk worth naming.** Task 4's policy computes the floor *before* awaiting the model in `shadow`/`hybrid` so `changed_from_baseline` has both sides; that means the floor's cost is paid on every turn in those modes. It is microseconds and it is the design's stated choice — but if a reviewer sees it as waste, the answer is in Section E's first sentence, not in reordering.
