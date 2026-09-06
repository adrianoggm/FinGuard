# Visual Follow-up Context and Classification (Wave 4.1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every visual follow-up is classified into one of five kinds and recorded as the twelfth decision seam, and every chart the conversation remembers knows its decision, its plan, its evidence, the chart it derived from and the turn that made it — with no visible change to any answer.

**Architecture:** `VisualContext` (the conversation's memory of a chart) gains five fields; two of them (`derived_from`, `turn_id`) travel on the wire, three (`decision`, `source_plan`, `evidence_ids`) are turn-local because the orchestrator keeps no conversation store. A pure reader in `application/queries/readers/` classifies a turn against the charts in context using the three deterministic readers that already exist (`interpret_visual`, the contextual follow-up plan reader, and a new drill-down lexicon); the handler records the classification as `Step.VISUAL_FOLLOW_UP` and does nothing else with it. No model branch, no new setting, no new banking call.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, pytest-asyncio (`asyncio_mode = "auto"`), mypy strict on `src`, ruff line-length 100, `uv`, `just`.

**Spec:** `docs/superpowers/specs/2026-09-05-continuous-visual-conversation-design.md` — §3 (the three kinds), §4 (the context), §5 (follow-up resolution), §10 row 4.1, §13 (refusals). Wave 3's spec `docs/superpowers/specs/2026-09-03-llm-visual-planner-design.md` stays binding for everything it covers.

**Base:** `task/wave3` at `5bdb699` (Wave 3 complete: 55 commits over `develop`'s `d1b0a40`). Suite at base, recorded — do not re-measure: `tests/test_[a-l]*.py` + `tests/test_[m-s]*.py` + `tests/test_[t-z]*.py` = **3741 passed / 3 skipped / 192 deselected**, corpus **190 passed at 0.0 %**.

## Global Constraints

- **No visible change.** Task 4.1 alters no answer text, no chart, no outcome, no response field except the two new optional fields on `VisualContextPayload`. A test proves the handler's result is identical with and without a recording turn.
- **No model call anywhere** in this plan; no new setting; the turn interpreter ladder is not consulted for classification (that is 4.2+ and gated on Wave 3's measured validity).
- **No new banking call.** The classifier is pure: it reads the sentence, the `TurnAnalysis` and the `Conversation`, and returns a value.
- **The user's question never enters `VisualContext`, any DTO, `record(...)` or logs.** The classifier reads it and returns a kind and a claimant; those two words are service vocabulary.
- **No second memory** (§13): everything lives on `Conversation.visual_contexts`; no store, no cache, no module-level state.
- **No wire authority** (§13): `derived_from` and `turn_id` travel; `decision`, `source_plan`, `evidence_ids` never do — the round-trip test asserts they come back empty.
- **Result → chart, never chart → plan** (§13): `source_plan` is copied from the result, never written back.
- **No hand-copied domain literals:** `VisualFollowUpKind` and `Claimant` are `Literal`s; tests derive their parametrisations from `get_args`.
- **`domain/` imports neither `application/` nor `infrastructure/`.** The classifier lives in `application/queries/readers/` because it needs `interpret_visual` (infrastructure) and the follow-up plan reader (application); the kind type lives in `domain/models/`.
- **A `ChartSpec` field crosses three boundaries** (`present`, `conversation_mapper`, `metrics/codec`) — this plan adds none. A `VisualContextPayload` field crosses two (`to_payload` dict and `_visual_contexts`) plus the DTO — both new fields cross both.
- **Positional construction of `VisualContext(chart_id, spec, profile)` keeps working** — every existing call site is untouched by Task 2.
- Never edit `Ceiling`, `Floor`, dataset cases, corpus episodes, or Wave 3's verifier/policy/adapter.
- Atomic commits, imperative present, **no `Co-Authored-By` trailer**, no push.
- Suite gate per commit: `uv run ruff check src tests scripts`, `uv run ruff format --check src tests scripts`, `uv run mypy src`, then FOUR foreground pytest calls each with `timeout: 600000`: `tests/test_[a-l]*.py --deselect tests/test_conversation_corpus.py`, `tests/test_[m-s]*.py`, `tests/test_[t-z]*.py`, `tests/test_conversation_corpus.py`.

---

## File Structure

| file | responsibility | task |
| --- | --- | --- |
| `docs/superpowers/specs/2026-09-05-continuous-visual-conversation-design.md` | §4 amended: no store; turn-local fields; `turn_id` minted; `source_plan` type; evidence source | 1 |
| `apps/orchestrator/src/finguard_orchestrator/domain/models/chart.py` | `VisualContext` gains `decision`, `source_plan`, `evidence_ids`, `derived_from`, `turn_id` (all defaulted) + validation | 2 |
| `apps/orchestrator/src/finguard_orchestrator/infrastructure/dto/llm_dto.py` | `VisualContextPayload` gains `derived_from`, `turn_id` | 3 |
| `apps/orchestrator/src/finguard_orchestrator/infrastructure/mappers/conversation_mapper.py` | both directions carry the two wire fields; server-side fields stay off the wire | 3 |
| `apps/orchestrator/src/finguard_orchestrator/infrastructure/mappers/llm_mapper.py` | `_remember_visual_context` fills `decision`, `source_plan`, `evidence_ids`, `turn_id` | 4 |
| `apps/orchestrator/src/finguard_orchestrator/infrastructure/controllers/llm_controller.py` | the restyle path fills `derived_from`, `turn_id`, and copies `source_plan`/`evidence_ids` | 4 |
| `apps/orchestrator/src/finguard_orchestrator/domain/models/visual_follow_up.py` | `VisualFollowUpKind`, `Claimant`, `VisualFollowUp` | 5 |
| `apps/orchestrator/src/finguard_orchestrator/application/queries/readers/follow_up.py` | `_contextual_follow_up_plan` promoted to `contextual_follow_up_plan` | 5 |
| `apps/orchestrator/src/finguard_orchestrator/application/queries/readers/visual_follow_up.py` | `classify(query, analysis) -> VisualFollowUp` | 5 |
| `apps/orchestrator/src/finguard_orchestrator/observability/decisions.py` | `Step.VISUAL_FOLLOW_UP` | 6 |
| `apps/orchestrator/src/finguard_orchestrator/application/queries/ask_financial_question.py` | `_record_visual_follow_up(query, analysis)` called once after the analysis is known | 6 |
| `apps/orchestrator/tests/test_visual_context_fields.py` | Task 2 tests | 2 |
| `apps/orchestrator/tests/test_visualization_stage1.py` | round-trip carries the two wire fields and drops the three server-side ones | 3 |
| `apps/orchestrator/tests/test_visual_context_producers.py` | Task 4 tests | 4 |
| `apps/orchestrator/tests/test_visual_follow_up.py` | classifier + record rows + no-visible-change | 5, 6 |
| `apps/orchestrator/tests/test_decisions.py`, `tests/test_decisions_call_sites.py` | twelve seams | 6 |
| `docs/next.md` | §9: wave 4.1 landed, what it does not yet do | 7 |

---

### Task 1: The design says where the context really lives

**Files:**
- Modify: `docs/superpowers/specs/2026-09-05-continuous-visual-conversation-design.md` (§4)

**Interfaces:**
- Consumes: the tree — `ConversationPayload` (`infrastructure/dto/llm_dto.py:~440-475`) is the whole conversation; no conversation repository exists under `domain/ports/`.
- Produces: the sentence Tasks 2-4 argue from.

- [ ] **Step 1: Amend §4** — replace the sentence beginning "The wire payload (`VisualContextPayload`) grows **only** `derived_from` and `turn_id`; `decision`, `source_plan` and `evidence_ids` are server-side, reconstructed from the conversation store" with:

> The wire payload (`VisualContextPayload`) grows **only** `derived_from` and `turn_id`. `decision`, `source_plan` and `evidence_ids` are **turn-local**: the orchestrator keeps no conversation store — the conversation itself is the wire payload — so they are populated in the turn that makes or derives the chart and come back `None` / `()` after a round-trip. What a later turn can still recover is what the conversation already carries: `last_plan` for the newest chart's plan and the `referenced_*` reference for the last answer's evidence. The foundation's rule stands: a round-tripped profile is a narrowed reconstruction and the wire is not where authority lives. `source_plan` is typed `FinancialPlan | ComparisonPlan | RatioPlan | None` and copied from the result's `metric_definition`; `evidence_ids` are the plan's `transaction_ids` when it carries them; `turn_id` is a UUID minted where the chart is made — no turn identifier exists elsewhere in the service.

Update the code block in §4 to the fields Task 2 defines (all optional, in the order below).

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/specs/2026-09-05-continuous-visual-conversation-design.md
git commit -m "docs(visual): the context's server-side fields are turn-local, because there is no store to reconstruct them from"
```

---

### Task 2: `VisualContext` remembers more than a chart

**Files:**
- Modify: `apps/orchestrator/src/finguard_orchestrator/domain/models/chart.py:291-299`
- Test: `apps/orchestrator/tests/test_visual_context_fields.py`

**Interfaces:**
- Consumes: `VisualDecision` (`domain/services/deterministic_visual_planner.py`), `FinancialPlan` (`domain/value_objects/financial_plan.py`), `ComparisonPlan` (`domain/value_objects/comparison_plan.py`), `RatioPlan` (`domain/value_objects/ratio_plan.py`). **Import direction check:** `chart.py` is in `domain/models/`; `VisualDecision` is in `domain/services/`, which imports `chart.py` — a direct import is a cycle. Use `TYPE_CHECKING` for the annotation and a string annotation, exactly as shown; the runtime needs no import because the field is only stored.
- Produces: `VisualContext(chart_id, spec, profile, decision=None, source_plan=None, evidence_ids=(), derived_from=None, turn_id=None)`; `CHART_ID_PATTERN`.

- [ ] **Step 1: Write the failing tests**

```python
"""A VisualContext remembers how the chart came to be, not only what it is."""

from __future__ import annotations

from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from support.visual_planners import BREAKDOWN_PROFILE

from finguard_orchestrator.domain.models.chart import DEFAULT_PRESENTATION, VisualContext
from finguard_orchestrator.domain.services.deterministic_visual_planner import (
    DeterministicVisualPlanner,
    VisualDecision,
)

SPEC = DeterministicVisualPlanner().plan(BREAKDOWN_PROFILE, DEFAULT_PRESENTATION)
assert SPEC is not None


def test_the_three_positional_fields_still_build_a_context() -> None:
    context = VisualContext("chart-1", SPEC, BREAKDOWN_PROFILE)
    assert (context.decision, context.source_plan, context.evidence_ids) == (None, None, ())
    assert (context.derived_from, context.turn_id) == (None, None)


def test_a_context_carries_its_decision_evidence_and_turn() -> None:
    decision = VisualDecision(spec=SPEC, profile=BREAKDOWN_PROFILE, source="deterministic")
    turn = uuid4()
    ids = (uuid4(), uuid4())
    context = VisualContext(
        "chart-1", SPEC, BREAKDOWN_PROFILE, decision=decision, evidence_ids=ids, turn_id=turn
    )
    assert context.decision is decision
    assert context.evidence_ids == ids
    assert context.turn_id == turn


def test_derived_from_must_be_a_chart_id() -> None:
    with pytest.raises(ValueError, match="derived_from"):
        VisualContext("chart-2", SPEC, BREAKDOWN_PROFILE, derived_from="not a chart id!")
    context = VisualContext("chart-2", SPEC, BREAKDOWN_PROFILE, derived_from="chart-1")
    assert context.derived_from == "chart-1"


def test_a_context_cannot_derive_from_itself() -> None:
    with pytest.raises(ValueError, match="derived_from"):
        VisualContext("chart-3", SPEC, BREAKDOWN_PROFILE, derived_from="chart-3")


def test_evidence_ids_are_uuids() -> None:
    with pytest.raises(TypeError):
        VisualContext("chart-4", SPEC, BREAKDOWN_PROFILE, evidence_ids=("not-a-uuid",))  # type: ignore[arg-type]


def test_replace_keeps_the_new_fields() -> None:
    context = VisualContext("chart-5", SPEC, BREAKDOWN_PROFILE, derived_from="chart-1")
    assert replace(context, chart_id="chart-6").derived_from == "chart-1"
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run --directory apps/orchestrator pytest tests/test_visual_context_fields.py -q`
Expected: FAIL — `TypeError: VisualContext.__init__() got an unexpected keyword argument 'decision'`.

- [ ] **Step 3: Extend the dataclass**

In `chart.py`, replace the `VisualContext` block (lines 291-299 at the base) with:

```python
CHART_ID_PATTERN: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")


@dataclass(frozen=True)
class VisualContext:
    """A chart the conversation remembers, and how it came to be.

    The first three fields are the chart; they are positional so every existing construction
    stands. The rest is Wave 4's record of provenance. `decision`, `source_plan` and
    `evidence_ids` are turn-local — the service keeps no conversation store, so a round trip
    brings them back empty — while `derived_from` and `turn_id` travel on the wire, because a
    later turn needs them to resolve «el gráfico anterior» and to say in which turn it was made.
    """

    chart_id: str
    spec: ChartSpec
    profile: VisualProfile
    decision: "VisualDecision | None" = None
    source_plan: "FinancialPlan | ComparisonPlan | RatioPlan | None" = None
    evidence_ids: tuple[UUID, ...] = ()
    derived_from: str | None = None
    turn_id: UUID | None = None

    def __post_init__(self) -> None:
        if not CHART_ID_PATTERN.fullmatch(self.chart_id):
            raise ValueError("invalid chart id")
        if self.derived_from is not None:
            if not CHART_ID_PATTERN.fullmatch(self.derived_from):
                raise ValueError("derived_from must be a chart id")
            if self.derived_from == self.chart_id:
                raise ValueError("derived_from cannot name the chart itself")
        if any(not isinstance(item, UUID) for item in self.evidence_ids):
            raise TypeError("evidence ids are UUIDs")
```

Add at the top of `chart.py`, with the other imports: `from uuid import UUID` and

```python
if TYPE_CHECKING:  # the decision and the plans import this module; only the annotation needs them
    from finguard_orchestrator.domain.services.deterministic_visual_planner import VisualDecision
    from finguard_orchestrator.domain.value_objects.comparison_plan import ComparisonPlan
    from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan
    from finguard_orchestrator.domain.value_objects.ratio_plan import RatioPlan
```

(`TYPE_CHECKING` from `typing`; `Final` is already imported or add it.) The existing inline `re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", ...)` in `Conversation.__post_init__` (`domain/models/conversation.py:~424`) becomes `CHART_ID_PATTERN.fullmatch(value)` — one definition of the id shape.

- [ ] **Step 4: Run to verify they pass**

Run: `uv run --directory apps/orchestrator pytest tests/test_visual_context_fields.py tests/test_visualization_stage1.py tests/test_visualization_stage2_core.py -q`
Expected: PASS; then `uv run mypy src` clean (the string annotations resolve under `TYPE_CHECKING`).

- [ ] **Step 5: Commit**

```bash
git add apps/orchestrator/src/finguard_orchestrator/domain/models/chart.py apps/orchestrator/src/finguard_orchestrator/domain/models/conversation.py apps/orchestrator/tests/test_visual_context_fields.py
git commit -m "feat(visual): a remembered chart carries its decision, plan, evidence, origin and turn"
```

---

### Task 3: Two fields travel, three stay home

**Files:**
- Modify: `apps/orchestrator/src/finguard_orchestrator/infrastructure/dto/llm_dto.py:256-262` (`VisualContextPayload`)
- Modify: `apps/orchestrator/src/finguard_orchestrator/infrastructure/mappers/conversation_mapper.py:233` (`_visual_contexts`) and `:596-626` (`to_payload`'s `"visual_contexts"` list)
- Test: `apps/orchestrator/tests/test_visualization_stage1.py` (the existing round-trip tests' file)

**Interfaces:**
- Consumes: Task 2's fields; `CHART_ID_PATTERN`.
- Produces: `VisualContextPayload.derived_from: str | None`, `VisualContextPayload.turn_id: UUID | None`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_visualization_stage1.py`, next to the existing round-trip tests; reuse that file's helpers for building a conversation with a context)

```python
def test_the_wire_carries_derived_from_and_turn_id_and_nothing_server_side() -> None:
    """§13: no wire authority — decision, plan and evidence never travel; origin and turn do."""
    turn = uuid4()
    decision = VisualDecision(spec=SPEC, profile=BREAKDOWN_PROFILE, source="deterministic")
    context = VisualContext(
        "chart-2", SPEC, BREAKDOWN_PROFILE,
        decision=decision, evidence_ids=(uuid4(),), derived_from="chart-1", turn_id=turn,
    )
    conversation = IDLE.with_visual_context(context)
    payload = conversation_mapper.to_payload(conversation)
    (item,) = payload.visual_contexts
    assert (item.derived_from, item.turn_id) == ("chart-1", turn)
    assert not hasattr(item, "decision") and not hasattr(item, "source_plan")
    assert not hasattr(item, "evidence_ids")
    back = conversation_mapper.to_domain(payload)
    (restored,) = back.visual_contexts
    assert (restored.derived_from, restored.turn_id) == ("chart-1", turn)
    assert (restored.decision, restored.source_plan, restored.evidence_ids) == (None, None, ())


def test_an_absent_origin_round_trips_as_none() -> None:
    conversation = IDLE.with_visual_context(VisualContext("chart-1", SPEC, BREAKDOWN_PROFILE))
    payload = conversation_mapper.to_payload(conversation)
    (item,) = payload.visual_contexts
    assert (item.derived_from, item.turn_id) == (None, None)
    (restored,) = conversation_mapper.to_domain(payload).visual_contexts
    assert (restored.derived_from, restored.turn_id) == (None, None)


def test_an_invalid_origin_on_the_wire_is_refused() -> None:
    payload = conversation_mapper.to_payload(
        IDLE.with_visual_context(VisualContext("chart-1", SPEC, BREAKDOWN_PROFILE))
    )
    raw = payload.model_dump(mode="json")
    raw["visual_contexts"][0]["derived_from"] = "not a chart id!"
    with pytest.raises(ValidationError):
        ConversationPayload.model_validate(raw)
```

(Imports the file may lack: `uuid4`, `VisualDecision`, `ValidationError` from `pydantic`, `ConversationPayload` from `finguard_orchestrator.infrastructure.dto.llm_dto`; `IDLE` and `SPEC`/`BREAKDOWN_PROFILE` as the file already defines or imports them — check before adding.)

- [ ] **Step 2: Run to verify they fail**

Run: `uv run --directory apps/orchestrator pytest tests/test_visualization_stage1.py -q -k "derived_from or absent_origin or invalid_origin"`
Expected: FAIL — `AttributeError: 'VisualContextPayload' object has no attribute 'derived_from'`.

- [ ] **Step 3: The DTO and both mapper directions**

`llm_dto.py`, in `VisualContextPayload` after `profile`:

```python
    derived_from: str | None = Field(
        default=None, min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"
    )
    turn_id: UUID | None = None
```

(`UUID` is already imported in `llm_dto.py` — check; the pattern is the DTO's own copy of the chart-id rule because Pydantic wants a string pattern — the same string `chart_id` already uses two lines above; do not introduce a third form.)

`conversation_mapper.py:233`, the construction inside `_visual_contexts`:

```python
        result.append(
            VisualContext(
                item.chart_id, spec, profile, derived_from=item.derived_from, turn_id=item.turn_id
            )
        )
```

`conversation_mapper.py:~596`, inside the `"visual_contexts"` list item dict, after `"chart_id": context.chart_id,`:

```python
                    "derived_from": context.derived_from,
                    "turn_id": context.turn_id,
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run --directory apps/orchestrator pytest tests/test_visualization_stage1.py tests/test_visualization_stage2_core.py tests/test_conversation_round_trip.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/orchestrator/src/finguard_orchestrator/infrastructure/dto/llm_dto.py apps/orchestrator/src/finguard_orchestrator/infrastructure/mappers/conversation_mapper.py apps/orchestrator/tests/test_visualization_stage1.py
git commit -m "feat(visual): a chart's origin and turn travel on the wire; its decision, plan and evidence stay server-side"
```

---

### Task 4: The producers fill the record

**Files:**
- Modify: `apps/orchestrator/src/finguard_orchestrator/infrastructure/mappers/llm_mapper.py:313-333` (`_remember_visual_context`)
- Modify: `apps/orchestrator/src/finguard_orchestrator/infrastructure/controllers/llm_controller.py:207-223` (the restyle path)
- Test: `apps/orchestrator/tests/test_visual_context_producers.py`

**Interfaces:**
- Consumes: `AskFinancialQuestionResult.metric_definition: FinancialPlan | ComparisonPlan | RatioPlan | None`, `.visual_decision`, `.visual_dataset` (`application/queries/financial_turn.py:192-295`); `FinancialPlan.transaction_ids: tuple[UUID, ...]` (`financial_plan.py:127`).
- Produces: a fresh chart's context with `decision`, `source_plan`, `evidence_ids`, `turn_id`; a restyled chart's context with `derived_from`, a new `turn_id`, and the source's `source_plan`/`evidence_ids` copied (its `decision` stays `None` until 4.2 records a patch as a decision).

- [ ] **Step 1: Write the failing tests**

```python
"""The two places a chart is remembered fill in how it came to be."""

from __future__ import annotations

from uuid import UUID, uuid4

from support.visual_planners import BREAKDOWN_PROFILE

from finguard_orchestrator.domain.models.chart import DEFAULT_PRESENTATION, VisualContext
from finguard_orchestrator.domain.services.deterministic_visual_planner import (
    DeterministicVisualPlanner,
    VisualDecision,
)
from finguard_orchestrator.infrastructure.mappers import llm_mapper

SPEC = DeterministicVisualPlanner().plan(BREAKDOWN_PROFILE, DEFAULT_PRESENTATION)
assert SPEC is not None


def _evidence_of(plan: object) -> tuple[UUID, ...]:
    return llm_mapper._evidence_of(plan)


def test_a_fresh_chart_remembers_its_decision_plan_evidence_and_turn(
    charted_result, chart_response
) -> None:
    """`charted_result` is a fixture: an AskFinancialQuestionResult with a visual_decision and a
    FinancialPlan metric_definition whose transaction_ids are two UUIDs; `chart_response` is the
    ChartVisualizationResponse the mapper built for it. Build both the way test_visualization_stage1
    builds its real-turn fixture."""
    remembered = llm_mapper._remember_visual_context(charted_result, chart_response)
    (context,) = remembered.conversation.visual_contexts[-1:]
    assert context.decision is charted_result.visual_decision
    assert context.source_plan is charted_result.metric_definition
    assert context.evidence_ids == charted_result.metric_definition.transaction_ids
    assert isinstance(context.turn_id, UUID)
    assert context.derived_from is None


def test_two_charts_in_one_process_get_two_turn_ids(charted_result, chart_response) -> None:
    first = llm_mapper._remember_visual_context(charted_result, chart_response)
    second = llm_mapper._remember_visual_context(charted_result, chart_response)
    assert first.conversation.visual_contexts[-1].turn_id != second.conversation.visual_contexts[-1].turn_id


def test_a_plan_without_evidence_yields_no_ids() -> None:
    assert _evidence_of(object()) == ()
    assert _evidence_of(None) == ()


def test_a_restyled_chart_names_its_origin_and_keeps_its_plan(restyle_turn) -> None:
    """`restyle_turn` drives the restyle endpoint (llm_controller) with a conversation holding
    `VisualContext("chart-1", SPEC, BREAKDOWN_PROFILE, source_plan=<a FinancialPlan>, evidence_ids=(uuid4(),))`
    and the sentence «ordénalo de mayor a menor» (a phrase `interpret_visual`'s whitelist claims; «ponlo en horizontal» is not one); it returns the QuestionResponse. Build it the way
    test_visualization_stage2_core drives the same endpoint."""
    response = restyle_turn
    contexts = response.conversation.visual_contexts
    derived = contexts[-1]
    assert derived.derived_from == "chart-1"
    assert derived.turn_id is not None and derived.turn_id != contexts[0].turn_id
    assert derived.decision is None  # a patch is not a VisualDecision until 4.2 says so
```

The fixtures `charted_result`, `chart_response`, `restyle_turn` are written in this test file (not `conftest.py`), copying the construction from the two stage tests named in the docstrings — the implementer reads those files first and reuses their builders by import where they are public.

Note the restyle assertion on `derived.source_plan`/`evidence_ids`: the restyle path receives the conversation from the WIRE, where those two are absent, so after a round-trip they are `None`/`()` — assert exactly that (`derived.source_plan is None`, `derived.evidence_ids == ()`) and say why in the test's docstring: within a single turn they would be copied; the endpoint never sees a single-turn context.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run --directory apps/orchestrator pytest tests/test_visual_context_producers.py -q`
Expected: FAIL — `AttributeError: module 'llm_mapper' has no attribute '_evidence_of'` then the context assertions.

- [ ] **Step 3: Fill the two producers**

`llm_mapper.py`, replace the `VisualContext(...)` construction inside `_remember_visual_context` and add the helper above it:

```python
def _evidence_of(plan: object) -> tuple[UUID, ...]:
    """The movements behind the figures, when the plan names them. Only FinancialPlan does."""
    ids = getattr(plan, "transaction_ids", ())
    return tuple(ids) if all(isinstance(item, UUID) for item in ids) else ()
```

```python
    return replace(
        result,
        conversation=result.conversation.with_visual_context(
            VisualContext(
                visualization.chart_id,
                spec,
                decision.profile,
                decision=decision,
                source_plan=result.metric_definition,
                evidence_ids=_evidence_of(result.metric_definition),
                turn_id=uuid.uuid4(),
            )
        ),
    )
```

(`import uuid` and `from uuid import UUID` as the module needs.)

`llm_controller.py:~222`, the derived context:

```python
    derived_context = VisualContext(
        chart_id,
        updated.spec,
        trusted_context.profile,
        source_plan=trusted_context.source_plan,
        evidence_ids=trusted_context.evidence_ids,
        derived_from=trusted_context.chart_id,
        turn_id=uuid.uuid4(),
    )
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run --directory apps/orchestrator pytest tests/test_visual_context_producers.py tests/test_visualization_stage1.py tests/test_visualization_stage2_core.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/orchestrator/src/finguard_orchestrator/infrastructure/mappers/llm_mapper.py apps/orchestrator/src/finguard_orchestrator/infrastructure/controllers/llm_controller.py apps/orchestrator/tests/test_visual_context_producers.py
git commit -m "feat(visual): a fresh chart records its decision, plan, evidence and turn; a restyled one names its origin"
```

---

### Task 5: Five kinds of visual follow-up, read deterministically

**Files:**
- Create: `apps/orchestrator/src/finguard_orchestrator/domain/models/visual_follow_up.py`
- Modify: `apps/orchestrator/src/finguard_orchestrator/application/queries/readers/follow_up.py:213` (`_contextual_follow_up_plan` → `contextual_follow_up_plan`, definition and every call site in `src` and `tests`; no alias)
- Modify: `apps/orchestrator/src/finguard_orchestrator/infrastructure/visualization/interpreter.py:47` (`_fold` → `fold`, definition and every call site; no alias)
- Create: `apps/orchestrator/src/finguard_orchestrator/application/queries/readers/visual_follow_up.py`
- Test: `apps/orchestrator/tests/test_visual_follow_up.py`

**Interfaces:**
- Consumes: `interpret_visual(sentence, contexts, *, voice=False) -> VisualIntent | None` (`infrastructure/visualization/interpreter.py:55`); `contextual_follow_up_plan(query) -> FinancialPlan | None`; `TurnAnalysis(intent, relation, focus, clauses)` (`domain/models/turn_analysis.py:29`); `TurnRelation.FOLLOW_UP`, `TurnFocus.LAST_ANSWER`; `AskFinancialQuestionQuery.sentence`, `.conversation`.
- Produces:

```python
VisualFollowUpKind = Literal[
    "PRESENTATION_ONLY", "ANALYTICAL_RESHAPE", "DRILL_DOWN", "UNRELATED", "AMBIGUOUS"
]
Claimant = Literal["interpret_visual", "follow_up_plan", "drill_down_reader", "both", "none"]

@dataclass(frozen=True)
class VisualFollowUp:
    kind: VisualFollowUpKind
    claimant: Claimant

def classify(query: AskFinancialQuestionQuery, analysis: TurnAnalysis, conversation: Conversation) -> VisualFollowUp
```

Also produced: `fold` — `interpreter.py`'s `_fold` (`:47`) promoted to a public name (definition and every call site in `interpreter.py`; no alias, no copy), because the reader needs the same accent-and-case folding and a second copy would drift.

- [ ] **Step 1: Write the failing tests**

```python
"""Every visual follow-up is one of five kinds, and a deterministic reader names which."""

from __future__ import annotations

from datetime import date
from typing import get_args
from uuid import UUID

import pytest
from support.visual_planners import BREAKDOWN_PROFILE

from finguard_orchestrator.application.queries.financial_turn import AskFinancialQuestionQuery
from finguard_orchestrator.domain.enums import Direction, GroupBy, Period
from finguard_orchestrator.domain.models.chart import DEFAULT_PRESENTATION, VisualContext
from finguard_orchestrator.domain.models.conversation import IDLE, Conversation
from finguard_orchestrator.domain.value_objects.financial_plan import FinancialPlan, RelativePeriod
from finguard_orchestrator.domain.models.turn_analysis import TurnAnalysis, TurnFocus, TurnRelation
from finguard_orchestrator.domain.models.visual_follow_up import (
    Claimant,
    VisualFollowUp,
    VisualFollowUpKind,
)
from finguard_orchestrator.domain.services.deterministic_visual_planner import DeterministicVisualPlanner
from finguard_orchestrator.domain.services.intent_router import Intent
from finguard_orchestrator.application.queries.readers.visual_follow_up import classify

SPEC = DeterministicVisualPlanner().plan(BREAKDOWN_PROFILE, DEFAULT_PRESENTATION)
assert SPEC is not None
ACCOUNT = UUID("11111111-1111-1111-1111-111111111111")
TODAY = date(2026, 7, 15)
PREVIOUS = FinancialPlan(  # the breakdown the chart in context drew; the reshape reader patches THIS
    direction=Direction.EXPENSE,
    period=RelativePeriod(Period.LAST_WEEK),
    group_by=GroupBy.CATEGORY,
    only_account_id=ACCOUNT,
)
CHARTED = Conversation(last_plan=PREVIOUS).with_visual_context(
    VisualContext("chart-1", SPEC, BREAKDOWN_PROFILE)
)
FOLLOW_UP = TurnAnalysis(intent=Intent.FINANCIAL_QUERY, relation=TurnRelation.FOLLOW_UP, focus=TurnFocus.LAST_ANSWER)
NEW = TurnAnalysis(intent=Intent.FINANCIAL_QUERY, relation=TurnRelation.NEW_REQUEST, focus=TurnFocus.NEW_REQUEST)


def _query(sentence: str) -> AskFinancialQuestionQuery:
    """The conversation is passed to classify separately; the query carries the sentence."""
    return AskFinancialQuestionQuery(sentence, ACCOUNT, TODAY, conversation=CHARTED)


ROWS = [
    ("Ponlo apilado", FOLLOW_UP, "PRESENTATION_ONLY", "interpret_visual"),
    ("Ordénalo de mayor a menor", FOLLOW_UP, "PRESENTATION_ONLY", "interpret_visual"),
    ("Solo restaurantes", FOLLOW_UP, "ANALYTICAL_RESHAPE", "follow_up_plan"),  # a category patch on PREVIOUS; interpret_visual yields (financial word)
    ("¿Por qué agosto es tan alto?", FOLLOW_UP, "DRILL_DOWN", "drill_down_reader"),
    ("Enséñame los movimientos responsables", FOLLOW_UP, "DRILL_DOWN", "drill_down_reader"),
    ("¿Cuánto gasté en mayo?", NEW, "UNRELATED", "none"),
    ("Hola", NEW, "UNRELATED", "none"),
    # both a data change ("restaurantes") and a format change ("barras") in one turn: the
    # interpreter's own mixed refusal, read as AMBIGUOUS before the table above is ever reached.
    ("Ponlo en barras y solo restaurantes", FOLLOW_UP, "AMBIGUOUS", "both"),
    # An unqualified "por que" or a bare "responsable"/"explica" used to read every one of these
    # as a drill-down; none of them names anything about the chart in context.
    ("¿Por qué no me funciona la app?", FOLLOW_UP, "UNRELATED", "none"),
    ("Explica el plan", FOLLOW_UP, "UNRELATED", "none"),
    ("¿Qué compras puedo hacer con 100 euros?", FOLLOW_UP, "UNRELATED", "none"),
    ("el responsable de la nómina", FOLLOW_UP, "UNRELATED", "none"),
]


@pytest.mark.parametrize(("sentence", "analysis", "kind", "claimant"), ROWS)
def test_the_deterministic_readers_name_the_kind(sentence, analysis, kind, claimant) -> None:
    assert classify(_query(sentence), analysis, CHARTED) == VisualFollowUp(kind, claimant)


def test_without_a_chart_in_context_every_turn_is_unrelated() -> None:
    assert classify(_query("Ordenalo de mayor a menor"), FOLLOW_UP, IDLE) == VisualFollowUp("UNRELATED", "none")


def test_two_readers_claiming_is_ambiguous(monkeypatch) -> None:
    """Forced, because the two real readers rarely overlap: the test is about the table, not the regex."""
    import finguard_orchestrator.application.queries.readers.visual_follow_up as module

    monkeypatch.setattr(module, "_presentation_claims", lambda query, conversation: True)
    monkeypatch.setattr(module, "_reshape_claims", lambda query, analysis: True)
    assert classify(_query("x"), FOLLOW_UP, CHARTED) == VisualFollowUp("AMBIGUOUS", "both")


def test_every_kind_and_claimant_is_reachable() -> None:
    """Collected from ROWS plus the forced AMBIGUOUS case: the closed sets are what the reader can
    actually say, so a member nothing produces fails here."""
    kinds = {row[2] for row in ROWS} | {"AMBIGUOUS"}
    claimants = {row[3] for row in ROWS} | {"both"}
    assert kinds == set(get_args(VisualFollowUpKind))
    assert claimants == set(get_args(Claimant))


def test_the_reader_never_reads_amounts() -> None:
    """The classifier receives a query and an analysis; the profile and spec in context are value-free
    by construction, and the reader must not reach into `visual_dataset` (which is not on the query)."""
    import inspect

    import finguard_orchestrator.application.queries.readers.visual_follow_up as module

    source = inspect.getsource(module)
    assert "visual_dataset" not in source and "amount" not in source
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run --directory apps/orchestrator pytest tests/test_visual_follow_up.py -q`
Expected: FAIL — `ModuleNotFoundError: finguard_orchestrator.domain.models.visual_follow_up`.

- [ ] **Step 3: The kind, the promotion, the reader**

`domain/models/visual_follow_up.py`:

```python
"""What a turn wants from the chart it follows — five words, read before anything is changed."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

VisualFollowUpKind = Literal[
    "PRESENTATION_ONLY", "ANALYTICAL_RESHAPE", "DRILL_DOWN", "UNRELATED", "AMBIGUOUS"
]
"""§5 of the continuous-visual-conversation design. AMBIGUOUS is a kind, not a failure: it is the
row that asks instead of guessing (4.2); in 4.1 it is only recorded."""

Claimant = Literal["interpret_visual", "follow_up_plan", "drill_down_reader", "both", "none"]
"""Which deterministic reader claimed the turn. `both` is the disagreement §5 says a model may one
day settle; `none` is the ordinary turn that has nothing to do with the chart."""


@dataclass(frozen=True)
class VisualFollowUp:
    kind: VisualFollowUpKind
    claimant: Claimant
```

`readers/follow_up.py`: rename `_contextual_follow_up_plan` → `contextual_follow_up_plan` (definition at :213 and every call — `grep -rn "_contextual_follow_up_plan" src tests` must return nothing afterwards). `interpreter.py`: rename `_fold` → `fold` the same way (`grep -rn "_fold" src tests` must return nothing afterwards).

`readers/visual_follow_up.py`:

```python
"""Classify a turn against the charts in context — deterministically, changing nothing.

Three readers already exist and each recognises one kind: `interpret_visual` reads a presentation
change, `contextual_follow_up_plan` reads a reshape as a patch on the previous plan, and the
drill-down lexicon below reads a question about one part of the result. The table in `classify`
is §5's: one claimant names the kind; two claimants are AMBIGUOUS; none is UNRELATED. No model, no
banking call, no state — the result is recorded and, in 4.1, nothing else.
"""

from __future__ import annotations

import re

from finguard_orchestrator.application.queries.financial_turn import AskFinancialQuestionQuery
from finguard_orchestrator.application.queries.readers.follow_up import contextual_follow_up_plan
from finguard_orchestrator.domain.models.conversation import Conversation
from finguard_orchestrator.domain.models.turn_analysis import TurnAnalysis, TurnFocus, TurnRelation
from finguard_orchestrator.domain.models.visual_follow_up import VisualFollowUp
from finguard_orchestrator.infrastructure.visualization.interpreter import fold, interpret_visual

_DRILL_DOWN = re.compile(
    r"\b(por que|qué movimientos|que movimientos|qué comercio|que comercio|qué compras|que compras|"
    r"responsable|responsables|explica|ensename (los|las) (movimientos|compras|operaciones)|"
    r"muestrame (los|las) (movimientos|compras|operaciones))\b"
)


def _presentation_claims(query: AskFinancialQuestionQuery, conversation: Conversation) -> bool:
    return interpret_visual(query.sentence, conversation.visual_contexts) is not None


def _reshape_claims(query: AskFinancialQuestionQuery, analysis: TurnAnalysis) -> bool:
    if analysis.relation is not TurnRelation.FOLLOW_UP or analysis.focus is not TurnFocus.LAST_ANSWER:
        return False
    return contextual_follow_up_plan(query) is not None


def _drill_down_claims(query: AskFinancialQuestionQuery, analysis: TurnAnalysis) -> bool:
    if analysis.relation is not TurnRelation.FOLLOW_UP or analysis.focus is not TurnFocus.LAST_ANSWER:
        return False
    return _DRILL_DOWN.search(fold(query.sentence)) is not None


def classify(
    query: AskFinancialQuestionQuery, analysis: TurnAnalysis, conversation: Conversation
) -> VisualFollowUp:
    """`conversation` is the handler's working conversation (`was`), which may differ from
    `query.conversation` after a restoration; the charts in context are read from it."""
    if not conversation.visual_contexts:
        return VisualFollowUp("UNRELATED", "none")
    presentation = _presentation_claims(query, conversation)
    drill = _drill_down_claims(query, analysis)
    reshape = not drill and _reshape_claims(query, analysis)
    if sum((presentation, reshape, drill)) > 1:
        return VisualFollowUp("AMBIGUOUS", "both")
    if presentation:
        return VisualFollowUp("PRESENTATION_ONLY", "interpret_visual")
    if reshape:
        return VisualFollowUp("ANALYTICAL_RESHAPE", "follow_up_plan")
    if drill:
        return VisualFollowUp("DRILL_DOWN", "drill_down_reader")
    return VisualFollowUp("UNRELATED", "none")
```

Three explicit branches rather than a dict keyed by claimant name: a dict would map a plain `str`
back onto the `Literal` kind and claimant fields, which mypy strict rejects without a
`type: ignore`; three branches keep every return a literal mypy can check without one.

**Reader order note (write it as a comment):** drill-down is checked before reshape because «¿por qué agosto es tan alto?» is also a FOLLOW_UP on the last answer; the lexicon is the more specific reader and wins, which is why `reshape` is computed as `not drill and ...`.

**Not this table's problem, but adjacent — named so the next reader of this plan does not
reinvent it:** the controller's visual short-circuit answers a claimed `interpret_visual` *before*
any `TurnAnalysis` exists, so it cannot call `classify` (which requires one). It has its own entry
point instead, `classify_claimed_visual(intent: VisualIntent, sentence: str) -> VisualFollowUp` in
this same module — added by the whole-branch fix wave's commit 2 (I-1), once the review found that
the short-circuit's own hand-built two-way mapping could not see the presentation+drill pair
`classify`'s table already handles.

- [ ] **Step 4: Run to verify they pass**

Run: `uv run --directory apps/orchestrator pytest tests/test_visual_follow_up.py -q` then `uv run mypy src` then `uv run pytest -q -m "not integration" tests/test_[a-l]*.py --deselect tests/test_conversation_corpus.py` (the follow-up reader's own tests live there).
Expected: PASS; mypy clean.

- [ ] **Step 5: Commit** (two commits: the promotion first)

```bash
git add apps/orchestrator/src/finguard_orchestrator/application/queries/readers/follow_up.py apps/orchestrator/src/finguard_orchestrator/infrastructure/visualization/interpreter.py <every file the two renames touched>
git commit -m "refactor(readers): the follow-up plan reader and the sentence folder are public, because a second module reads them"
git add apps/orchestrator/src/finguard_orchestrator/domain/models/visual_follow_up.py apps/orchestrator/src/finguard_orchestrator/application/queries/readers/visual_follow_up.py apps/orchestrator/tests/test_visual_follow_up.py
git commit -m "feat(visual): five kinds of visual follow-up, named by the readers that already exist"
```

---

### Task 6: The twelfth seam records the kind and changes nothing

**Files:**
- Modify: `apps/orchestrator/src/finguard_orchestrator/observability/decisions.py` (`Step.VISUAL_FOLLOW_UP`, docstrings that count seams)
- Modify: `apps/orchestrator/src/finguard_orchestrator/application/queries/ask_financial_question.py` (`_record_visual_follow_up`, one call after `analysis` is known at `:~742`)
- Modify: `apps/orchestrator/tests/test_decisions.py` (`len(Step) == 12`; "a thirteenth should be a diff to justify"; the sweep fixture; `changed` count), `apps/orchestrator/tests/test_decisions_call_sites.py` (section 12; the `>=` set)
- Test: `apps/orchestrator/tests/test_visual_follow_up.py` (append)

**Interfaces:**
- Consumes: `record(step, verdict, *, changed, field, was=None, now=None, detail=None)`, `recording_turn()`, `TurnRecorder.sealed().where(Step)` (as `tests/test_visual_decision_seam.py` uses them); Task 5's `classify`.
- Produces: `Step.VISUAL_FOLLOW_UP = "visual_follow_up"`; one record per turn that has a chart in context: `record(Step.VISUAL_FOLLOW_UP, Verdict.AGREED, changed=False, field="visual_follow_up", now=<kind>, detail=<claimant>)`. Turns with no chart in context record nothing.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_visual_follow_up.py`)

```python
async def test_a_charted_turn_records_its_kind_and_claimant(seam_turn) -> None:
    """`seam_turn(sentence, conversation)` drives the handler under `recording_turn()` the way
    tests/test_visual_decision_seam.py does, returning the sealed TurnRecord."""
    sealed = await seam_turn("Ordenalo de mayor a menor", CHARTED)
    (decision,) = sealed.where(Step.VISUAL_FOLLOW_UP)
    assert (decision.verdict, decision.changed, decision.field) == (Verdict.AGREED, False, "visual_follow_up")
    assert (decision.was, decision.now, decision.detail) == (None, "PRESENTATION_ONLY", "interpret_visual")


async def test_a_turn_without_a_chart_records_no_visual_follow_up(seam_turn) -> None:
    sealed = await seam_turn("¿Cuánto gasté en mayo?", IDLE)
    assert sealed.where(Step.VISUAL_FOLLOW_UP) == ()


@pytest.mark.parametrize("kind", get_args(VisualFollowUpKind))
async def test_every_kind_is_a_record_row(seam_turn, monkeypatch, kind) -> None:
    import finguard_orchestrator.application.queries.ask_financial_question as handler_module

    monkeypatch.setattr(
        handler_module,
        "classify_visual_follow_up",
        lambda query, analysis, conversation: VisualFollowUp(kind, "none"),
    )
    sealed = await seam_turn("x", CHARTED)
    (decision,) = sealed.where(Step.VISUAL_FOLLOW_UP)
    assert decision.now == kind and decision.changed is False


async def test_the_classification_changes_nothing_visible(handler_turn) -> None:
    """The plan's headline constraint: the same turn, recorded and unrecorded, is the same answer."""
    recorded, _ = await turn_for("Ordenalo de mayor a menor", PREVIOUS, conversation=CHARTED)
    unrecorded = await handler_turn("Ordenalo de mayor a menor", CHARTED)
    assert recorded == unrecorded
```

(Fixtures — exact, verified at 3661e0a: `tests/test_decisions_call_sites.py` exports `turn_for(sentence, plan, *, ledger=None, **extra) -> tuple[AskFinancialQuestionResult, TurnRecord]`, which builds a handler with fakes via `handler_for(plan, ledger)`, runs one whole turn under `recording_turn()` and returns the sealed record; `**extra` is forwarded into `AskFinancialQuestionQuery(sentence=..., account_id=ACCOUNT, today=TODAY, **extra)`, so pass `conversation=CHARTED`. Import them as the seam test imports its neighbours: `from test_decisions_call_sites import ACCOUNT, TODAY, FakeLedger, handler_for, turn_for`. Define in this file:

```python
async def seam_turn(sentence: str, conversation: Conversation) -> TurnRecord:
    _, sealed = await turn_for(sentence, PREVIOUS, conversation=conversation)
    return sealed

async def handler_turn(sentence: str, conversation: Conversation) -> AskFinancialQuestionResult:
    """The same turn with NO recording turn open — `record()` is a no-op outside one."""
    handler = handler_for(PREVIOUS, FakeLedger())
    return await handler.handle(
        AskFinancialQuestionQuery(sentence=sentence, account_id=ACCOUNT, today=TODAY, conversation=conversation)
    )
```

`PREVIOUS`/`CHARTED` are Task 5's constants (import them from `tests/test_visual_follow_up.py`'s module scope or rebuild them identically). The no-visible-change test then reads `recorded, _ = await turn_for(S, PREVIOUS, conversation=CHARTED)` and `unrecorded = await handler_turn(S, CHARTED)`; `assert recorded == unrecorded`. `handler_turn` drives the handler, not the controller, so no `turn_id` is minted on either side. **`handler_turn` must drive the handler, not the controller:** Task 4 mints a fresh `turn_id` in `llm_mapper._remember_visual_context` and in the restyle path, both of which run after the handler returns, so two handler results compare equal while two controller responses would differ by that id. If the fixture ever has to go through the controller, compare with `turn_id` masked and say so in the test.)

`tests/test_decisions.py`: `11` → `12` at `:283` with the message "a thirteenth should be a diff to justify"; the sweep at `:523-606` adds a `record(Step.VISUAL_FOLLOW_UP, Verdict.AGREED, changed=False, field="visual_follow_up", now="UNRELATED", detail="none")` row so `== set(Step)` holds; `changed` count unchanged (this row does not alter the answer) — update the message "nine of the twelve altered the answer in this example". `tests/test_decisions_call_sites.py`: `# --- 12 · visual_follow_up ---` driving a handler whose conversation holds a chart, asserting `only(sealed, Step.VISUAL_FOLLOW_UP).now == "UNRELATED"` for an ordinary question, and `Step.VISUAL_FOLLOW_UP` added to the `>=` set at `:370-376` (the shared fixture may need a `visual_contexts` entry — widen it the way Task 5 of Wave 3 widened `FakeLedger`, checking every test in the file for a dependency first).

- [ ] **Step 2: Run to verify they fail**

Run: `uv run --directory apps/orchestrator pytest tests/test_visual_follow_up.py tests/test_decisions.py -q`
Expected: FAIL — `AttributeError: VISUAL_FOLLOW_UP`.

- [ ] **Step 3: The seam**

`decisions.py`: add `VISUAL_FOLLOW_UP = "visual_follow_up"` after `VISUAL_PLANNER` with a comment in the enum's style ("what a turn wants from the chart it follows — recorded before anything is changed, so the readers' agreement can be measured before a model is trusted with the disagreement"); update every sentence in the module that counts seams ("eleven" → "twelve"; "a twelfth should be a diff" → "a thirteenth").

`ask_financial_question.py`: import `classify as classify_visual_follow_up` from `readers.visual_follow_up` and `VisualFollowUp`; add next to `_record_visual`:

```python
def _record_visual_follow_up(
    query: AskFinancialQuestionQuery, analysis: TurnAnalysis, conversation: Conversation
) -> None:
    """Twelfth seam. Records what the turn wants from the chart it follows and changes nothing:
    the kind and the claimant are service vocabulary, the sentence stays in the query. `conversation`
    is the handler's working conversation (`was`), not `query.conversation`."""
    if not conversation.visual_contexts:
        return
    follow_up = classify_visual_follow_up(query, analysis, conversation)
    record(
        Step.VISUAL_FOLLOW_UP,
        Verdict.AGREED,
        changed=False,
        field="visual_follow_up",
        now=follow_up.kind,
        detail=follow_up.claimant,
    )
```

and call `_record_visual_follow_up(query, analysis, was)` immediately before `intent = analysis.intent` (`:~748` at the base — the first line after the `if query.analysis is not None / elif / else` block that produces `analysis`; `was` is the working conversation there). Nothing reads the return value.

- [ ] **Step 4: Run to verify they pass**

Run: `uv run --directory apps/orchestrator pytest tests/test_visual_follow_up.py tests/test_decisions.py tests/test_decisions_call_sites.py tests/test_visual_decision_seam.py -q`, then the full gates and the four suite calls.
Expected: PASS; totals grow by the new tests only.

- [ ] **Step 5: Commit** (two commits)

```bash
git add apps/orchestrator/src/finguard_orchestrator/observability/decisions.py apps/orchestrator/tests/test_decisions.py
git commit -m "feat(otel): a twelfth seam, visual_follow_up, because what a turn wants from a chart is decided before it is changed"
git add apps/orchestrator/src/finguard_orchestrator/application/queries/ask_financial_question.py apps/orchestrator/tests/test_visual_follow_up.py apps/orchestrator/tests/test_decisions_call_sites.py
git commit -m "feat(visual): every charted turn records its follow-up kind and changes nothing else"
```


**Controller addition (2026-09-06, from Task 5's review — the blocker's trap, guarded end to end):** add one more test to `tests/test_visual_follow_up.py`, driven through the handler like the others:

```python
async def test_a_reshape_turn_records_the_follow_up_reader_at_most_once(seam_turn) -> None:
    """The classifier consults the follow-up reader's undecorated body (Task 5 fix), so the cascade's
    own @_recogniser call is the only one that may record a claim. Two CLAIMED rows for one reader
    in one turn is the regression tests/test_reader_registry.py's invariant exists to forbid."""
    # The handler's deterministic analyser labels this turn NEW_REQUEST (probed at 11c1f54), so the
    # reshape gate never opens unless the edge's analysis is handed in, as production does:
    _, sealed = await turn_for("Solo restaurantes", PREVIOUS, conversation=CHARTED, analysis=FOLLOW_UP)
    claims = [d for d in sealed.decisions if d.step is Step.RECOGNISER and d.detail == "contextual_follow_up_plan"]
    assert len(claims) <= 1
    (kind,) = sealed.where(Step.VISUAL_FOLLOW_UP)
    assert kind.now == "ANALYTICAL_RESHAPE"
```

(If the real cascade classifies «Solo restaurantes» through a different reader first and the follow-up reader is never reached, `claims` is `[]` and the assertion still holds — the point is "never two".) Also note for the docstrings you write: the reshape reader 4.1 consults is `contextual_follow_up_plan`'s body, a deliberate substitution for §5's `continuation_patch.read()`.

---

### Controller ruling (2026-09-06): the twelfth seam also records on the controller's visual short-circuit

**Why.** `llm_controller.py` returns `_visual_only_response(...)` the moment `interpret_visual` claims — at both the question route (~l.1364) and the streaming route (~l.1512) — **without calling the handler**. A handler-only record would therefore never see a real `PRESENTATION_ONLY` turn (nor a mixed one) in production; it would record only the turns the interpreter declined. The record must be emitted where every visual turn actually passes.

**What changes in this task.**
1. Put the record's vocabulary in ONE place: a helper in `application/queries/readers/visual_follow_up.py`:

```python
def record_visual_follow_up(follow_up: VisualFollowUp) -> None:
    """One Decision on the existing record; the kind and claimant are service vocabulary."""
    record(
        Step.VISUAL_FOLLOW_UP,
        Verdict.AGREED,
        changed=False,
        field="visual_follow_up",
        now=follow_up.kind,
        detail=follow_up.claimant,
    )
```

   The handler's `_record_visual_follow_up(query, analysis, was)` classifies and then calls this helper (it no longer calls `record` itself). Importing `record`/`Step`/`Verdict` from `finguard_orchestrator.observability` into an `application/` reader module is allowed (application may import observability; check how `ask_financial_question.py` imports them and mirror it).
2. In `llm_controller.py`, at BOTH `if visual is not None:` branches, before building the response, call
   `record_visual_follow_up(VisualFollowUp("AMBIGUOUS", "both") if visual.mixed else VisualFollowUp("PRESENTATION_ONLY", "interpret_visual"))`
   (`VisualIntent.mixed` is Task 5's fix-round addition). Nothing else on those paths changes — the response is built exactly as before.
3. Tests: drive the restyle endpoint the way `tests/test_visualization_stage2_core.py` does — inside `recording_turn()` if the route does not open one itself (check `grep -rn recording_turn src` first; if the middleware opens it per request, use the app-level test client and read the sealed record the way `tests/test_decisions_call_sites.py`'s streaming test does) — and assert one `Step.VISUAL_FOLLOW_UP` row with `now == "PRESENTATION_ONLY"`, `detail == "interpret_visual"` for «Ordénalo de mayor a menor», and `now == "AMBIGUOUS"`, `detail == "both"` for «Ponlo en barras y solo restaurantes». Also assert the response itself is unchanged from a run without a recording turn (the no-visible-change constraint, now on this path too).
4. `test_decisions_call_sites.py` section 12 stays as written (the handler path). Commit 2's message may say "recorded where every visual turn passes: the handler and the controller's short-circuit".

**Resolved (controller, 2026-09-06):** the recording turn is opened per request by the OTel middleware (`observability/otel.py:481`), not by the routes. So in production the controller's visual short-circuit is inside a recording turn already; in a test that calls the route function directly (the stage2_core pattern), wrap the call in `with recording_turn() as turn:` yourself and read `turn.sealed()`. No app-level client is needed.

---

### Task 7: The roadmap says what 4.1 did and did not do

**Files:**
- Modify: `docs/next.md` (§9, after the wave-3 paragraph)

- [ ] **Step 1: One dated paragraph** (2026-09-xx, the day it lands), in §9's voice: wave 4.1 landed — the conversation's charts carry decision, plan, evidence, origin and turn (origin and turn on the wire, the rest turn-local because there is no store); every charted turn is classified into one of five kinds by the three deterministic readers and recorded as `Step.VISUAL_FOLLOW_UP`; **nothing visible changed** — no answer, chart or call differs; the corpus still cannot see charts (that is 4.2's episodes). Name what the record now lets 4.2 measure before it acts: how often the readers agree, how often nothing claims a charted turn.

- [ ] **Step 2: Commit**

```bash
git add docs/next.md
git commit -m "docs(next): wave 4.1 landed — charts remember their provenance and follow-ups are classified, nothing visible changed"
```

---

## Self-review (done while writing)

- **Spec coverage.** §4 fields: Task 2 (all five) + Task 1 (the amendment); wire rule: Task 3; §5 kinds and readers: Task 5; `Step.VISUAL_FOLLOW_UP` with `now=<kind>`, `detail=<claimant>`: Task 6; §10 row 4.1 "every follow-up kind is classified and recorded; no visible change yet": Tasks 5-6 and the no-visible-change test; §13 refusals: Global Constraints + Task 3's off-the-wire assertions. Not in 4.1 by design: asking on AMBIGUOUS (4.2), the model rung (4.2+), `source="patch"` (4.2), the drill-down plan (4.3).
- **Placeholders.** Two fixtures are described rather than written (`charted_result`/`chart_response` in Task 4, `seam_turn`/`handler_turn` in Task 6) because they copy constructions that exist in two named test files; the implementer's brief carries those file names and the pre-read at dispatch confirms the builders' current names. `_query` in Task 5 likewise. Everything else is code.
- **Type consistency.** `VisualFollowUp(kind, claimant)` positional in tests and reader; `classify(query, analysis)` everywhere; `Claimant` values match the reader's table and the tests' rows; `CHART_ID_PATTERN` used in `chart.py` and `conversation.py`; `turn_id: UUID | None` on the domain and `UUID | None` on the DTO.
- **Known risks for the pre-read at dispatch.** (1) `Intent.FINANCIAL_QUERY` — confirm the member's real name in `intent_router.py`. (2) `IDLE` — confirm it is the public idle `Conversation` constant in `domain/models/conversation.py`. (3) `interpret_visual`'s `_fold` visibility. (4) The handler's exact line where `analysis` is final. (5) Whether `tests/test_decisions_call_sites.py`'s shared fixture can hold a `VisualContext` without breaking a sibling test.
