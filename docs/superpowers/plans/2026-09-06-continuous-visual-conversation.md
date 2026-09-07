# Continuous Visual Conversation (Wave 4.2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every visual follow-up is verified before it is shown — a presentation patch goes through the same verifier as a model proposal and is recorded as a decision with `source="patch"`; an ambiguous follow-up asks instead of guessing; a reshaped chart names the chart it came from; and the conversation corpus finally sees charts, asserting the chart drawn, the banking calls made and avoided, and the chain.

**Architecture:** Three small domain additions — `draft_of(spec)` so a `ChartSpec` can re-enter the verifier, `VisualSource` with `patch`, and a `Claimant` that names the pair of readers — let the existing controller restyle path verify and record what it already merges; the handler carries its classification onto the result so the mapper can link `derived_from` on a reshape; the controller's short-circuit turns an `AMBIGUOUS` classification into §5's question. The corpus harness gains four wire-visible per-turn assertions and one episode. No model rung (the setting stays `deterministic`; Task 9 has not run), no store, no new banking call.

**Tech Stack:** Python 3.12, FastAPI, Pydantic v2, pytest-asyncio (`asyncio_mode = "auto"`), mypy strict on `src`, ruff line-length 100, `uv`, PyYAML (episodes).

**Spec:** `docs/superpowers/specs/2026-09-05-continuous-visual-conversation-design.md` — §3.1 ("What Wave 4 adds": the patch through `VisualPlanVerifier`, recorded with `source="patch"`), §3.2 (the `derived_from` link), §5 (AMBIGUOUS asks; recorded detail), §9 (episodes; invariants), §10 row 4.2, §13. Wave 3's spec stays binding. §7 (explanation) is 4.5's and is NOT touched here; the controller's ruling on §7 (derive from what survives; a descriptive decision summary may travel) is in the 4.1 ledger.

**Base:** `task/wave3` at `0f35640` (Wave 4.1 complete, fix wave included; the SDD ledger confirms the SHA at setup — do not re-measure). Suite at base: `tests/test_[a-l]*.py` + `tests/test_[m-s]*.py` + `tests/test_[t-z]*.py` = **3793 passed / 3 skipped / 192 deselected**, corpus **190 passed at 0.0 %**.

## Global Constraints

- **A refused patch never merges.** `merge_encoding_patch` + `verify(draft_of(spec), profile)` decide; a `Rejection` leaves the previous chart in place and the response names the rule (`VISUAL_INCOMPATIBLE`); nothing "repairs" a patch.
- **Asking is the only new visible behaviour on a turn the verifier accepts, and only on `AMBIGUOUS`; verification is a second, deliberate visible change.** Every other accepted turn answers as at the base; the 4.1 no-visible-change tests stay green except where a test asserts the old silent restyle of an ambiguous sentence — such a test changes to expect the question, and says so. **(I1, fix wave.)** The gate itself changes what a restyle does: probed across the interpreter's whitelist × the eight floor families, 29 of the 62 combinations base accepted are now refused (mark, order or grouped), a 47% reduction — each is a chart the verifier's rule exists to refuse (a line over unordered categories, a value sort on a temporal axis, a stack on a comparison), never a false refusal. The refusal speaks one Spanish clause per rule (`llm_controller._REFUSAL_TEXT`, keyed by `RejectionRule`), and the chips the answer offers are filtered through the same gate (C1) so none of them promises a restyle the gate would then refuse.
- **`VISUAL_UNCHANGED` is a third, deliberate visible change (fix wave 2, F2).** A verified restyle whose merged spec is canonical-equal to the chart already shown answers `VISUAL_UNCHANGED`, "El gráfico ya está así; lo dejo como está.", and mints no version — no new `chart_id`, no derived context, no `patch` decision row; the `VISUAL_FOLLOW_UP` row recorded upstream is the turn's whole audit trail. The same equality drops a chip from `_chart_suggestions` whenever applying it would answer this way.
- **No model call anywhere; no new setting; the setting stays `deterministic`.** §5's model rung waits for Task 9's numbers.
- **No new banking call.** Presentation-only and ambiguous turns make zero ledger calls (asserted on the fake's `ran`).
- **Result → chart, never chart → plan.** Nothing in this plan writes a plan or filters from a chart.
- **No second memory; no wire authority.** `VisualContextPayload` gains no field. `derived_from` is computed server-side from the conversation the request carried.
- **Closed sets are `Literal`s read through `get_args`** (`VisualSource`, `VisualFollowUpKind`, `Claimant`); the hand-typed `{"llm", "deterministic"}` in `VisualDecision.__post_init__` becomes `get_args(VisualSource)`.
- **`domain/` imports neither `application/` nor `infrastructure/`.** The interpreter lives in `domain/services/visual_interpreter.py` (4.1's fix wave).
- **Anything consulting a reader outside the cascade calls its undecorated `read_*` body** (4.1's convention).
- **Never edit `Ceiling`, `Floor`, dataset cases or existing corpus episodes**; this plan ADDS one episode file.
- Atomic commits, imperative present, **no `Co-Authored-By`**, no push.
- Suite gate per commit: `uv run ruff check src tests`, `uv run ruff format --check src tests`, `uv run mypy src`, then FOUR foreground pytest calls each with `timeout: 600000`: `tests/test_[a-l]*.py --deselect tests/test_conversation_corpus.py`, `tests/test_[m-s]*.py`, `tests/test_[t-z]*.py`, `tests/test_conversation_corpus.py`.

---

## File Structure

| file | responsibility | task |
| --- | --- | --- |
| `domain/models/visual_plan.py` | `VisualSource`; `draft_of(spec) -> VisualPlanDraft`; `Claimant` pairs live in `visual_follow_up.py` | 1, 2, 5 |
| `domain/services/deterministic_visual_planner.py` | `VisualDecision.source: VisualSource`; invariants for `patch` | 2 |
| `domain/models/visual_follow_up.py` | `Claimant` names the pair (`presentation+reshape`, `presentation+drill`, `reshape+drill`) | 5 |
| `application/queries/readers/visual_follow_up.py` | `classify` / `classify_claimed_visual` return the pair | 5 |
| `application/queries/financial_turn.py` | `AskFinancialQuestionResult.visual_follow_up: VisualFollowUp \| None` | 4 |
| `application/queries/ask_financial_question.py` | `_record_visual_follow_up` returns the classification; the handler sets it on the result; `_record_visual` promoted to `record_visual_decision` | 3, 4 |
| `infrastructure/controllers/llm_controller.py` | restyle path verifies the patched spec, records a `patch` decision, refuses with the rule; `AMBIGUOUS` asks; saved-metric branch keeps provenance (D-4.1-1) | 3, 6 |
| `infrastructure/mappers/llm_mapper.py` | `_remember_visual_context` sets `derived_from` on a reshape | 4 |
| `tests/support/episodes.py` | `Turn.chart`, `Turn.banking_calls`, `Turn.derived_from`, `Turn.groups`; the `Ledger` fake honours `groups`; complaints | 7 |
| `evaluation/conversations/32-visual-continuation.yaml` | the base episode's turns 1, 2, 3, 6 | 7 |
| `docs/next.md`, the design (§3.1, §3.2, §5 marked landed) | the record on paper | 8 |
| tests: `tests/test_visual_plan.py`, `tests/test_deterministic_visual_planner.py`, `tests/test_visualization_stage2_core.py`, `tests/test_visual_follow_up.py`, `tests/test_visual_context_producers.py`, `tests/test_conversation_corpus.py` | | 1–7 |

---

### Task 1: A spec can re-enter the verifier

**Files:**
- Modify: `apps/orchestrator/src/finguard_orchestrator/domain/models/visual_plan.py`
- Test: `apps/orchestrator/tests/test_visual_plan.py`

**Interfaces:**
- Consumes: `ChartSpec`, `ChartEncoding`, `EncodingSort` (`domain/models/chart.py`); `VisualPlanDraft`, `ProposedEncoding`, `ProposedSort` (this module); `verify(draft, profile)` (`domain/services/visual_plan_verifier.py`); `canonical(spec)`.
- Produces: `def draft_of(spec: ChartSpec) -> VisualPlanDraft` — the plain-string draft that names exactly what the spec names: mark, every encoding as `(channel, field, type, sort)`, chart-wide `sort`/`sort_field`, `stack`, `limit`; the title is dropped (a title is not a plan).

- [ ] **Step 1: Write the failing tests** (append to `tests/test_visual_plan.py`)

```python
from support.visual_planners import BREAKDOWN_PROFILE

from finguard_orchestrator.domain.models.chart import DEFAULT_PRESENTATION
from finguard_orchestrator.domain.models.visual_plan import Rejection, canonical, draft_of
from finguard_orchestrator.domain.services.deterministic_visual_planner import DeterministicVisualPlanner
from finguard_orchestrator.domain.services.visual_plan_verifier import verify


def test_a_spec_re_enters_the_verifier_and_comes_out_the_same() -> None:
    """draft_of is the inverse the verifier already has: verify(draft_of(spec)) is spec, canonically."""
    spec = DeterministicVisualPlanner().plan(BREAKDOWN_PROFILE, DEFAULT_PRESENTATION)
    assert spec is not None
    verified = verify(draft_of(spec), BREAKDOWN_PROFILE)
    assert not isinstance(verified, Rejection), verified
    assert canonical(verified) == canonical(spec)


def test_the_draft_names_every_encoding_and_drops_the_title() -> None:
    spec = DeterministicVisualPlanner().plan(BREAKDOWN_PROFILE, DEFAULT_PRESENTATION)
    assert spec is not None
    draft = draft_of(spec)
    assert draft.mark == spec.mark
    assert [(e.channel, e.field, e.type) for e in draft.encodings] == [
        (e.channel, e.field, e.type) for e in spec.encodings
    ]
    assert (draft.sort, draft.sort_field, draft.stack, draft.limit) == (
        spec.sort, spec.sort_field, spec.stack, spec.limit
    )
    assert draft.rationale is None
```

Also parametrise the first test over every floor family the verifier's own tests already enumerate (`tests/test_visual_plan_verifier.py` builds them as `_FLOOR_FAMILIES`; import or copy the builders — the round trip must hold for all eight).

- [ ] **Step 2: Run to verify they fail**

Run: `uv run --directory apps/orchestrator pytest tests/test_visual_plan.py -q`
Expected: FAIL — `ImportError: cannot import name 'draft_of'`.

- [ ] **Step 3: Implement**

In `visual_plan.py`, after `canonical`:

```python
def draft_of(spec: ChartSpec) -> VisualPlanDraft:
    """The plain-string draft that names exactly what a spec names — so a spec the code built (a
    merged patch, a saved metric's chart) can be judged by the same verifier a model's proposal is.
    The title is dropped: a title is presentation, not a plan, and the verifier has no rule for it."""
    return VisualPlanDraft(
        mark=spec.mark,
        encodings=tuple(
            ProposedEncoding(
                channel=item.channel,
                field=item.field,
                type=item.type,
                sort=None if item.sort is None else ProposedSort(item.sort.mode, item.sort.field),
            )
            for item in spec.encodings
        ),
        sort=spec.sort,
        sort_field=spec.sort_field,
        stack=spec.stack,
        limit=spec.limit,
    )
```

Check `EncodingSort`'s field names (`mode`, `field`) and `ChartEncoding.sort`'s type in `chart.py` before writing; if the names differ, use the tree's.

- [ ] **Step 4: Run to verify they pass** — `uv run --directory apps/orchestrator pytest tests/test_visual_plan.py tests/test_visual_plan_verifier.py -q`; `uv run mypy src`.

- [ ] **Step 5: Commit**

```bash
git add apps/orchestrator/src/finguard_orchestrator/domain/models/visual_plan.py apps/orchestrator/tests/test_visual_plan.py
git commit -m "feat(visual): a chart the code built can re-enter the verifier as the draft it names"
```

---

### Task 2: A patch is a source of chart decisions

**Files:**
- Modify: `apps/orchestrator/src/finguard_orchestrator/domain/models/visual_plan.py` (`VisualSource`)
- Modify: `apps/orchestrator/src/finguard_orchestrator/domain/services/deterministic_visual_planner.py` (`VisualDecision`)
- Test: `apps/orchestrator/tests/test_deterministic_visual_planner.py`

**Interfaces:**
- Produces: `VisualSource = Literal["llm", "deterministic", "patch"]`; `VisualDecision.source: VisualSource`; invariants: `source in get_args(VisualSource)`; `fallback_reason` only with `deterministic`; `accepted == (source in {"llm", "patch"})`; a `patch` decision has `proposed=False`, `valid=True`, `latency_ms=None`, `proposed_spec=None`.

**Landed** (fix wave, M1/D-4.2-11): `VisualDecision.__post_init__` enforces exactly three
invariants — `source`, `fallback_reason` and `accepted` — and only those; `proposed=False`,
`valid=True`, `latency_ms=None` and `proposed_spec=None` on a `patch` decision are conventions of
the constructor sites that build one (`_visual_only_response`), never enforced by the dataclass
itself. Probed: all four are accepted when violated. This bullet's own wording over-described what
landed; this note is the correction, not a code change.

- [ ] **Step 1: Failing tests** (beside `test_accepted_means_the_model_chart_is_shown`)

```python
def test_a_patch_is_a_source_and_is_what_is_shown() -> None:
    decision = VisualDecision(spec=SPEC, profile=PROFILE, source="patch", accepted=True, valid=True)
    assert decision.source == "patch" and decision.accepted and decision.valid
    with pytest.raises(ValueError, match="accepted"):
        VisualDecision(spec=SPEC, profile=PROFILE, source="patch", accepted=False)
    with pytest.raises(ValueError, match="fall back"):
        VisualDecision(spec=SPEC, profile=PROFILE, source="patch", accepted=True, fallback_reason="timeout")


def test_the_source_set_is_the_literal() -> None:
    from typing import get_args
    from finguard_orchestrator.domain.models.visual_plan import VisualSource
    assert set(get_args(VisualSource)) == {"llm", "deterministic", "patch"}
    with pytest.raises(ValueError, match="source"):
        VisualDecision(spec=SPEC, profile=PROFILE, source="wizard")  # type: ignore[arg-type]
```

- [ ] **Step 2: Run** — FAIL (`"patch"` refused by the hand-typed set).
- [ ] **Step 3: Implement** — `VisualSource` in `visual_plan.py`; `source: VisualSource`; `__post_init__`: `if self.source not in get_args(VisualSource): raise ValueError("unsupported visual decision source")`; `if self.fallback_reason is not None and self.source != "deterministic": raise ValueError("only a deterministic chart fell back")` (keep the old message's sense — check the existing test's `match`); `if self.accepted != (self.source in {"llm", "patch"}): raise ValueError("accepted means the model's or the patch's chart is what is shown")` — update the existing invariant test's `match` if the message changes. Every existing `VisualDecision(...)` construction keeps working (defaults unchanged).
- [ ] **Step 4: Run** `tests/test_deterministic_visual_planner.py tests/test_visual_planning_policy.py tests/test_visual_decision_seam.py`.
- [ ] **Step 5: Commit** — `feat(visual): a patch is a third source of chart decisions, and the source set is the literal`.

---

### Task 3: The restyle path verifies, records and refuses with a reason

**Files:**
- Modify: `apps/orchestrator/src/finguard_orchestrator/infrastructure/controllers/llm_controller.py` (the restyle branch: `apply_visual_intent` → verify → record → respond; `_derived_context` gains `decision`; the saved-metric rebuild keeps provenance — D-4.1-1)
- Modify: `apps/orchestrator/src/finguard_orchestrator/application/queries/ask_financial_question.py` (`_record_visual` → public `record_visual_decision`; the handler's own call renamed)
- Test: `apps/orchestrator/tests/test_visualization_stage2_core.py` (the restyle endpoint's file), `apps/orchestrator/tests/test_visual_context_producers.py`

**Interfaces:**
- Consumes: `draft_of`, `verify`, `Rejection`, `VisualDecision(source="patch")`, `record_visual_decision(decision)`, `recording_turn`.
- Produces: on a verified patch — `VISUAL_UPDATED` as today, the derived context's `decision` set, one `Step.VISUAL_PLANNER` record (`Verdict.AGREED`, `changed=False`, `field="chart"`, `was=<the source context's decision source or "deterministic">`, `now="patch"`, `detail="accepted"`); on a `Rejection` — `VISUAL_INCOMPATIBLE` with the rule named in the text, the previous chart untouched, and one `Step.VISUAL_PLANNER` record with `Verdict.DECLINED`, `changed=False`, `now=<was>`, `detail=f"invalid_proposal:{rejection.rule}"`.

- [ ] **Step 1: Failing tests** (in `test_visualization_stage2_core.py`, following its restyle-driving pattern under `recording_turn()`)

```python
async def test_a_patch_that_the_verifier_refuses_keeps_the_previous_chart_and_names_the_rule(...) -> None:
    """A donut asked of a breakdown whose total is not verified: merge_encoding_patch may accept the
    shape, the verifier refuses the claim (rule donut_claims). The previous chart stays; the response
    says why; one VISUAL_PLANNER record says DECLINED with the rule."""
    # drive «ponlo en donut» (or the interpreter's donut phrase) over a BREAKDOWN context whose
    # profile has verified_total=False
    ...
    assert response.outcome == "VISUAL_INCOMPATIBLE"
    assert "donut_claims" in response.text or "total" in response.text.lower()
    assert response.conversation.visual_contexts[-1].chart_id == "chart-1"   # nothing derived
    (decision,) = turn.sealed().where(Step.VISUAL_PLANNER)
    assert decision.verdict is Verdict.DECLINED and decision.detail == "invalid_proposal:donut_claims"


async def test_a_verified_patch_is_recorded_as_a_patch_decision(...) -> None:
    # «ordénalo de mayor a menor» over a breakdown bar: verifies; VISUAL_UPDATED as before
    ...
    assert response.outcome == "VISUAL_UPDATED"
    (decision,) = turn.sealed().where(Step.VISUAL_PLANNER)
    assert (decision.verdict, decision.now, decision.detail) == (Verdict.AGREED, "patch", "accepted")
    derived = conversation_mapper.to_domain(response.conversation).visual_contexts[-1]
    assert derived.derived_from == "chart-1"
    # decision is turn-local: after the wire it is None — assert the in-process helper instead:
    ctx = _derived_context(trusted, updated_spec, "chart-2", decision=VisualDecision(...source="patch"...))
    assert ctx.decision is not None and ctx.decision.source == "patch"


def test_the_saved_metric_source_keeps_the_original_provenance() -> None:
    """D-4.1-1: the rebuild is replace(context, spec=source.chart), not a three-positional rebuild."""
    ...
```

- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implement.** In the restyle branch, after `updated = apply_visual_intent(intent, trusted_context)`:

```python
    verified = verify(draft_of(updated.spec), trusted_context.profile)
    if isinstance(verified, Rejection):
        record_visual_decision_refused(trusted_context, verified)   # one DECLINED row, see below
        return QuestionResponse(
            outcome="VISUAL_INCOMPATIBLE",
            text=f"No puedo aplicar ese cambio a este gráfico ({verified.reason}); el anterior se mantiene.",
            conversation=conversation_mapper.to_payload(conversation),
        )
    decision = VisualDecision(spec=verified, profile=trusted_context.profile, source="patch", accepted=True, valid=True)
    record_visual_decision(decision, was=trusted_context.decision.source if trusted_context.decision else "deterministic")
    chart_id = "chart-" + uuid.uuid4().hex[:16]
    derived_context = _derived_context(trusted_context, verified, chart_id, decision=decision)
```

`record_visual_decision` is `_record_visual` made public in the handler module with a `was: str` keyword (default `"deterministic"`) so the restyle row can say what it replaced; `record_visual_decision_refused(context, rejection)` records `Verdict.DECLINED, changed=False, field="chart", now=<was>, detail=f"invalid_proposal:{rejection.rule}"` — put both beside `_record_visual`'s current home and import them in the controller (infrastructure may import application). The saved-metric rebuild becomes `trusted_context = replace(context, spec=source.chart) if ... else context`. `_derived_context(trusted, spec, chart_id, *, decision=None)` passes `decision=` through. Every message the user sees stays Spanish and in the controller's voice.

- [ ] **Step 4: Run** the two test files + `tests/test_visual_decision_seam.py` + `tests/test_decisions.py` (the record table grows two rows — if `test_decisions.py` pins the VISUAL_PLANNER rows' shapes, extend, never weaken).
- [ ] **Step 5: Commit** — `feat(visual): a restyle goes through the verifier — accepted as a patch decision, or refused with the rule and the previous chart kept`.

**Landed** (`51f8d54`, fix rounds `77a858c`/`c14ffe6`/`49a1460`): `verify(draft_of(updated.spec), profile)` is a **gate**, not a rebuild — the decision names, the derived context stores, the presenter shows and the save candidate keeps `updated.spec` itself (the verified spec is the merged spec minus its titles, used only to pass the gate). The refusal text is one Spanish sentence naming the rule as a token, no English reason: «No puedo aplicar ese cambio a este gráfico: no pasa la regla «{rule}» del verificador; el anterior se mantiene.» — not the sketch's `({verified.reason})` interpolation above. The patch row records `changed = (was != now)` (which is always `True`, since `now` is always `"patch"`), not `changed=False`; a `patch` decision returns before `record_visual_decision`'s `logger.info` call, so it never emits the `visual_planner.decision` promotion-log line Task 9 reads.

**Task 3b (landed as fix round 1, `77a858c`):** the wire's `VisualFieldPayload` carries `role`/`order` (typed by the domain's own `FieldRole`/`FieldOrder` literals) and `VisualProfilePayload` carries `comparison`/`plan_limited`, with the old derivation (from `type` alone) kept for a conversation an older client still holds; `row_count`/`plan_metric` stay off the wire; a contradictory wire `role`/`type` is refused at the DTO with a 422, never falls through to an unhandled 500. **(I4, fix wave.)** That old derivation validates the payload; it does not restore what base itself could do. `order` falls back to `NONE`, and the verifier's own `order` rule reads `NONE` as "nothing may sort this field" — so a sort restyle asked of an old-shape chart is refused exactly as at base, before this task existed, until the conversation's next chart is built (every new one carries the full profile). Recovering `order` from the spec's own axis sort was judged not worth the code for a window one turn wide.

---

### Task 4: A reshaped chart names the chart it came from

**Files:**
- Modify: `apps/orchestrator/src/finguard_orchestrator/application/queries/financial_turn.py` (`AskFinancialQuestionResult.visual_follow_up: VisualFollowUp | None = None`)
- Modify: `apps/orchestrator/src/finguard_orchestrator/application/queries/ask_financial_question.py` (`_record_visual_follow_up` returns the `VisualFollowUp | None`; the handler puts it on the result it yields)
- Modify: `apps/orchestrator/src/finguard_orchestrator/infrastructure/mappers/llm_mapper.py` (`_remember_visual_context`: `derived_from = previous.chart_id if result.visual_follow_up and result.visual_follow_up.kind == "ANALYTICAL_RESHAPE" and previous is not None else None`, where `previous = result.conversation.visual_contexts[-1] if result.conversation.visual_contexts else None` — the newest chart BEFORE this turn's is appended)
- Test: `apps/orchestrator/tests/test_visual_context_producers.py`, `tests/test_visual_follow_up.py`

**Interfaces:** the result field is service vocabulary (kind + claimant), never the sentence; the mapper reads it and nothing else new.

- [ ] Tests: a reshape turn (classified `ANALYTICAL_RESHAPE`) that produces a new chart yields a context whose `derived_from` is the previous chart's id; an `UNRELATED` new chart yields `None`; the result's `visual_follow_up` is set by the handler when a chart was in context and `None` otherwise (through `turn_for`, with `analysis=FOLLOW_UP` injected as 4.1's seam tests do).
- [ ] Implement; run `tests/test_visual_context_producers.py tests/test_visual_follow_up.py tests/test_visualization_stage1.py`; the no-visible-change tests must stay green (the result gained a field with default `None`; the comparison is equality of two runs, both carrying it).
- [ ] Commit — `feat(visual): a reshaped chart names the chart it came from`.

**Landed** (`58c7df2`, fix `010d0f0`): the classification travels from `_steps` (an async generator) to `stream()`'s `_decided_visual` call through a one-element list threaded as an explicit out-parameter (`stream → _steps_answering_scope → _steps`) — the reviewer judged it the least-bad mechanism for a value born inside a generator and needed after it. The two early returns that draw a chart of their own before ending the turn — retry control, previous-plan restore — never reach `_record_visual_follow_up`, so both draw a chart whose `derived_from` is `None` because no analysis exists there; stated in `_record_visual_follow_up`'s own docstring, not hidden.

---

### Task 5: The claimant names the pair

**Files:**
- Modify: `apps/orchestrator/src/finguard_orchestrator/domain/models/visual_follow_up.py` (`Claimant = Literal["interpret_visual", "follow_up_plan", "drill_down_reader", "presentation+reshape", "presentation+drill", "reshape+drill", "none"]` — `both` removed)
- Modify: `apps/orchestrator/src/finguard_orchestrator/application/queries/readers/visual_follow_up.py` (`classify` builds the pair from the two claimants; `classify_claimed_visual` returns `presentation+drill` for the lexicon case and `presentation+reshape` for the interpreter's mixed refusal)
- Test: `apps/orchestrator/tests/test_visual_follow_up.py` (every `"both"` expectation becomes the pair; the reachability test derives from `get_args(Claimant)`)

- [ ] Tests first (the mixed row → `presentation+reshape`; the monkeypatched two-claimant test → the pair it forces; the three I-1 sentences → `presentation+drill`); run; implement; commit — `feat(visual): an ambiguous follow-up says which two readers claimed it`.

**Landed** (`a74a3cd`, fix `259b74d`): `reshape+drill` is the vocabulary's third pair and is unreachable today — `classify`'s own drill-before-reshape precedence (`reshape = not drill and ...`) makes drill and reshape mutually exclusive, so nothing here or in production ever produces it. `test_every_kind_and_claimant_is_reachable` (`tests/test_visual_follow_up.py`) asserts the *difference* `set(get_args(Claimant)) - claimants == {"reshape+drill"}`, not a union back into `get_args(Claimant)`, so a new pair added without a reader to produce it fails this test instead of passing silently.

---

### Task 6: An ambiguous follow-up asks

**Files:**
- Modify: `apps/orchestrator/src/finguard_orchestrator/infrastructure/controllers/llm_controller.py` (both short-circuit branches: after `record_visual_follow_up(follow_up)`, `if follow_up.kind == "AMBIGUOUS": return QuestionResponse(outcome="VISUAL_AMBIGUOUS", text=_ASK[follow_up.claimant], conversation=conversation_mapper.to_payload(was))` — the conversation is returned unchanged; nothing is merged; no ledger call)
- Test: `apps/orchestrator/tests/test_visual_follow_up.py` (controller path), `tests/test_visualization_stage2_core.py`

**Interfaces:** `_ASK: dict[Claimant, str]` with two texts, §5's for `presentation+reshape` («¿Quieres cambiar sólo la visualización, o volver a calcular los datos?») and a drill-down variant for `presentation+drill` («¿Quieres cambiar sólo la visualización, o ver los movimientos detrás de esa parte del gráfico?»); the outcome `VISUAL_AMBIGUOUS` needs no enumeration change (checked at `83774a2`): `TurnPayload.outcome` is a `str` with pattern `^[A-Z][A-Z0-9_]*$` (`llm_dto.py:236`), `QuestionResponse.outcome` is a plain `str` (`:813`), and `apps/web/src` has no `VISUAL_*` switch — the client renders `text`. Add the new outcome to whatever list `tests/test_decisions_call_sites.py` or the OTel exporter maps outcomes to verdicts (`_OUTCOME_VERDICTS` or similar in the handler — grep `VISUAL_UPDATED` in `src` to find every enumeration of the visual outcomes and extend each).

- [ ] Tests: «muéstrame los movimientos ordenados de mayor a menor» over a charted conversation → `VISUAL_AMBIGUOUS`, the drill text, zero ledger calls, conversation unchanged, one `VISUAL_FOLLOW_UP` row `AMBIGUOUS / presentation+drill`; the interpreter's mixed sentence → `VISUAL_AMBIGUOUS` with §5's text (the interpreter's own explanation is no longer returned); «Ordénalo de mayor a menor» → `VISUAL_UPDATED` as before. Update 4.1's no-visible-change controller test if it used an ambiguous sentence (it used «Ordénalo de mayor a menor» — unchanged).
- [ ] Implement; run; commit — `feat(visual): an ambiguous visual follow-up asks which of the two things the holder meant`.

**Landed** (`d85da69`): `_ASK` has exactly the two keys `classify_claimed_visual` can return — `presentation+reshape` and `presentation+drill`, not a third `reshape+drill` entry (Task 5's landed note); `test_every_ambiguous_claimant_classify_claimed_visual_can_return_has_a_text` checks that against the function's own outputs, not against this dict's keys, so an unanswerable claimant fails a test instead of a `KeyError` in production. `TurnPayload.outcome` and `QuestionResponse.outcome` stayed the plain, unconstrained `str` the pre-read risk flagged them as, so `VISUAL_AMBIGUOUS` needed no enumeration change anywhere.

---

### Task 7: The corpus sees charts

**Files:**
- Modify: `apps/orchestrator/tests/support/episodes.py` (`Turn.chart: dict | None = None`, `Turn.banking_calls: int | None = None`, `Turn.derived_from: bool | None = None`, `Turn.groups: list | None = None`; the `Ledger` fake answers with the turn's `groups` when given — thread the current turn's groups to the fake before posting; `_complaints` checks `chart` against the canonical form of `answer["visualization"]["spec"]` (mark, channels/fields/types, stack, limit, sort/sort_field — build the expected canonical from the YAML mapping), `banking_calls` against `len(ran)`, `derived_from` against `answer["conversation"]["visual_contexts"][-1]["derived_from"] is not None`)
- Create: `apps/orchestrator/evaluation/conversations/32-visual-continuation.yaml`
- Test: `apps/orchestrator/tests/test_conversation_corpus.py` (the new keys can be asserted and a wrong expectation is caught — mirror `test_the_query_a_turn_ran_can_be_asserted` / `test_a_wrong_expectation_about_the_query_is_caught`)

**The episode** (§9's base episode, turns 1, 2, 3, 6; turns 4–5 are 4.3's and are named in `why` as pending):

```yaml
episodes:
  - name: a_summer_breakdown_is_reshaped_and_then_restyled
    why: >
      §9's base episode, the four turns 4.2 owns. Turn 1 draws the first chart; 2 and 3 reshape and
      must run a query; 6 restyles the previous chart and must run none. Turns 4-5 (drill-down) are
      Wave 4.3's and are not here yet.
    metrics: [context_retention]
    scope: SETTLED
    turns:
      - say: ¿En qué he gastado más este verano?
        route: QUERY
        groups: [[RESTAURANTS, 46556, 3], [TRANSPORT, 21000, 2], [GROCERIES, 18000, 5]]
        chart:   # the breakdown floor's canonical form, probed at 83774a2: chart-wide sort is None, the ranking is the y axis's own sort
          mark: bar
          channels: [[y, dimension, nominal], [x, value_minor, quantitative], [tooltip, dimension, nominal], [tooltip, value_minor, quantitative]]
          axis_sort: [y, descending, value_minor]
        banking_calls: 1
      - say: Separa transporte público y gasolina.
        route: QUERY
        groups: [[PUBLIC_TRANSPORT, 12000, 2], [FUEL, 9000, 1]]
        banking_calls: 1
      - say: Ahora compáralo por meses.
        route: QUERY
        banking_calls: 1
      - say: Pon el gráfico anterior en horizontal.
        route: VISUAL
        banking_calls: 0
        no_model: true
```

**Routes (checked at `83774a2`):** the harness's `tool_of` knows `QUERY, BALANCE, PAYMENT, COURTESY, CLARIFY, TRANSFER, IDENTITY, CONTACTS, REFUSED, CATEGORIES` — no visual route. Task 7 adds `VISUAL = "VISUAL"` beside them and maps the outcomes `VISUAL_UPDATED`, `VISUAL_INCOMPATIBLE`, `VISUAL_NEEDS_TARGET`, `VISUAL_AMBIGUOUS` to it in `tool_of` BEFORE its fallthrough — which today is `return QUERY`, so a visual outcome is silently accepted as a query: exactly the blindness §9 names, and the first thing Task 7's harness test must prove fixed (a `VISUAL_UPDATED` answer asserted as `route: QUERY` becomes a complaint). **`chart:` shape (defined here, not by the implementer):** a mapping whose keys are a subset of `canonical(spec)`'s elements — `mark`, `stack`, `sort`, `sort_field`, `limit`, `channels` as a list of `[channel, field, type]` triples, and `axis_sort` as one `[channel, mode, field]` triple naming the per-encoding sort an axis carries (the breakdown floor ranks through the y axis's own sort and leaves the chart-wide `sort` None — probed). `canonical(spec)` returns `(mark, encodings, stack, chart_sort, chart_sort_field, limit)` with each encoding a 5-tuple `(channel, field, type, sort_mode, sort_field)`; the harness compares every key the turn gives and ignores the rest (`channels` compares the set of `(channel, field, type)` triples, ignoring per-encoding sorts). **Where the spec comes from:** NOT `answer["visualization"]["spec"]` (that is the presented Vega-Lite) but the context the conversation carried back — `conversation_mapper.to_domain(ConversationPayload.model_validate(answer["conversation"])).visual_contexts[-1].spec`, a `ChartSpec` — so the assertion is on the same object the service remembers. Whether turns 2–3 produce a chart with the fakes is decided by the implementer AGAINST THE REAL HARNESS (probe first, write the expectations the code meets, and report any turn whose expectation had to be weaker than §9 states — that is a measurement 4.3/4.4 will read). «en horizontal» is not in the interpreter's whitelist (4.1 probes): use a phrase it claims («Ordena el gráfico anterior de mayor a menor») and say so in `why`. `derived_from: true` is asserted only on a turn the classifier actually links (probe).

- [ ] Tests → run (the corpus floor stays ≥ 166 episodes; the new file adds one) → implement → commit twice: `test(corpus): a turn can assert its chart, its banking calls and its origin` and `test(corpus): the first visual episode — a breakdown reshaped and restyled`.

**Landed** (`3d8bf99`, `df478d2`, fix rounds `a162c6d`, `ee1f6c3`): the shipped episode needed six turns, not four — the corpus's own six-turn floor (`test_every_episode_is_long_enough_to_be_a_conversation`) has no per-episode exception, so a named-account balance check and a closing courtesy follow the restyle, needing no new capability. Turns 1 and 2 are not this sketch's own wording: «¿En qué he gastado más este verano?» and «Separa transporte público y gasolina.» reach the planner on two sentences no planning case exercises, which moved `tests/test_what_we_hand_to_the_planner.py`'s two handover ratchets (`UNMEASURED` 22→24, `MUTE` 43→44) — neither this task's `Ceiling` nor dataset case to move, so the shipped episode says the same thing in sentences the planning dataset already carries an expectation for (`¿Cuánto gasté el mes pasado?`, `¿Cómo se reparte mi sueldo este mes?`), and both ratchets are back at 22/43. Turn 3, «Ahora compáralo por meses.», never reaches the planner: the deterministic comparison reader claims it before any plan is built and refuses for want of two complete calendar periods — a real `REFUSED` turn with zero banking calls, not the `QUERY` this sketch assumed. And turn 2's chart does not derive from turn 1's: a different, complete financial question reads as a new request rather than a follow-up under the deterministic turn analyser's own rules, so `derived_from: false` is asserted there — `derived_from: true` is exercised only by the restyle turn (Task 3's mechanism), never by a reshape, in this episode.

---

### Task 8: The record on paper

- `docs/next.md` §9: one dated paragraph after 4.1's — what 4.2 verified, recorded, asked and linked; the corpus's first visual episode and what its expectations had to concede; §7 still 4.5's and the ruling on how it will derive.
- Design: §3.1 and §3.2 "What Wave 4 adds" marked landed with the file names; §5's "asks" marked landed with the two texts; §10 row 4.2 ticked.
- Commit — `docs(visual): wave 4.2 landed — every visual follow-up is verified or asked about, and the corpus sees its first chart`.

---

## Self-review

- **Spec coverage.** §3.1 patch → verifier + `source="patch"`: Tasks 1–3. §3.2 `derived_from` link: Task 4. §5 asks + detail names the pair: Tasks 5–6. §9 episodes + banking-call invariant: Task 7. §10 row 4.2 "done when": Task 7's episode. §13: constraints. Not here by design: §7 (4.5), drill-down (4.3), multimodal (4.4), the model rung (Task 9).
- **Placeholders.** Task 3's and Task 7's test bodies carry `...` where the fixture construction copies an existing file's pattern (`test_visualization_stage2_core.py`'s restyle driver; `test_conversation_corpus.py`'s assertion tests); the briefs name the files. Everything else is code or exact prose.
- **Pre-read risks for dispatch.** (1) `EncodingSort`'s field names. (2) Whether `test_decisions.py` pins the VISUAL_PLANNER row shapes. (3) The wire's outcome enumeration and the web client's outcome switch. (4) Whether the fakes can make turns 2–3 produce a chart at all. (5) `_record_visual`'s current signature and the record table it writes (Wave 3 Task 5). (6) Resolved at `0f35640`: `interpret_visual`, `fold`, `VisualIntent`, `apply_visual_intent` live in `domain/services/visual_interpreter.py` (the old `infrastructure/visualization/interpreter.py` is gone); `classify_claimed_visual(intent: VisualIntent, sentence: str) -> VisualFollowUp` and `record_visual_follow_up(follow_up)` exist in `application/queries/readers/visual_follow_up.py`.
