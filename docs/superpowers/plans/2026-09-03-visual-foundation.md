# Visual Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the visual decision a single, testable, application-layer choice, and close the two chart defects the rubric review names under *Visual logic* — stacked categorical comparisons and label-ordered categories — without a single model call.

**Architecture:** The chart decision moves out of a synchronous DTO mapper and into the application layer, where an async port could later ask a model. `VisualProfile` stops being derived from the chart and starts describing the dataset. The channel and sort vocabulary widens so that "grouped, not stacked" and "two independent orders" become representable rather than aspirational. A Vega-Lite compile gate over every emitted family stops a spec that renders from drifting from a spec that is declared.

**Tech Stack:** Python 3.12, dataclasses, `pytest`, `ruff`, `mypy` (orchestrator); TypeScript, `vitest`, `vega-lite ^6.4.3` (web).

**Spec:** `docs/superpowers/specs/2026-09-03-llm-visual-planner-design.md`

## Global Constraints

- **No model call in this plan.** Waves 1 and 2 are deterministic. The LLM planner is a separate plan, written after this one lands.
- **Ruff line limit 100.** Gates that must pass before every commit: `uv run --directory apps/orchestrator pytest -q -m "not integration"`, `ruff check .`, `ruff format --check .`, `mypy src`. Web: `npm --prefix apps/web run test` and `npm --prefix apps/web run typecheck`.
- **Suite baseline before starting: `3,545 + 190 = 3,735 passed, 3 skipped`** on the orchestrator, run in two halves (`--deselect tests/test_conversation_corpus.py`, then that file alone) because long runs on this machine intermittently stall. Establish it yourself before Task 1 and report it; do not trust this number.
- **The conversation corpus must stay green with all four rates at 0.0 %.**
- **Never edit a `Ceiling` or a `Floor`,** and never edit a dataset case or corpus episode to move a number.
- **Atomic commits, one change per commit. Never add a `Co-Authored-By` trailer.**
- `apps/web` has `vega-lite ^6.4.3`; the orchestrator emits `vega-lite/v5.json` today. Task 9 fixes that and gates it.

---

## File Structure

| file | responsibility |
| --- | --- |
| `apps/orchestrator/src/finguard_orchestrator/domain/models/chart.py` | Modify. `VisualField`, the inverted `VisualProfile`, `PresentationContext`, `Stack`, the widened `Channel`, per-encoding sort on `ChartEncoding`. |
| `.../domain/services/visual_profile.py` | **Create.** `visual_profile_of(dataset, total_minor)` — the dataset→profile factory, moved out of the mapper. |
| `.../domain/services/deterministic_visual_planner.py` | **Create.** `DeterministicVisualPlanner.plan(profile) -> ChartSpec \| None`. The floor, and later the baseline. |
| `.../application/queries/ask_financial_question.py` | Modify. Build the profile, ask the planner, put a `VisualDecision` on the result. |
| `.../infrastructure/mappers/llm_mapper.py` | Modify. Delete `_chart_for_dataset`; render the decided spec. Both call sites collapse into one. |
| `.../infrastructure/visualization/vega_lite.py` | Modify. Schema v6, `stack: null` for `Stack.disabled`, offset channels, per-encoding sort. |
| `apps/orchestrator/tests/test_visual_profile.py` | **Create.** The factory, from datasets. |
| `apps/orchestrator/tests/test_deterministic_visual_planner.py` | **Create.** The floor's rules, including the two rubric defects. |
| `apps/orchestrator/tests/test_visualization_stage1.py`, `..._stage2_core.py` | Modify. Existing chart tests follow the decision's new home. |
| `apps/orchestrator/scripts/export_chart_fixtures.py` | **Create.** Emits one fixture per chart family for the web compile gate. |
| `apps/web/src/chartFamilies.test.ts` | **Create.** The compile gate: declared schema version, and every family compiles. |

---

## Wave 1 — the profile changes direction, and the decision leaves the mapper

### Task 1: `VisualField` and the inverted `VisualProfile`

**Files:**
- Modify: `apps/orchestrator/src/finguard_orchestrator/domain/models/chart.py:66-110`
- Test: `apps/orchestrator/tests/test_visualization_stage1.py`

**Interfaces:**
- Consumes: `FieldType` (already in `chart.py:9`).
- Produces: `VisualField(name, type, role, cardinality, order)`; `VisualProfile(fields, metric, unit, positive, mutually_exclusive, verified_total, comparison)`; `VisualProfile.field_names() -> frozenset[str]`; `VisualProfile.field(name) -> VisualField | None`; `VisualProfile.role(role) -> tuple[VisualField, ...]`.

- [ ] **Step 1: Write the failing test**

```python
def test_a_profile_describes_its_dataset_and_not_a_chart() -> None:
    """The profile is the planner's input, so it cannot be derived from the chart it decides."""
    profile = VisualProfile(
        fields=(
            VisualField("dimension", "nominal", "dimension", 5, "MEASURE_DESC"),
            VisualField("value_minor", "quantitative", "measure", 5),
        ),
        metric="SUM",
        unit="money",
        positive=True,
        mutually_exclusive=True,
        verified_total=True,
        comparison=False,
    )

    assert profile.field_names() == frozenset({"dimension", "value_minor"})
    assert profile.field("value_minor").role == "measure"
    assert profile.role("measure")[0].name == "value_minor"
    assert profile.field("nope") is None


def test_two_fields_may_share_the_dimension_role_when_their_types_differ() -> None:
    """RECURRENCE puts `occurred_at` temporal on x and `dimension` nominal on y.

    They are told apart by type, not by a fourth role. A `timestamp` role would be the same
    fact written twice, which is the defect family this repository keeps finding.
    """
    profile = VisualProfile(
        fields=(
            VisualField("occurred_at", "temporal", "dimension", 12, "TEMPORAL"),
            VisualField("dimension", "nominal", "dimension", 3, "INPUT"),
            VisualField("value_minor", "quantitative", "measure", 12),
        ),
        metric="RECURRENCE",
        unit="money",
        positive=True,
        mutually_exclusive=False,
        verified_total=False,
        comparison=False,
    )

    assert len(profile.role("dimension")) == 2
    assert {item.type for item in profile.role("dimension")} == {"temporal", "nominal"}


def test_a_profile_refuses_a_duplicate_field_name() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        VisualProfile(
            fields=(
                VisualField("dimension", "nominal", "dimension", 2),
                VisualField("dimension", "temporal", "dimension", 2),
            ),
            metric="SUM",
            unit="money",
            positive=True,
            mutually_exclusive=True,
            verified_total=False,
            comparison=False,
        )


def test_a_measure_must_be_quantitative() -> None:
    """The role's type is a fact about the dataset, not a choice. A nominal measure is a
    dataset that has been mislabelled, and the planner would inherit the lie."""
    with pytest.raises(ValueError, match="measure"):
        VisualField("value_minor", "nominal", "measure", 5)


def test_a_negative_cardinality_is_not_a_count() -> None:
    with pytest.raises(ValueError, match="cardinality"):
        VisualField("dimension", "nominal", "dimension", -1)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --directory apps/orchestrator pytest tests/test_visualization_stage1.py -k "profile or measure_must or cardinality" -v`

Expected: FAIL with `NameError: name 'VisualField' is not defined` (and, for the first test, `TypeError` about unexpected keyword arguments once `VisualField` exists but `VisualProfile` has not changed).

- [ ] **Step 3: Write the implementation**

Replace `chart.py`'s `VisualProfile` (lines 66-110) with:

```python
FieldRole = Literal["dimension", "measure", "series"]
FieldOrder = Literal["MEASURE_DESC", "INPUT", "TEMPORAL", "NONE"]

_ROLE_TYPES: Final[dict[FieldRole, frozenset[str]]] = {
    # The role's type is a fact about the dataset rather than a choice. A `measure` that is not
    # quantitative is a mislabelled dataset, and every rule downstream would inherit the lie.
    "measure": frozenset({"quantitative"}),
    "series": frozenset({"nominal"}),
    "dimension": frozenset({"nominal", "temporal"}),
}


@dataclass(frozen=True)
class VisualField:
    """One column of the dataset, described so a planner can choose a channel for it.

    `role` exists because `type` cannot carry it. The dataset has two nominal fields --
    `dimension` and `series` -- and nothing in their type says which is the axis and which is the
    colour split. `cardinality` is per field for the same reason: two series are a comparison and
    fifteen are an unreadable legend, and a single count on the profile could not tell them apart.
    """

    name: str
    type: FieldType
    role: FieldRole
    cardinality: int
    order: FieldOrder = "NONE"

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", self.name):
            raise ValueError("visual fields must be identifiers")
        if self.type not in {"nominal", "quantitative", "temporal"}:
            raise ValueError("unsupported visual field type")
        if self.role not in _ROLE_TYPES:
            raise ValueError("unsupported visual field role")
        if self.type not in _ROLE_TYPES[self.role]:
            raise ValueError(f"a {self.role} cannot be {self.type}")
        if self.cardinality < 0:
            raise ValueError("cardinality cannot be negative")
        if self.order not in {"MEASURE_DESC", "INPUT", "TEMPORAL", "NONE"}:
            raise ValueError("unsupported visual field order")


@dataclass(frozen=True)
class VisualProfile:
    """Value-free facts about the *dataset*, safe to send in a conversation round trip.

    It used to be derived from a `ChartSpec` through `from_spec`, which is backwards for a planner:
    the profile is the input to the chart decision, not a summary of its output. Nothing ephemeral
    belongs here -- not the question, not the viewport -- because `conversation.py` validates this
    object coming back from the browser, so anything added to it becomes wire surface.
    """

    fields: tuple[VisualField, ...]
    metric: str
    unit: str
    positive: bool = False
    mutually_exclusive: bool = False
    verified_total: bool = False
    comparison: bool = False

    def __post_init__(self) -> None:
        names = [item.name for item in self.fields]
        if len(set(names)) != len(names):
            raise ValueError("duplicate field name in visual profile")
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", self.metric):
            raise ValueError("visual profile metric must be uppercase")
        if self.unit not in {"money", "count", "ratio", "unknown"}:
            raise ValueError("unsupported visual profile unit")

    def field_names(self) -> frozenset[str]:
        return frozenset(item.name for item in self.fields)

    def field(self, name: str) -> VisualField | None:
        return next((item for item in self.fields if item.name == name), None)

    def role(self, role: FieldRole) -> tuple[VisualField, ...]:
        return tuple(item for item in self.fields if item.role == role)

    def temporal(self) -> bool:
        """Whether any dimension is temporal. Not a stored field: it is a question about types."""
        return any(item.type == "temporal" for item in self.role("dimension"))
```

Add `Final` to the `typing` import on line 4 if it is not already there.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --directory apps/orchestrator pytest tests/test_visualization_stage1.py -k "profile or measure_must or cardinality" -v`

Expected: PASS, 5 tests.

- [ ] **Step 5: Run both suite halves — this breaks `from_spec`'s caller on purpose**

Run: `uv run --directory apps/orchestrator pytest -q -m "not integration" --deselect tests/test_conversation_corpus.py`

Expected: FAIL in `llm_mapper.py:330` (`VisualProfile.from_spec` no longer exists) and in whatever chart tests construct the old profile. **This is the expected state at the end of Task 1** — Task 2 gives the caller its replacement. Record the failing test names in the commit message so Task 2 can confirm it fixed exactly those.

- [ ] **Step 6: Commit**

```bash
git add apps/orchestrator/src/finguard_orchestrator/domain/models/chart.py \
        apps/orchestrator/tests/test_visualization_stage1.py
git commit -m "$(cat <<'EOF'
refactor(chart): the profile describes the dataset, not the chart it decides

`VisualProfile.from_spec(spec)` derived the profile from the chart, which is backwards for a
planner: the profile is the *input* to the chart decision. Inverting it is what makes a visual
planner possible at all.

`VisualField` carries `role` because `type` cannot: the dataset has two nominal fields and
nothing in their type says which is the axis and which is the colour split. `cardinality` and
`order` sit on the field rather than the profile -- one cardinality could not say whether a
series has two values or fifteen, and one order would force the legend to inherit the axis's
rule.

Three roles, not four. A temporal dimension is `type=temporal, role=dimension`; RECURRENCE
carries two dimensions told apart by type. A `timestamp` role would be the same fact written
twice, which is the defect family this repository has spent a week finding.

The suite is red at this commit: `llm_mapper.py:330` still calls `from_spec`. Task 2 replaces
it with the dataset factory.
EOF
)"
```

---

### Task 2: the dataset → profile factory

**Files:**
- Create: `apps/orchestrator/src/finguard_orchestrator/domain/services/visual_profile.py`
- Create: `apps/orchestrator/tests/test_visual_profile.py`
- Modify: `apps/orchestrator/src/finguard_orchestrator/infrastructure/mappers/llm_mapper.py:326-350`

**Interfaces:**
- Consumes: `VisualField`, `VisualProfile` from Task 1; `VisualDataset` and `VisualRow` (already in the domain — read their definitions before writing, they are what `result.visual_dataset` holds).
- Produces: `visual_profile_of(dataset: VisualDataset, *, total_minor: int | None) -> VisualProfile`.

- [ ] **Step 1: Write the failing test**

```python
def test_a_breakdown_profile_names_its_dimension_and_measure() -> None:
    dataset = VisualDataset(
        metric="SUM",
        rows=(
            VisualRow(dimension="GROCERIES", value_minor=24_908),
            VisualRow(dimension="RESTAURANTS", value_minor=10_385),
        ),
    )

    profile = visual_profile_of(dataset, total_minor=35_293)

    assert profile.field("dimension").role == "dimension"
    assert profile.field("dimension").cardinality == 2
    assert profile.field("dimension").order == "MEASURE_DESC"
    assert profile.field("value_minor").role == "measure"
    assert profile.unit == "money"
    assert profile.positive is True
    assert profile.verified_total is True          # the rows sum to the stated total
    assert profile.comparison is False


def test_a_series_gets_its_own_cardinality_and_makes_this_a_comparison() -> None:
    """Two series are a comparison; the count is what later refuses fifteen of them."""
    dataset = VisualDataset(
        metric="SUM",
        rows=(
            VisualRow(dimension="GROCERIES", value_minor=100, series="2026-07"),
            VisualRow(dimension="GROCERIES", value_minor=90, series="2026-06"),
            VisualRow(dimension="RESTAURANTS", value_minor=50, series="2026-07"),
        ),
    )

    profile = visual_profile_of(dataset, total_minor=None)

    assert profile.comparison is True
    assert profile.field("series").role == "series"
    assert profile.field("series").cardinality == 2
    assert profile.field("dimension").cardinality == 2
    assert profile.mutually_exclusive is False     # series means the rows are not disjoint parts


def test_a_frequency_dimension_keeps_the_order_the_dataset_built() -> None:
    """FREQUENCY is weekday names: lunes precedes martes because the producer said so, and
    sorting them by magnitude would destroy the only thing the chart is for."""
    dataset = VisualDataset(
        metric="FREQUENCY",
        rows=(
            VisualRow(dimension="lunes", value_minor=4),
            VisualRow(dimension="martes", value_minor=9),
        ),
    )

    profile = visual_profile_of(dataset, total_minor=None)

    assert profile.field("dimension").order == "INPUT"
    assert profile.unit == "count"


def test_a_temporal_dimension_is_ordered_temporally() -> None:
    dataset = VisualDataset(
        metric="SUM",
        group_by="MONTH",
        rows=(
            VisualRow(dimension="2026-01", value_minor=100),
            VisualRow(dimension="2026-02", value_minor=200),
        ),
    )

    profile = visual_profile_of(dataset, total_minor=None)

    assert profile.field("dimension").type == "temporal"
    assert profile.field("dimension").order == "TEMPORAL"
    assert profile.temporal() is True


def test_a_recurrence_dataset_carries_two_dimensions() -> None:
    dataset = VisualDataset(
        metric="RECURRENCE",
        rows=(
            VisualRow(dimension="Netflix", value_minor=1_299, occurred_at="2026-07-03"),
            VisualRow(dimension="Spotify", value_minor=1_099, occurred_at="2026-07-05"),
        ),
    )

    profile = visual_profile_of(dataset, total_minor=None)

    assert {item.name for item in profile.role("dimension")} == {"dimension", "occurred_at"}
    assert profile.field("occurred_at").type == "temporal"
    assert profile.field("occurred_at").order == "TEMPORAL"


def test_a_profile_carries_no_value_from_any_row() -> None:
    """The whole point of the type. A sentinel, because the profile legitimately holds counts."""
    dataset = VisualDataset(
        metric="SUM",
        rows=(VisualRow(dimension="GROCERIES", value_minor=734_291),),
    )

    profile = visual_profile_of(dataset, total_minor=734_291)

    assert "734291" not in repr(profile)
    assert "GROCERIES" not in repr(profile)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --directory apps/orchestrator pytest tests/test_visual_profile.py -v`

Expected: FAIL with `ModuleNotFoundError: No module named 'finguard_orchestrator.domain.services.visual_profile'`.

**Before Step 3, read `VisualDataset` and `VisualRow`.** Run `grep -rn "class VisualDataset\|class VisualRow" -A14 apps/orchestrator/src/finguard_orchestrator/domain/`. The tests above assume `metric`, `rows`, `group_by`, and rows with `dimension`, `value_minor`, `series`, `occurred_at`. If any name differs, **fix the test to the real name, not the reverse** — and say so in the commit.

- [ ] **Step 3: Write the implementation**

```python
"""The dataset described so a planner can choose a chart for it, and nothing more.

This logic lived inline in `llm_mapper._visualization_to_dto`, feeding
`VisualProfile.from_spec`. It moves here for two reasons: a mapper builds transport DTOs and
should not compute domain facts, and the profile is now the *input* to a decision that the
application makes rather than a summary of one the mapper already made.

**Nothing here reads a value.** Cardinality is a count of distinct labels, `positive` is a
comparison against zero, and `verified_total` is an equality. No amount and no dimension label
survives into the returned object, and a test asserts that with sentinels.
"""

from finguard_orchestrator.domain.models.chart import VisualField, VisualProfile

# Group keys that name a calendar bucket. Same set `derive_chart` has always used; stated once
# here so the profile and the floor cannot disagree about what "temporal" means.
_TEMPORAL_GROUPS = frozenset({"DAY", "WEEK", "MONTH", "PERIOD", "DATE"})

# Metrics whose measure is a number of movements rather than money.
_COUNTING = frozenset({"COUNT", "FREQUENCY"})


def _unit(metric: str) -> str:
    if metric in _COUNTING:
        return "count"
    return "ratio" if metric == "RATIO_SHARE" else "money"


def _dimension_order(metric: str, temporal: bool) -> str:
    """What ordering the dimension asks for, which is a property of the question, not the chart.

    FREQUENCY is the row that earns this function: its dimension is nominal weekday names in the
    order the producer built them, and sorting those by magnitude destroys the only thing the
    chart is for. RECURRENCE likewise names streams rather than ranking them.
    """
    if temporal:
        return "TEMPORAL"
    if metric in {"FREQUENCY", "RECURRENCE"}:
        return "INPUT"
    return "MEASURE_DESC"


def visual_profile_of(dataset: VisualDataset, *, total_minor: int | None) -> VisualProfile:
    rows = dataset.rows
    temporal = getattr(dataset, "group_by", None) in _TEMPORAL_GROUPS
    has_series = any(row.series is not None for row in rows)
    has_occurred_at = any(row.occurred_at is not None for row in rows)

    fields: list[VisualField] = [
        VisualField(
            "dimension",
            "temporal" if temporal else "nominal",
            "dimension",
            len({row.dimension for row in rows}),
            _dimension_order(dataset.metric, temporal),
        ),
        VisualField("value_minor", "quantitative", "measure", len(rows)),
    ]
    if has_series:
        # `INPUT` and not a magnitude order: a legend of periods reads in the order the producer
        # built them, and the producer builds the latest window first.
        fields.append(
            VisualField(
                "series",
                "nominal",
                "series",
                len({row.series for row in rows if row.series is not None}),
                "INPUT",
            )
        )
    if has_occurred_at:
        fields.append(
            VisualField(
                "occurred_at",
                "temporal",
                "dimension",
                len({row.occurred_at for row in rows if row.occurred_at is not None}),
                "TEMPORAL",
            )
        )

    return VisualProfile(
        fields=tuple(fields),
        metric=dataset.metric,
        unit=_unit(dataset.metric),
        positive=all(row.value_minor > 0 for row in rows),
        # A series means the rows are slices of one dimension rather than disjoint parts of a
        # whole, so they cannot be the parts a donut claims sum to the total.
        mutually_exclusive=not has_series,
        verified_total=(
            dataset.metric == "RATIO_SHARE"
            or (total_minor is not None and sum(row.value_minor for row in rows) == total_minor)
        ),
        comparison=has_series,
    )
```

Add the `VisualDataset` import from wherever Step 2's grep found it.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --directory apps/orchestrator pytest tests/test_visual_profile.py -v`

Expected: PASS, 6 tests.

- [ ] **Step 5: Replace the mapper's inline construction**

In `llm_mapper.py`, delete the `VisualProfile.from_spec(...)` block (lines 330-350) and call the factory:

```python
    profile = visual_profile_of(result.visual_dataset, total_minor=result.total_minor)
```

Fix the import: drop `VisualProfile` if now unused, add
`from finguard_orchestrator.domain.services.visual_profile import visual_profile_of`.

- [ ] **Step 6: Run both suite halves — Task 1's failures must be gone**

Run:
```
uv run --directory apps/orchestrator pytest -q -m "not integration" --deselect tests/test_conversation_corpus.py
uv run --directory apps/orchestrator pytest -q -m "not integration" tests/test_conversation_corpus.py
```

Expected: PASS, and the specific tests Task 1 recorded as failing are green again. If a chart test now fails on a *different* assertion, that is a behaviour change and it must be understood before proceeding — the profile's values are supposed to be identical to what `from_spec` produced for every existing dataset.

- [ ] **Step 7: Run the linters**

Run: `cd apps/orchestrator && uv run ruff check . && uv run ruff format --check . && uv run mypy src`

Expected: all clean.

- [ ] **Step 8: Commit**

```bash
git add apps/orchestrator/src/finguard_orchestrator/domain/services/visual_profile.py \
        apps/orchestrator/tests/test_visual_profile.py \
        apps/orchestrator/src/finguard_orchestrator/infrastructure/mappers/llm_mapper.py
git commit -m "$(cat <<'EOF'
feat(chart): the dataset describes itself, and a mapper stops computing domain facts

`visual_profile_of(dataset, total_minor=...)` replaces the inline construction that fed
`VisualProfile.from_spec` from inside a transport mapper. Same values for every existing
dataset -- the suite is the proof -- but now derived from the data rather than from a chart
somebody already chose.

The ordering decision moves in with it, and FREQUENCY is why it exists: its dimension is
weekday names in producer order, so `INPUT`. A blanket "categorical sorts by value" would
have put lunes anywhere, which is the trap a broad rule falls into.

Nothing here reads a value. Cardinality is a count of distinct labels, `positive` a comparison
against zero, `verified_total` an equality -- and a sentinel test asserts no amount and no
dimension label survives into the profile.
EOF
)"
```

---

### Task 3: `PresentationContext`, and the wire surface it needs

**Files:**
- Modify: `apps/orchestrator/src/finguard_orchestrator/domain/models/chart.py` (append after `VisualProfile`)
- Modify: `apps/orchestrator/src/finguard_orchestrator/infrastructure/dto/llm_dto.py:613` area
- Test: `apps/orchestrator/tests/test_visualization_stage1.py`, `apps/orchestrator/tests/test_llm_endpoints.py`

**Interfaces:**
- Produces: `PresentationContext(channel, viewport, max_visible_rows)` with `DEFAULT_PRESENTATION`.

- [ ] **Step 1: Write the failing test**

```python
def test_presentation_is_not_part_of_the_profile() -> None:
    """The profile round-trips through the browser (`conversation.py` validates it coming back),
    so anything put in it becomes wire surface. Viewport and channel are circumstances of one
    execution, not properties of the data."""
    profile_keys = {item.name for item in dataclasses.fields(VisualProfile)}

    assert "channel" not in profile_keys
    assert "viewport" not in profile_keys
    assert "max_visible_rows" not in profile_keys
    assert "question" not in profile_keys


def test_a_request_that_names_no_viewport_still_has_one() -> None:
    """The planner cannot wait on a web deployment, so the default is part of the contract."""
    assert DEFAULT_PRESENTATION.channel == "TEXT"
    assert DEFAULT_PRESENTATION.viewport == "DESKTOP"
    assert DEFAULT_PRESENTATION.max_visible_rows == ROWS_THAT_FIT


def test_a_mobile_viewport_shows_fewer_rows_than_it_is_asked_for() -> None:
    context = PresentationContext(channel="TEXT", viewport="MOBILE", max_visible_rows=40)

    assert context.max_visible_rows == 40          # stated wins over guessed
    with pytest.raises(ValueError, match="max_visible_rows"):
        PresentationContext(channel="TEXT", viewport="MOBILE", max_visible_rows=0)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --directory apps/orchestrator pytest tests/test_visualization_stage1.py -k presentation -v`

Expected: FAIL with `NameError: name 'PresentationContext' is not defined`.

- [ ] **Step 3: Write the implementation**

In `chart.py`, after `VisualProfile`:

```python
@dataclass(frozen=True)
class PresentationContext:
    """The circumstances of *this* execution, which are not facts about the data.

    Deliberately not part of `VisualProfile`: that object is validated coming back from the
    browser, so a field added to it becomes wire surface and travels on every turn.
    """

    channel: Literal["TEXT", "VOICE"]
    viewport: Literal["MOBILE", "DESKTOP"]
    max_visible_rows: int

    def __post_init__(self) -> None:
        if self.channel not in {"TEXT", "VOICE"}:
            raise ValueError("unsupported presentation channel")
        if self.viewport not in {"MOBILE", "DESKTOP"}:
            raise ValueError("unsupported viewport")
        if not 1 <= self.max_visible_rows <= 100:
            raise ValueError("max_visible_rows must be between 1 and 100")


# What a client that says nothing gets. `channel` already exists on the request DTO; `viewport`
# and `max_visible_rows` are new wire surface, and a default is what stops the visual work from
# waiting on a web deployment.
DEFAULT_PRESENTATION: Final = PresentationContext(
    channel="TEXT", viewport="DESKTOP", max_visible_rows=ROWS_THAT_FIT
)
```

`DEFAULT_PRESENTATION` must be declared **after** `ROWS_THAT_FIT` (currently `chart.py:271`); move the constant above `VisualProfile` if the module order requires it, and say so in the commit.

- [ ] **Step 4: Add the two optional request fields**

In `llm_dto.py`, beside the existing `channel: Literal["TEXT", "VOICE"] = "TEXT"` (line ~613):

```python
    viewport: Literal["MOBILE", "DESKTOP"] = "DESKTOP"
    max_visible_rows: int = Field(default=ROWS_THAT_FIT, ge=1, le=100)
```

- [ ] **Step 5: Write the endpoint test**

```python
def test_a_request_may_state_its_viewport_and_a_request_that_does_not_still_works(client) -> None:
    """`extra="forbid"` means a field must be *declared* to be accepted, and declared means an
    old client that omits it keeps working."""
    without = client.post("/v1/questions", json={"question": "¿cuánto gasté este mes?"},
                          headers=AUTH)
    assert without.status_code in {200, 422}       # 422 only for an unrelated planning reason

    with_viewport = client.post(
        "/v1/questions",
        json={"question": "¿cuánto gasté este mes?", "viewport": "MOBILE",
              "max_visible_rows": 5},
        headers=AUTH,
    )
    assert with_viewport.status_code == without.status_code

    rejected = client.post(
        "/v1/questions",
        json={"question": "¿cuánto gasté este mes?", "viewport": "WATCH"},
        headers=AUTH,
    )
    assert rejected.status_code == 422
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run --directory apps/orchestrator pytest tests/test_visualization_stage1.py -k presentation tests/test_llm_endpoints.py -k viewport -v`

Expected: PASS.

- [ ] **Step 7: Run both suite halves and the linters**

Run the two halves plus `ruff check .`, `ruff format --check .`, `mypy src`.

Expected: all clean, counts back to baseline plus the new tests.

- [ ] **Step 8: Commit**

```bash
git add apps/orchestrator/src/finguard_orchestrator/domain/models/chart.py \
        apps/orchestrator/src/finguard_orchestrator/infrastructure/dto/llm_dto.py \
        apps/orchestrator/tests/test_visualization_stage1.py \
        apps/orchestrator/tests/test_llm_endpoints.py
git commit -m "$(cat <<'EOF'
feat(chart): presentation is a circumstance of one turn, not a fact about the data

`PresentationContext` carries channel, viewport and row budget, and it is deliberately not part
of `VisualProfile`: `conversation.py` validates the profile coming back from the browser, so a
field added there becomes wire surface on every turn.

`channel` already existed on the request DTO. `viewport` and `max_visible_rows` are new and
optional, with a default, because the visual work cannot wait on a web deployment -- and
`extra="forbid"` means a declared optional field is exactly what keeps an older client working
while rejecting a viewport nobody supports.
EOF
)"
```

---

### Task 4: the decision leaves the mapper

**Files:**
- Create: `apps/orchestrator/src/finguard_orchestrator/domain/services/deterministic_visual_planner.py`
- Create: `apps/orchestrator/tests/test_deterministic_visual_planner.py`
- Modify: `apps/orchestrator/src/finguard_orchestrator/application/queries/ask_financial_question.py`
- Modify: `apps/orchestrator/src/finguard_orchestrator/infrastructure/mappers/llm_mapper.py:208-260, 320-360`

**Interfaces:**
- Consumes: `visual_profile_of` (Task 2), `PresentationContext`/`DEFAULT_PRESENTATION` (Task 3).
- Produces: `DeterministicVisualPlanner.plan(profile, presentation) -> ChartSpec | None`; `VisualDecision(spec, source, rationale, fallback_reason)` on `AskFinancialQuestionResult.visual_decision`.

- [ ] **Step 1: Write the failing test**

```python
def test_the_floor_reproduces_todays_chart_for_a_breakdown() -> None:
    """Task 4 is a move, not a behaviour change. This is the assertion that says so."""
    profile = visual_profile_of(
        VisualDataset(
            metric="SUM",
            rows=(
                VisualRow(dimension="GROCERIES", value_minor=24_908),
                VisualRow(dimension="RESTAURANTS", value_minor=10_385),
            ),
        ),
        total_minor=None,
    )

    spec = DeterministicVisualPlanner().plan(profile, DEFAULT_PRESENTATION)

    assert spec.mark == "bar"
    assert {(e.channel, e.field) for e in spec.encodings} >= {
        ("y", "dimension"),
        ("x", "value_minor"),
    }


def test_the_floor_declines_a_single_row() -> None:
    """One row is a KPI, and the eligibility guard keeps it out of the policy entirely. The
    floor returning None is what that guard reads."""
    profile = visual_profile_of(
        VisualDataset(metric="SUM", rows=(VisualRow(dimension="GROCERIES", value_minor=100),)),
        total_minor=None,
    )

    assert DeterministicVisualPlanner().plan(profile, DEFAULT_PRESENTATION) is None


def test_a_temporal_dimension_is_a_line_and_a_cumulative_one_is_an_area() -> None:
    for metric, mark in (("SUM", "line"), ("CUMULATIVE", "area")):
        profile = visual_profile_of(
            VisualDataset(
                metric=metric,
                group_by="MONTH",
                rows=(
                    VisualRow(dimension="2026-01", value_minor=100),
                    VisualRow(dimension="2026-02", value_minor=200),
                ),
            ),
            total_minor=None,
        )

        spec = DeterministicVisualPlanner().plan(profile, DEFAULT_PRESENTATION)

        assert spec.mark == mark, metric


def test_a_decision_records_where_it_came_from() -> None:
    decision = VisualDecision(spec=some_bar_spec(), source="deterministic")

    assert decision.source == "deterministic"
    assert decision.rationale is None
    assert decision.fallback_reason is None        # nothing fell back; the floor was the plan
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --directory apps/orchestrator pytest tests/test_deterministic_visual_planner.py -v`

Expected: FAIL with `ModuleNotFoundError` for `deterministic_visual_planner`.

- [ ] **Step 3: Write the floor**

```python
"""The chart a rule chooses, which is three things at once.

It is the **availability guarantee** (a deployment with no visual model behaves exactly as it
does today), the **fallback** when a model planner abstains or fails, and the **experimental
baseline** the model's proposal is compared against. That third role is why it is injected
separately rather than being the port's default implementation: computing "did the model choose
something different" needs both planners at once.

It wraps `derive_chart`, which stays where it is and stays pure. All this adds is the profile as
an input instead of loose arguments.
"""

from dataclasses import dataclass
from typing import Literal

from finguard_orchestrator.domain.models.chart import (
    ChartSpec,
    PresentationContext,
    VisualProfile,
    derive_chart,
)


@dataclass(frozen=True)
class VisualDecision:
    """Which chart was chosen, by whom, and why the other one was not.

    `source` is `llm | deterministic` and not `llm | deterministic_fallback`: in the
    deterministic and shadow modes the floor is the *intended* selection rather than a recovery.
    `fallback_reason` is filled only when an attempt was made and failed.
    """

    spec: ChartSpec
    source: Literal["llm", "deterministic"]
    rationale: str | None = None
    fallback_reason: str | None = None

    def __post_init__(self) -> None:
        if self.source not in {"llm", "deterministic"}:
            raise ValueError("unsupported visual decision source")
        if self.fallback_reason is not None and self.source == "llm":
            raise ValueError("a chosen model plan did not fall back")


class DeterministicVisualPlanner:
    def plan(
        self, profile: VisualProfile, presentation: PresentationContext
    ) -> ChartSpec | None:
        measure = profile.role("measure")
        dimension = next(
            (item for item in profile.role("dimension") if item.type != "temporal"),
            None,
        ) or next(iter(profile.role("dimension")), None)
        if not measure or dimension is None:
            return None
        return derive_chart(
            group_by="MONTH" if profile.temporal() else "NONE",
            metric=profile.metric,
            rows=measure[0].cardinality,
            fields=profile.field_names(),
            dimension_field=dimension.name,
            value_field=measure[0].name,
            plan_limited=False,
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run --directory apps/orchestrator pytest tests/test_deterministic_visual_planner.py -v`

Expected: PASS, 4 tests. If `test_the_floor_reproduces_todays_chart_for_a_breakdown` fails on channels, read `derive_chart`'s non-temporal branch (`chart.py:300-320`) and fix the **test** to the real channels — the point of this test is that the move changes nothing.

- [ ] **Step 5: Put the decision on the result and have the mapper read it**

In `ask_financial_question.py`, where the result is assembled with `visual_dataset`, add:

```python
        visual_decision = None
        if result.visual_dataset is not None and len(result.visual_dataset.rows) > 1:
            # The eligibility guard. A single figure is a KPI and never enters the visual policy;
            # every request that passes here ends in a ChartSpec.
            profile = visual_profile_of(
                result.visual_dataset, total_minor=result.total_minor
            )
            spec = self._visual_floor.plan(profile, presentation)
            if spec is not None:
                visual_decision = VisualDecision(spec=spec, source="deterministic")
```

In `llm_mapper.py`, **delete `_chart_for_dataset` entirely** and replace both call sites
(`:236` and `:326`) with `result.visual_decision.spec`. Note in the commit that the decision was
being taken **twice per turn** — once for the DTO and once for the conversational state — and is
now taken once.

- [ ] **Step 6: Run both suite halves and the linters**

Run the two halves plus `ruff check .`, `ruff format --check .`, `mypy src`.

Expected: PASS. Existing chart tests that called `_chart_for_dataset` must be pointed at
`DeterministicVisualPlanner` — that is a test edit, not a behaviour change, and the assertions
inside them must not move.

- [ ] **Step 7: Commit**

```bash
git add -A apps/orchestrator
git commit -m "$(cat <<'EOF'
refactor(visual): the chart decision leaves the transport mapper

`derive_chart` was called from `_chart_for_dataset`, a synchronous function inside
`llm_mapper.py`. A mapper builds DTOs; a model call cannot live there, so the decision has to
move up before a visual planner is possible at all. The application decides, and the mapper
renders what it is handed.

It was also being decided **twice per turn** -- once at `:236` for the response DTO and again at
`:326` for the conversational state. One decision now, carried on the result as a
`VisualDecision`.

`DeterministicVisualPlanner` wraps `derive_chart` unchanged and is injected separately rather
than as a port default, because the baseline comparison a model planner needs requires both
planners at once. `source` is `llm | deterministic`: the floor is a legitimate selection, not
always a recovery.

The eligibility guard is stated where it belongs: a dataset with one row is a KPI and never
enters the visual policy, which is the precondition that makes "every request ends in a
ChartSpec" true.
EOF
)"
```

---

## Wave 2 — the floor and the guard rails

### Task 5: `Stack.disabled` and the offset channels

**Files:**
- Modify: `apps/orchestrator/src/finguard_orchestrator/domain/models/chart.py:7-8, 28-60`
- Modify: `apps/orchestrator/src/finguard_orchestrator/infrastructure/visualization/vega_lite.py:53-70`
- Test: `apps/orchestrator/tests/test_visualization_stage2_core.py`

**Interfaces:**
- Produces: `Stack = Literal["zero", "normalize", "disabled"]`; `Channel` widened with `"xOffset"`, `"yOffset"`.

- [ ] **Step 1: Write the failing test**

```python
def test_a_disabled_stack_emits_an_explicit_null_and_not_an_absent_property() -> None:
    """`stack=None` omits the property, and Vega-Lite's default for a bar with a quantitative
    measure and a colour series is to **stack**. So `None` means "let Vega-Lite decide" and
    Vega-Lite decides to stack. Only an explicit null turns it off."""
    spec = ChartSpec(
        mark="bar",
        encodings=(
            ChartEncoding("y", "dimension", "nominal"),
            ChartEncoding("x", "value_minor", "quantitative"),
            ChartEncoding("color", "series", "nominal"),
            ChartEncoding("yOffset", "series", "nominal"),
        ),
        stack="disabled",
    )

    emitted = present(Visualization(spec=spec, data=()))

    assert emitted["encoding"]["x"]["stack"] is None
    assert "yOffset" in emitted["encoding"]


def test_an_absent_stack_is_still_absent() -> None:
    """The old behaviour has to stay reachable, because a temporal line needs no stack at all."""
    spec = ChartSpec(
        mark="line",
        encodings=(
            ChartEncoding("x", "dimension", "temporal"),
            ChartEncoding("y", "value_minor", "quantitative"),
        ),
    )

    emitted = present(Visualization(spec=spec, data=()))

    assert "stack" not in emitted["encoding"]["y"]


def test_an_offset_is_a_channel_the_vocabulary_knows() -> None:
    ChartEncoding("xOffset", "series", "nominal")     # must not raise
    with pytest.raises(ValueError, match="channel"):
        ChartEncoding("zOffset", "series", "nominal")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --directory apps/orchestrator pytest tests/test_visualization_stage2_core.py -k "stack or offset" -v`

Expected: FAIL — `ValueError: unsupported chart channel` for `yOffset`, and a `KeyError`/assertion for the missing explicit null.

- [ ] **Step 3: Widen the vocabulary**

`chart.py`:

```python
Channel = Literal["x", "y", "color", "tooltip", "theta", "xOffset", "yOffset"]
Stack = Literal["zero", "normalize", "disabled"]
```

Update `ChartEncoding.__post_init__`'s channel set and `ChartSpec.stack`'s annotation to `Stack | None`, and the `__post_init__` check for a stacked donut to `self.stack is not None`.

- [ ] **Step 4: Translate it in the presenter**

In `vega_lite.py`, replace `if visualization.spec.stack:` with:

```python
    if visualization.spec.stack is not None:
        quantitative = next(
            item.channel
            for item in visualization.spec.encodings
            if item.type == "quantitative" and item.channel in {"x", "y"}
        )
        # `disabled` has to be an explicit null. Omitting the property is what Vega-Lite reads as
        # "you decide", and for a bar with a colour series it decides to stack -- so the absent
        # property is the bug, not the fix.
        encoding[quantitative]["stack"] = (
            None if visualization.spec.stack == "disabled" else visualization.spec.stack
        )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run --directory apps/orchestrator pytest tests/test_visualization_stage2_core.py -k "stack or offset" -v`

Expected: PASS, 3 tests.

- [ ] **Step 6: Run both suite halves and the linters, then commit**

```bash
git add -A apps/orchestrator
git commit -m "$(cat <<'EOF'
feat(chart): an unstacked comparison needs an explicit null, not an absent property

The presenter emitted `if spec.stack:`, so `stack=None` meant *omit the property* -- and
Vega-Lite's default for a bar with a quantitative measure and a colour series is to stack. A rule
saying "a comparison must have stack=None" would therefore have been a no-op with a green test on
top: the chart would have kept stacking and the defect would have looked closed.

So `Stack` gains `disabled`, which the presenter writes as an explicit `stack: null`, and
`Channel` gains `xOffset`/`yOffset` -- because disabling the stack without an offset gives
overlapping bars rather than grouped ones. Both halves are needed and neither is sufficient.
EOF
)"
```

---

### Task 6: a sort per encoding

**Files:**
- Modify: `apps/orchestrator/src/finguard_orchestrator/domain/models/chart.py:12-26`
- Modify: `apps/orchestrator/src/finguard_orchestrator/infrastructure/visualization/vega_lite.py`
- Test: `apps/orchestrator/tests/test_visualization_stage2_core.py`

**Interfaces:**
- Produces: `EncodingSort(mode, field)`; `ChartEncoding.sort: EncodingSort | None`.

- [ ] **Step 1: Write the failing test**

```python
def test_a_chart_can_order_its_dimension_and_its_series_differently() -> None:
    """`ChartSpec` carries one `sort` and one `sort_field`, so two independent orders were not
    representable: a breakdown wants its dimension by magnitude and its legend in the order the
    producer built it. Without a per-encoding sort this design would promise two and ship one."""
    spec = ChartSpec(
        mark="bar",
        encodings=(
            ChartEncoding(
                "y", "dimension", "nominal",
                sort=EncodingSort(mode="descending", field="value_minor"),
            ),
            ChartEncoding("x", "value_minor", "quantitative"),
            ChartEncoding("color", "series", "nominal", sort=EncodingSort(mode="input")),
        ),
    )

    emitted = present(Visualization(spec=spec, data=()))

    assert emitted["encoding"]["y"]["sort"] == {"field": "value_minor", "order": "descending"}
    assert emitted["encoding"]["color"]["sort"] is None      # Vega-Lite's "keep data order"


def test_a_sort_field_needs_a_mode_that_uses_one() -> None:
    with pytest.raises(ValueError, match="input"):
        EncodingSort(mode="input", field="value_minor")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --directory apps/orchestrator pytest tests/test_visualization_stage2_core.py -k "differently or sort_field_needs" -v`

Expected: FAIL with `NameError: name 'EncodingSort' is not defined`.

- [ ] **Step 3: Write the implementation**

```python
@dataclass(frozen=True)
class EncodingSort:
    """How one channel orders itself, which is not always how the chart orders itself.

    `ChartSpec.sort` stays for the single-axis case it already serves. This exists because a
    breakdown wants its dimension by magnitude *and* its legend in producer order, and one
    setting cannot carry both.
    """

    mode: Literal["input", "ascending", "descending"]
    field: str | None = None

    def __post_init__(self) -> None:
        if self.mode not in {"input", "ascending", "descending"}:
            raise ValueError("unsupported sort mode")
        if self.mode == "input" and self.field is not None:
            raise ValueError("input order names no field to sort by")
        if self.field is not None and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", self.field):
            raise ValueError("sort fields must be identifiers")
```

Add `sort: EncodingSort | None = None` to `ChartEncoding`, and in `vega_lite.py`'s encoding loop:

```python
        if item.sort is not None:
            # Vega-Lite reads `"sort": null` as "keep the data's order", which is what `input`
            # means. A named field becomes the object form.
            encoded["sort"] = (
                None
                if item.sort.mode == "input"
                else {"field": item.sort.field, "order": item.sort.mode}
                if item.sort.field
                else item.sort.mode
            )
```

- [ ] **Step 4: Run the tests, both suite halves and the linters, then commit**

```bash
git add -A apps/orchestrator
git commit -m "$(cat <<'EOF'
feat(chart): two independent orders, because one setting could not carry both

`ChartSpec` holds a single `sort` and `sort_field`, so "the dimension by measure descending
while the series keeps the order the producer built" was not representable. A design that
promised both and shipped one would be the same shape as `stack=None` promising to unstack.

`EncodingSort` sits on the encoding. `ChartSpec.sort` stays for the single-axis case it already
serves. `input` names no field, and says so by raising, because Vega-Lite reads `"sort": null`
as "keep the data's order" -- and that order is the producer's: it builds the latest window
first, so `input` on a comparison legend gives *actual* before *anterior*. It preserves; it does
not prescribe.
EOF
)"
```

---

### Task 7: a categorical comparison is grouped, never stacked

**Files:**
- Modify: `apps/orchestrator/src/finguard_orchestrator/domain/services/deterministic_visual_planner.py`
- Test: `apps/orchestrator/tests/test_deterministic_visual_planner.py`

**Interfaces:**
- Consumes: `Stack`, offset channels (Task 5); `EncodingSort` (Task 6).

- [ ] **Step 1: Write the failing test**

```python
def test_a_categorical_comparison_groups_its_series_instead_of_stacking_them() -> None:
    """The rubric review names this defect. Stacking two periods adds figures that are not parts
    of one total: the bar's length becomes a number nobody asked for, and it renders cleanly."""
    profile = visual_profile_of(
        VisualDataset(
            metric="SUM",
            rows=(
                VisualRow(dimension="GROCERIES", value_minor=100, series="2026-07"),
                VisualRow(dimension="GROCERIES", value_minor=90, series="2026-06"),
                VisualRow(dimension="RESTAURANTS", value_minor=50, series="2026-07"),
                VisualRow(dimension="RESTAURANTS", value_minor=70, series="2026-06"),
            ),
        ),
        total_minor=None,
    )

    spec = DeterministicVisualPlanner().plan(profile, DEFAULT_PRESENTATION)

    assert spec.stack == "disabled"
    channels = {(e.channel, e.field) for e in spec.encodings}
    assert ("color", "series") in channels
    # The offset must match the dimension's axis: dimension on y means the offset is yOffset.
    dimension_channel = next(e.channel for e in spec.encodings if e.field == "dimension")
    assert (f"{dimension_channel}Offset", "series") in channels


def test_a_normalised_share_is_still_stacked() -> None:
    """RATIO_SHARE *is* parts of a verified whole, so `normalize` stays right for it. The rule is
    about comparisons, not about every chart with a colour."""
    profile = visual_profile_of(
        VisualDataset(
            metric="RATIO_SHARE",
            rows=(
                VisualRow(dimension="GROCERIES", value_minor=70),
                VisualRow(dimension="RESTAURANTS", value_minor=30),
            ),
        ),
        total_minor=100,
    )

    spec = DeterministicVisualPlanner().plan(profile, DEFAULT_PRESENTATION)

    assert spec.stack == "normalize"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --directory apps/orchestrator pytest tests/test_deterministic_visual_planner.py -k "groups_its_series or normalised" -v`

Expected: FAIL — `spec.stack` is `None` for the comparison, and there is no offset encoding.

- [ ] **Step 3: Write the implementation**

Add to `DeterministicVisualPlanner.plan`, after the `derive_chart` call:

```python
        if spec is None:
            return None
        series = profile.role("series")
        if profile.comparison and series and not profile.temporal():
            # Grouped, and both halves are required: `disabled` stops Vega-Lite's default stack,
            # and the offset is what separates the bars instead of overlapping them. The offset's
            # axis follows the dimension's, or the groups would be split along the measure.
            dimension_channel = next(
                item.channel for item in spec.encodings if item.field == dimension.name
            )
            spec = replace(
                spec,
                stack="disabled",
                encodings=spec.encodings
                + (
                    ChartEncoding("color", series[0].name, "nominal",
                                  sort=EncodingSort(mode="input")),
                    ChartEncoding(f"{dimension_channel}Offset", series[0].name, "nominal",
                                  sort=EncodingSort(mode="input")),
                ),
            )
        return spec
```

- [ ] **Step 4: Run the tests, both suite halves and the linters**

Expected: PASS. **Existing comparison tests may now assert the old stacked shape** — read each one before changing it, and if a test asserted stacking was correct, its assertion was the defect and the commit message must say which test changed and why.

- [ ] **Step 5: Commit**

```bash
git add -A apps/orchestrator
git commit -m "$(cat <<'EOF'
fix(chart): a categorical comparison is grouped, and that needs both halves

One of the two chart defects the rubric review names. Stacking two periods of the same category
adds figures that are not parts of one total, so the bar's length becomes a number nobody asked
for -- and it renders cleanly, which is why it survived.

Both halves are required. `stack="disabled"` stops Vega-Lite's default, which for a bar with a
colour series is to stack; the offset is what separates the bars rather than overlapping them.
The offset's axis follows the dimension's, or the groups would be split along the measure.

RATIO_SHARE keeps `normalize`, because it really is parts of a verified whole. The rule is about
comparisons, not about every chart that has a colour.
EOF
)"
```

---

### Task 8: a ranked dimension sorts by its measure

**Files:**
- Modify: `apps/orchestrator/src/finguard_orchestrator/domain/services/deterministic_visual_planner.py`
- Test: `apps/orchestrator/tests/test_deterministic_visual_planner.py`

- [ ] **Step 1: Write the failing test**

```python
def test_a_breakdown_sorts_its_categories_by_magnitude() -> None:
    """The second defect the review names. Ten categories in label order put the largest
    anywhere, which is the one thing a breakdown exists to show."""
    profile = visual_profile_of(
        VisualDataset(
            metric="SUM",
            rows=(
                VisualRow(dimension="ALQUILER", value_minor=51_300),
                VisualRow(dimension="GROCERIES", value_minor=24_908),
                VisualRow(dimension="ZAPATOS", value_minor=1_200),
            ),
        ),
        total_minor=None,
    )

    spec = DeterministicVisualPlanner().plan(profile, DEFAULT_PRESENTATION)

    dimension = next(e for e in spec.encodings if e.field == "dimension")
    assert dimension.sort == EncodingSort(mode="descending", field="value_minor")


def test_a_weekday_frequency_keeps_the_order_the_producer_built() -> None:
    """The counter-test, and the reason the rule reads the profile instead of the field type.
    lunes must precede martes even though martes is larger."""
    profile = visual_profile_of(
        VisualDataset(
            metric="FREQUENCY",
            rows=(
                VisualRow(dimension="lunes", value_minor=4),
                VisualRow(dimension="martes", value_minor=9),
            ),
        ),
        total_minor=None,
    )

    spec = DeterministicVisualPlanner().plan(profile, DEFAULT_PRESENTATION)

    dimension = next(e for e in spec.encodings if e.field == "dimension")
    assert dimension.sort == EncodingSort(mode="input")


def test_a_temporal_dimension_is_not_sorted_by_magnitude() -> None:
    profile = visual_profile_of(
        VisualDataset(
            metric="SUM",
            group_by="MONTH",
            rows=(
                VisualRow(dimension="2026-01", value_minor=900),
                VisualRow(dimension="2026-02", value_minor=100),
            ),
        ),
        total_minor=None,
    )

    spec = DeterministicVisualPlanner().plan(profile, DEFAULT_PRESENTATION)

    dimension = next(e for e in spec.encodings if e.field == "dimension")
    assert dimension.sort is None or dimension.sort.mode != "descending"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run --directory apps/orchestrator pytest tests/test_deterministic_visual_planner.py -k "magnitude or weekday or temporal_dimension_is_not" -v`

Expected: FAIL — the dimension encoding has no `sort`.

- [ ] **Step 3: Write the implementation**

In `plan`, before the comparison block:

```python
        # The order comes from the profile, never from the field's type. FREQUENCY is nominal and
        # deliberately `INPUT`: its dimension is weekday names in producer order, and sorting
        # those by magnitude destroys the only thing that chart is for.
        _SORTS: Final = {
            "MEASURE_DESC": EncodingSort(mode="descending", field=measure[0].name),
            "INPUT": EncodingSort(mode="input"),
            "TEMPORAL": None,
            "NONE": None,
        }
        wanted = _SORTS[dimension.order]
        if wanted is not None:
            spec = replace(
                spec,
                encodings=tuple(
                    replace(item, sort=wanted) if item.field == dimension.name else item
                    for item in spec.encodings
                ),
            )
```

Move `_SORTS` to module scope so it is built once; the local placement above is only to show
where it is read.

- [ ] **Step 4: Run the tests, both suite halves and the linters, then commit**

```bash
git add -A apps/orchestrator
git commit -m "$(cat <<'EOF'
fix(chart): a ranked dimension sorts by magnitude, and a weekday does not

The second chart defect the rubric review names. Ten categories in label order put the largest
anywhere, which is the one thing a breakdown exists to show.

The rule reads `dimension.order` from the profile and never the field's type, because "every
categorical dimension sorts by value" would have broken FREQUENCY: its dimension is nominal
weekday names with the producer's order, and lunes has to precede martes even when martes is
larger. That counter-test ships beside the fix.
EOF
)"
```

---

### Task 9: schema v6, and a compile gate over every family

**Files:**
- Modify: `apps/orchestrator/src/finguard_orchestrator/infrastructure/visualization/vega_lite.py:24`
- Create: `apps/orchestrator/scripts/export_chart_fixtures.py`
- Create: `apps/web/src/chartFamilies.test.ts`
- Create: `apps/web/src/__fixtures__/chart-families.json` (generated, committed)

**Interfaces:**
- Consumes: `present`, `DeterministicVisualPlanner`, `visual_profile_of`.
- Produces: `chart-families.json` — a list of `{name, spec, data}` covering every family.

- [ ] **Step 1: Write the failing web test**

```ts
import { describe, expect, it } from "vitest";
import { compile } from "vega-lite";
import type { TopLevelSpec } from "vega-lite";
import families from "./__fixtures__/chart-families.json";

// Two separate properties, and the separation is the point: `compile()` need not consult
// `$schema` to choose a version, so a single "it compiles" test would pass over a stale schema
// URL forever. One assertion on what we declare, one on what actually compiles.
describe("every chart family the backend emits", () => {
  it("declares the Vega-Lite version the client has installed", () => {
    for (const family of families) {
      expect(family.spec.$schema).toBe(
        "https://vega.github.io/schema/vega-lite/v6.json",
      );
    }
  });

  it("compiles under Vega-Lite 6", () => {
    for (const family of families) {
      expect(() =>
        compile({ ...family.spec, data: { values: family.data } } as TopLevelSpec),
      ).not.toThrow();
    }
  });

  it("covers every family, so a new mark cannot arrive unmeasured", () => {
    const marks = new Set(
      families.map((family) =>
        typeof family.spec.mark === "string" ? family.spec.mark : family.spec.mark.type,
      ),
    );
    expect(marks).toEqual(new Set(["bar", "line", "area", "point", "arc"]));
  });
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npm --prefix apps/web run test -- chartFamilies`

Expected: FAIL — the fixture file does not exist.

- [ ] **Step 3: Write the exporter**

```python
"""One rendered spec per chart family, for the web compile gate.

The Vega-Lite compiler only exists in JavaScript, so the honest place for "does this actually
compile" is a vitest test -- and the specs it must compile are the ones this side emits. This
script is the bridge: it renders every family and writes them where the web test reads them.

Run it whenever a family is added or the presenter changes:

    uv run python scripts/export_chart_fixtures.py
"""
```

The script builds one `VisualDataset` per family — breakdown (`bar`), temporal (`line`),
cumulative (`area`), frequency (`bar`, `INPUT` order), recurrence (`point`), ratio share
(normalised `bar`), and a categorical comparison (grouped `bar`) — runs
`visual_profile_of` → `DeterministicVisualPlanner().plan` → `present`, and writes
`[{"name": ..., "spec": ..., "data": [...]}]` to
`apps/web/src/__fixtures__/chart-families.json` with `indent=2` and a trailing newline.

**The donut is the one family the floor does not produce**, so build its `ChartSpec` directly
and say so in a comment: it is reachable only through a conversational transformation, and a
gate that skipped it would leave the one mark with its own `__post_init__` rule unmeasured.

- [ ] **Step 4: Change the schema version**

`vega_lite.py:24`: `v5.json` → `v6.json`.

- [ ] **Step 5: Generate and run**

```bash
uv run --directory apps/orchestrator python scripts/export_chart_fixtures.py
npm --prefix apps/web run test -- chartFamilies
```

Expected: PASS, 3 tests. **If a family fails to compile, that is a real finding** — record which
family and the compiler's message before changing anything, because it means the backend has been
emitting a spec Vega-Lite 6 rejects.

- [ ] **Step 6: Add a test that the fixtures are not stale**

```python
def test_the_exported_fixtures_match_what_the_presenter_emits_today() -> None:
    """A committed fixture is a snapshot, and a snapshot nobody regenerates is a gate that has
    stopped measuring. This fails the build when the presenter changes and the export does not."""
    exported = json.loads(FIXTURES.read_text(encoding="utf-8"))
    assert exported == build_chart_families()
```

- [ ] **Step 7: Run everything and commit**

```bash
git add -A apps/orchestrator apps/web
git commit -m "$(cat <<'EOF'
fix(chart): declare the Vega-Lite version the client has, and gate every family on compiling

The presenter emitted `vega-lite/v5.json` while `apps/web` has `vega-lite ^6.4.3`. One line --
but the reason it went unnoticed is the interesting half: `Conversation.test.ts` already
compiles three hand-written fixtures, and `compile()` does not have to consult `$schema` to pick
a version, so an "it compiles" test passes over a stale URL indefinitely.

So the gate is two assertions, not one: the declared version is the installed one, and every
family the backend can emit compiles under Vega-Lite 6. The families come from an exporter
rather than by hand, and a test fails the build when the presenter changes without the export
being regenerated -- a snapshot nobody refreshes is a gate that has stopped measuring, which
this repository has now found eight times.

The donut is built directly in the exporter because the floor never produces it: it is reachable
only through a conversational transformation, and it is the one mark with its own `__post_init__`
rule, so a gate that skipped it would leave exactly the riskiest family unmeasured.
EOF
)"
```

---

## Self-Review

**Spec coverage.** Section A → Task 4. Section B's inverted profile → Tasks 1-2; `PresentationContext`
→ Task 3; the privacy guarantee → Task 2's sentinel test (the *port* payload test belongs to the
planner plan, since there is no port yet). Section D's `Stack.disabled` → Task 5; per-field order →
Tasks 6 and 8; grouped comparison → Task 7; the compile gate and schema version → Task 9. Section D's
role/channel matrix, per-mark rules, type equality and the `limit` rule are **verifier** rules with no
verifier in this plan — they belong to the planner plan and are listed there as its first task.
Sections E, F and G (policy, record, measurement) are entirely the planner plan.

**Deliberately out of scope, and stated so the next plan starts from it:** the `VisualPlannerPort`,
the LLM adapter, `VisualPlanDraft`/`ProposedEncoding`/`ProposedSort`, `VisualPlanVerifier`,
`VisualPlanningPolicy` with its four modes, the seven-field record, and the evaluation. Wave 2 creates
the vocabulary those are written against, which is why they wait.

**Placeholders.** None. Every code step carries the code; Task 2 Step 2 and Task 4 Step 4 tell the
implementer to read a real definition and fix the *test* if a name differs, which is an instruction
rather than a gap.

**Type consistency.** `VisualField(name, type, role, cardinality, order)` is used identically in Tasks
1, 2, 4, 7 and 8. `visual_profile_of(dataset, *, total_minor)` is keyword-only in Task 2 and called
that way in Tasks 4, 7, 8. `DeterministicVisualPlanner.plan(profile, presentation)` takes two
positional arguments everywhere. `EncodingSort(mode, field)` is introduced in Task 6 and used in 7 and
8. `Stack` and the offset channels are introduced in Task 5 and used in 7. `VisualDecision(spec,
source, rationale, fallback_reason)` is defined in Task 4 and not touched again here.

**One risk worth naming.** Task 1 leaves the suite red on purpose, and Task 2 makes it green. If these
two are split across a review gate, the reviewer sees a red build. They are adjacent for that reason,
and Task 1's commit message says so.
