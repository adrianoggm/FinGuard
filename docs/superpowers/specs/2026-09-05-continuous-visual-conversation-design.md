# Wave 4 — Continuous Visual Conversation

**Status:** design, awaiting the user's review. **Not to be planned in tasks until Wave 3 has been
measured** — see §2. The implementation plan (`writing-plans`) is written after `just evaluate-visual`
has run three times, because §8's gate and §5's classifier both need Wave 3's numbers, and a plan
that adds contracts on a base still moving is the mistake the previous plan spent nine reviews avoiding.

**Naming.** The original visual-planner design numbered "measure, set the budget, promote to hybrid" as
wave 4. That step is Task 9 of `2026-09-05-visual-planner-shadow.md` and closes **Wave 3**. This
document is the wave after it.

---

## 1. Why this exists

Wave 3 makes the model able to propose a chart a person would have chosen, and proves it. What the
jury will remember is not that a chart appeared — it is that the chart *answered back*: the user kept
asking, changed how it looked without repeating the question, reorganised or compared the data,
pointed at one bar and asked why, switched between voice and text without losing the thread, and could
see why that chart and not another. Every figure on screen stays bound to verified banking evidence.

> FinGuard turns a financial question into a living visual space the user keeps conversing with.

## 2. Prerequisites — what Wave 3 must have landed and measured

Each row names the artefact, not the intention. Wave 4 does not start until every row is true.

| prerequisite | where it is proven |
| --- | --- |
| `VisualPlannerPort` implemented; `VisualPlanDraft` separate from `ChartSpec` | shadow plan Tasks 1-2, 6 |
| the verifier refuses invalid proposals and never repairs | shadow plan Task 3; `Rejection.rule` per rule |
| the deterministic floor available as fallback; modes `deterministic`, `shadow`, `hybrid` | shadow plan Task 4 |
| fallback reasons observable; budget bounded | Task 4 (`fallback_reason`, `wait_for`), Task 5 (`Step.VISUAL_PLANNER`) |
| initial visual corpus; zero invalid Vega-Lite; zero chart/answer disagreement | Task 7 (`evaluation/visual/cases.yaml`), the compile gate from the foundation |
| `changed_from_baseline` measured | Task 7's `valid ∧ suitable ∧ changed` figure |
| structural privacy: the planner receives no rows and no amounts | Task 6's key-set test |
| **three measured runs and a written mode decision** | Task 9, recorded in `docs/next.md` |

Two numbers from Task 9 feed this design directly: the budget (P95 of `latency_ms`) sets §8's ceiling
for a presentation-only change, and the validity rate decides whether §5's classifier may lean on the
model at all for ambiguous follow-ups.

## 3. Three kinds of visual continuation — and what already exists for each

This split is the centre of the wave. Treating every follow-up as "change the chart" would either
re-query banking for a colour change or, worse, change the data under a chart that looks the same.

### 3.1 Presentation-only — **largely built**

«Ponlo en horizontal» · «Ordénalo de mayor a menor» · «Prefiero líneas» · «Enséñame solo los cinco primeros».

The question and the data do not change. **What exists today:** `interpret_visual()` in
`infrastructure/visualization/interpreter.py` reads these with a whitelist regex (never the model);
`EncodingPatch(mark, stack, sort, sort_field, limit, title)` with `clear()`; `merge_encoding_patch()`
validates the patch against the profile and merges by code; `apply_visual_intent()` yields a new
`VisualContext`; the restyle endpoint renders `present_spec` over the rebuilt spec; the client
references charts by `chart_id` / `source_chart_id` on the wire. The foundation made the rebuilt spec
carry every field (`EncodingSort`, `Stack.disabled`) across all three serialisation boundaries.

**What Wave 4 adds:** the patched spec goes through **`VisualPlanVerifier`** before it is shown, so a
patch that is legal as a patch but wrong for these data (a donut on a profile with no verified total,
a limit on a temporal series) is refused with a reason instead of merged — today `merge_encoding_patch`
holds only the donut rule. And the patch is recorded as a `VisualDecision` with `source="patch"` (a
third source, beside `llm` and `deterministic`), so the explanation in §7 can say what happened.

Properties: no banking call; no financial planner; the current dataset is reused; near-immediate;
an invalid change is explained or the previous chart stays.

**Landed, wave 4.2.** `infrastructure/controllers/llm_controller.py`'s `_visual_only_response` runs
`verify(draft_of(updated.spec), trusted_context.profile)` as a gate: accepted, the merged
`updated.spec` (titles included) is what is shown, stored and offered for saving, and a `VisualDecision`
with `source="patch"` is recorded (`application/queries/ask_financial_question.py`'s
`record_visual_decision`) as `AGREED`/`changed=(was != now)`/`now="patch"`/`detail="accepted"`, never
through the `visual_planner.decision` promotion-log line; refused, `VISUAL_INCOMPATIBLE` speaks one
Spanish clause per rule (`llm_controller._REFUSAL_TEXT`, keyed by the closed `RejectionRule` — the
rule token itself stays in the record's `detail`, never in the text), the previous chart is kept, and
`record_visual_decision_refused` writes one `DECLINED`/`invalid_proposal:<rule>` row. The merge-level
refusal (`apply_visual_intent` raising before a merged spec ever reaches `verify`) is recorded the same
way now, under rule `merge` (fix wave, I3) — promise 1's per-rule rejection rate used to cover only the
verifier's own path. The wire's nested profile now carries the shape facts §4 describes
(`infrastructure/dto/llm_dto.py`'s `VisualFieldPayload.role`/`order`,
`VisualProfilePayload.comparison`/`plan_limited`), with the old derivation kept for a conversation an
older client still holds — it validates, but a sort restyle asked of that chart is refused with rule
`order` exactly as at base, until the conversation's next chart carries the full profile (fix wave, I4)
— `row_count`/`plan_metric` still off the wire, and a contradictory wire `role`/`type` refused at the
DTO (422, not a 500).

**Fix wave, I1: verification is a second, deliberate visible change, not only the asking in §5.**
Probed across the interpreter's whitelist × the eight floor families: 29 of the 62 restyles base
accepted are now refused (`mark`, `order` or `grouped`) — a 47% reduction, and every one a chart the
refusing rule exists to refuse (a line over unordered categories, a value sort on a temporal axis, a
stack on a comparison), never a false refusal. One of those 29 was a chip the answer itself used to
offer on the commonest chart (a plain breakdown's "Ponlo en línea"); `_chart_suggestions`
(`infrastructure/mappers/llm_mapper.py`) now filters every candidate through the same gate
(`interpret_visual` → `apply_visual_intent` → `verify(draft_of(...), ...)`) before offering it, so a
chip offered is a chip the gate has already agreed to (C1).

### 3.2 Analytical reshape — **half built**

«Separa gasolina y transporte público» · «Ahora agrúpalo por meses» · «Compáralo con el verano pasado» · «Excluye los taxis».

The meaning of the query changes, so the data must be fetched again. **What exists:** follow-ups are
already patches on the previous plan (`continuation_patch.read()` / `apply()`,
`GroundedContinuationPatch`, `inherited_window`), the financial planner receives the previous plan as
`ContinuationBase`, and the turn is classified `TurnRelation.FOLLOW_UP` / `TurnFocus.LAST_ANSWER`.
The verified new result already flows through `_decided_visual` → the visual policy → a new profile
and a new decision.

**What Wave 4 adds:** the link. The new `VisualDecision` records `derived_from: chart_id | None`, so
the conversation holds a chain (breakdown → monthly series → comparison) rather than a bag of charts,
and «el gráfico anterior» resolves to a specific `chart_id`. Nothing in the visual planner may ever
modify the financial plan — the arrow only points from result to chart.

**Landed, wave 4.2.** `infrastructure/mappers/llm_mapper.py`'s `_remember_visual_context` sets a fresh chart's
`derived_from` to the previous chart's id server-side, exactly when
`result.visual_follow_up.kind == "ANALYTICAL_RESHAPE"`; a restyle names its own origin instead, at
`llm_controller.py`'s `_derived_context` (Task 3's mechanism, not this one). The classification
travels from `_steps` (an async generator) to `stream()`'s `_decided_visual` call through
`follow_up_seen`, a one-element list threaded as an explicit out-parameter (`stream` →
`_steps_answering_scope` → `_steps`) — reviewed as the least-bad mechanism for a value born inside a
generator and needed after it. Two of `_steps`'s early returns that end a turn before `analysis`
exists — retry control and the previous-plan restore — still draw a chart of their own but never
reach `_record_visual_follow_up`, so that chart's `derived_from` is `None`, stated in
`_record_visual_follow_up`'s own docstring rather than hidden.

### 3.3 Drill-down — **new**

«¿Por qué agosto es tan alto?» · «¿Qué movimientos explican ese pico?» · «¿Qué comercio es responsable?» · «Enséñame las compras de esa barra».

Not a style change: a new, bounded query derived from one part of the previous result.

```
selected group or period  +  previous verified plan
        ↓
bounded drill-down plan           (the previous plan narrowed to that group/period; never widened)
        ↓
banking `run` with those filters  (the port already has `run` and `ranked`; no new endpoint)
        ↓
movements + explanation, linked to the original evidence
```

Rules: keep the account scope; restrict to the pointed-at period or group; never widen the window
silently; link the movements to the original evidence ids; allow "back to the previous chart";
**refuse an ambiguous reference** («ese pico» when two bars tie) with a question, never a guess.

Reference resolution — «agosto», «la barra más alta», «esa categoría» — is a deterministic reader over
the previous result's dimension values first (they are the labels on screen), and the model only when
the reader finds nothing or more than one match. It reads the result's *labels*, which are already on
the client; it never receives amounts.

## 4. Visual conversational context

Not a second memory. **What exists:** `Conversation.visual_contexts: tuple[VisualContext, ...]`
(bounded by `MAX_VISUAL_CONTEXTS`), `with_visual_context()`, `visual_context(chart_id)`;
`VisualContext(chart_id, spec, profile)`; `Conversation.conversation_id`; the previous plan and the
evidence UUIDs the conversation already carries for movement references.

**What Wave 4 adds** to `VisualContext`, not beside it:

```python
@dataclass(frozen=True)
class VisualContext:
    chart_id: str
    spec: ChartSpec
    profile: VisualProfile
    decision: VisualDecision | None = None      # source, proposed_spec, fallback_reason — turn-local
    source_plan: FinancialPlan | ComparisonPlan | RatioPlan | None = None   # the plan this charted
    evidence_ids: tuple[UUID, ...] = ()   # the movements behind the figures, turn-local like decision
    derived_from: str | None = None       # the chart this reshaped or drilled from
    turn_id: UUID | None = None           # minted where the chart is made; survives the wire (D-4.2-9)
```

The wire payload (`VisualContextPayload`) grows only `derived_from` and `turn_id` at the context
level; its nested profile payload grows the four shape facts below. `decision`,
`source_plan` and `evidence_ids` are **turn-local**: the orchestrator keeps no conversation store —
the conversation itself is the wire payload — so they are populated in the turn that makes or derives
the chart and come back `None` / `()` after a round-trip. What a later turn can still recover is what
the conversation already carries: `last_plan` for the newest chart's plan and the `referenced_*`
reference for the last answer's evidence. A round-tripped profile carries the value-free shape facts
the verifier reads — each field's `role` and `order`, and the profile's `comparison` and
`plan_limited` — because §3.1 verifies a merged patch against it and a narrowed profile refused
legitimate restyles after one round trip (wave 4.2, Task 3). It still carries nothing a browser
should be able to assert about the data (`row_count`, `plan_metric` stay off the wire), and the wire
is still not where authority lives: the verifier decides — and since Task 3b, a context is only a
verifier's input if the server's seal on it verifies for this holder (`MetricTokenCodec.verify_context`),
so a client can draw and navigate a chart it altered but cannot have a decision taken on it. The echo
contract is exact: a client returns `visual_contexts` precisely as received, because the seal covers
every field of it — a rendered chart spec (`$schema`, `width`, `height`, the three keys `present_spec`
adds for drawing) is not a context spec and is refused at the wire rather than silently untrusted. The
seal itself is an integrity envelope, not a secret — base64url, not encrypted — and it is the first
field on the conversation wire to carry the holder reference in clear.
`source_plan` is typed
`FinancialPlan | ComparisonPlan | RatioPlan | None` and copied from the result's `metric_definition`;
`evidence_ids` are the plan's `transaction_ids` when it carries them; `turn_id` is a UUID minted where
the chart is made — no turn identifier exists elsewhere in the service. The context can answer: which
question produced the chart, which plan produced its data, which fields the result has, which chart
was accepted and from whom, which evidence backs each figure, in which turn.

**Invariant.** A visual follow-up may change the presentation; it can never, on its own, change the
financial question or its figures.

## 5. Follow-up resolution

```python
VisualFollowUpKind = Literal[
    "PRESENTATION_ONLY", "ANALYTICAL_RESHAPE", "DRILL_DOWN", "UNRELATED", "AMBIGUOUS",
]
```

Where it lives: `application/queries/readers/visual_follow_up.py`'s `classify()`. The deterministic
reader decides first — `interpret_visual()` already recognises presentation-only;
`read_contextual_follow_up()` already recognises a reshape; the drill-down reader from §3.3 is new.
The model is consulted only when the readers disagree or find nothing, and only if Wave 3's validity
rate has earned it — the turn interpreter ladder (`Deterministic → Shadowed → Hybrid → Promoted` in
`turn_interpreters.py`) is the intended home for that model rung, still 4.2's to build. The result is
**verified** the way every other decision is: a `PRESENTATION_ONLY` must produce a patch
`merge_encoding_patch` accepts; an `ANALYTICAL_RESHAPE` must produce a plan the financial verifier
accepts; a `DRILL_DOWN` must resolve to exactly one group.

The controller's visual short-circuit — a claimed `interpret_visual` answered before any
`TurnAnalysis` exists — classifies with `classify_claimed_visual(intent, sentence)` instead, the
same module's own row for a turn `classify` never sees.

| kind | financial planner | banking | visual planner |
| --- | --- | --- | --- |
| PRESENTATION_ONLY | no | no | patch + verifier |
| ANALYTICAL_RESHAPE | yes, as a patch | yes | yes |
| DRILL_DOWN | yes, bounded | yes | optional (movements are a table first) |
| UNRELATED | the normal flow | as the query needs | as the result needs |
| AMBIGUOUS | no | no | no — ask |

A doubtful classification never reinterprets the chart silently. It asks:
«¿Quieres cambiar sólo la visualización, o volver a calcular los datos agrupándolos por meses?»

**Landed, wave 4.2** — as `VISUAL_AMBIGUOUS`, conversation unchanged, zero banking calls.
`llm_controller.py`'s `_ASK` holds exactly the two texts `classify_claimed_visual` can return, one
per `AMBIGUOUS` claimant pair the controller's short-circuit can reach:
presentation+reshape — «¿Quieres cambiar sólo la visualización, o volver a calcular los datos?»;
presentation+drill — «¿Quieres cambiar sólo la visualización, o ver los movimientos detrás de esa
parte del gráfico?». `reshape+drill` is the vocabulary's third pair and is unreachable today (the
reader's own drill-before-reshape precedence), so it carries no text here — a reachability test
asserts exactly that member is the one nothing produces.

Recorded as one `Decision` on the existing record: `Step.VISUAL_FOLLOW_UP`, `now=<kind>`,
`detail=<reader that claimed | model | asked>`. A twelfth seam, justified in its commit.

## 6. Continuity across voice, text and chart

The conversation belongs to the user, not to the channel. **What exists:** the voice gateway
(`apps/voice-gateway`) already stamps every event with `turnId` and a `session_id`, and cancels by
turn generation; text, audio and visual results are already synchronised per `docs/next.md` §10.

**What Wave 4 adds:** `chart_id` and the `VisualDecision`'s identity travel on the same events, so the
client can never play a turn's audio beside another turn's chart; and the **spoken summary is
derived from the chart, not from the table**:

> «Agosto fue el mes con mayor gasto. En pantalla tienes el desglose, y puedes preguntarme por cualquiera de los meses.»

The voice presenter reads the accepted spec's mark and the profile's top group — never all categories —
which is the second consumer `docs/next.md` said presenters-per-channel had been waiting for. Voice and
text follow-ups share the `conversation_id` and therefore the visual context; a voice «ponlo en
horizontal» is the same `PRESENTATION_ONLY` as a typed one.

## 7. Visual explainability

Generated **from the accepted decision**, never from the model's free-text `rationale`:

```python
@dataclass(frozen=True)
class VisualExplanation:
    chart_reason: str          # "Barras: estás comparando categorías."
    ordering_reason: str | None   # "Orden: gasto total, de mayor a menor."
    scope_summary: str         # "Datos: 23 movimientos entre el 1 de junio y el 31 de agosto."
    evidence_count: int
    decision_source: Literal["AI_VERIFIED", "DETERMINISTIC_FALLBACK", "USER_PATCH"]
```

Shown under a discreet «¿Por qué este gráfico?». Derivation is a pure function of
`(spec, profile, decision, source_plan)`: mark → `chart_reason`; the dimension's `EncodingSort` →
`ordering_reason`; the plan's window and the evidence count → `scope_summary`; `decision.source` →
`decision_source`. The user never sees a provider error, a prompt, or a verifier rule name.

**Rule:** the explanation describes the chart finally accepted. If the model proposed a donut and the
verifier refused it, the explanation justifies the bars the floor drew — and says
«visualización segura seleccionada automáticamente», not why the donut failed.

## 8. Performance

Once banking has answered, the answer writer and the visual planner are independent; they run
concurrently on the same verified result. Objectives, each one a test:

- a presentation-only change makes **zero** banking calls (assert on the banking fake's call count);
- a deterministic presentation change is near-immediate — no model call on that path;
- the spoken response does not wait for the chart when the chart is still inside its budget;
- the visual planner keeps Wave 3's budget; on expiry the floor is shown;
- adding the planner never delays the first visible sign of progress.

**The gate is stated against Wave 3's measurement**, not a number invented here: a follow-up turn's
P95 may not exceed Wave 3's measured single-turn P95 plus the presentation-only path's own cost, and
that cost is measured before it is promised.

## 9. Conversational visual evaluation

The visual corpus gains **episodes**, not sentences — and this is the item that finally makes the
conversation corpus see charts (today 0 of 190 episodes reach `question_to_dto`).

Base episode:
1. «¿En qué he gastado más este verano?»
2. «Separa transporte público y gasolina.»
3. «Ahora compáralo por meses.»
4. «¿Por qué agosto es tan alto?»
5. «Enséñame los movimientos responsables.»
6. «Pon el gráfico anterior en horizontal.»

Per turn the harness asserts: the financial plan preserved or changed exactly as expected; the calls
made and **the calls avoided** (banking not called on turn 6); the evidence used; the visual profile;
the accepted chart (`canonical()`); the explanation's `decision_source`; context preserved
(`derived_from` chain); latency; decision source.

Multimodal cases: voice → chart → typed follow-up; text → chart → spoken follow-up; a voice
interruption mid-response; a channel switch that keeps account, period and category; the chart visible
while the spoken summary plays.

Invariants, each a test that names what would break it:
zero amounts sent to the visual planner · zero invalid specs · zero text/chart disagreement · zero
silent scope widening · **zero banking calls for presentation-only changes** · zero explanations of a
discarded spec · continuity across channels on every episode · every drill-down linked to verifiable
evidence · every rejected proposal ends in a valid answer.

## 10. Incremental delivery — each sub-wave is a plan of its own

| sub-wave | delivers | done when |
| --- | --- | --- |
| **4.1 Context and classification** | `VisualContext` extended (§4); `VisualFollowUpKind` and the deterministic readers (§5); `Step.VISUAL_FOLLOW_UP`; invariant tests | every follow-up kind is classified and recorded; no visible change yet |
| **4.2 Continuous visual conversation — done (3b, 4.2b)** | presentation patches through the verifier with `source="patch"`, on a context the server has sealed (3b); reshape linkage `derived_from`; the corpus's episode reshaped, regrouped and restyled in §9's own words (4.2b) | done — `evaluation/conversations/32-visual-continuation.yaml` runs every turn end to end with the banking-call assertion: turn 2, «Separa transporte público y gasolina.», re-queries the plan in view (one call, `derived_from: true`) instead of a planning-dataset stand-in; turn 3, «Ahora compáralo por meses.», regroups it into a month-by-month series instead of the comparison reader's refusal; turns 4-5 restyle a bar by number (turn 3's own chart is a line, which refuses an orientation change) and by «el gráfico anterior», each an effective, verified change. Turn 1 stays the planning-dataset anchor sentence — `window_words.py` excludes «este verano» by design, so nothing here could give it a window without guessing. Turns 4-5 of §9's own sketch — a drill-down into a split category — are 4.3's. **Fix wave, I-4:** in §9's own order the restyle bullet (§11) is refused right after turn 3's month line, with no chart between them for a bar to be turned — episode 32 reaches it only because turn 4 names a bar by number first; the scenario in §9's own sequence closes once 4.3 leaves a chart behind for «el gráfico anterior» to resolve to |
| **4.3 Financial drill-down** | reference resolution over result labels; bounded drill-down plan; movements linked to evidence; "back" | turns 4-5 pass; an ambiguous reference asks |
| **4.4 Multimodal continuity** | `chart_id`/decision id on voice events; the chart-derived spoken summary; the multimodal cases | the five multimodal cases pass; full-turn latency measured against §8 |
| **4.5 Explainability and product** | `VisualExplanation`; «¿Por qué este gráfico?»; the video's polish | the acceptance scenario in §11 runs on camera |

Sub-waves 4.1-4.2 are worth landing on their own: they make the corpus see charts and make every
visual follow-up verified, without a single new product surface.

## 11. Acceptance scenario

Complete when this runs end to end, on camera:

- By voice: «¿En qué he gastado más este verano?» — FinGuard answers by voice, shows the ranking, and
  discreetly explains why bars. **Not this wave** — voice and explainability are §4.5's; episode 32's
  turn 1 anchors on «¿Cuánto gasté el mes pasado?» instead, because `window_words.py` excludes a
  season on purpose.
- By text: «Separa transporte público y gasolina.» — the summer and the account are kept, the data is
  re-queried, the chart updates, `derived_from` points at the first chart. **Landed, 4.2b** —
  episode 32 turn 2, verbatim; the window kept is turn 1's own (last month, not a summer — the same
  reason as above), one banking call, `derived_from: true`.
- «Ahora compáralo por meses.» — the query becomes a time series and a suitable chart is chosen.
  **Landed, 4.2b** — episode 32 turn 3, verbatim: a month-by-month line, one banking call,
  `derived_from: true`, where this reader used to refuse as an incomplete comparison.
- «¿Por qué agosto es tan alto?» — August is resolved inside the previous result, the responsible
  movements are queried, the answer carries evidence. **Not this wave** — reference resolution and
  the bounded drill-down plan are 4.3's.
- «Pon el gráfico anterior en horizontal.» — the previous visual context is recovered and only its
  presentation changes, with zero banking calls. **Landed, 4.2b** — episode 32 turn 5, verbatim:
  «el gráfico anterior» resolves to the chart the prior restyle just appended, zero banking calls,
  an effective change. **Fix wave, I-4:** landed on a bar named explicitly, not on §9's own order —
  probed in that order, right after turn 3's month line with no restyle between, «el gráfico
  anterior» is that line and this turn refuses (orientation is a bar's concern, not a temporal
  series's), which is why episode 32 inserts turn 4, «Pon el primer gráfico en vertical.», naming a
  bar by number before this bullet's own wording can land on one. The ordered scenario itself closes
  only once 4.3's drill-down turns leave a chart of their own behind for «el gráfico anterior» to
  resolve to.

## 12. Out of scope, to protect the calendar

Arbitrary Vega code from the model · financial forecasts · investment recommendations · automatic
actions from a chart · Bizum confirmation by voice alone · drag-and-drop visual editing ·
collaboration between users · proactive alerts.

## 13. What this design refuses

- **No second memory.** The visual context extends `VisualContext` on `Conversation`; a parallel store
  is two truths.
- **No visual planner writing a financial plan.** Result → chart, never chart → plan.
- **No explanation from the model's rationale.** Derived from the accepted decision or not shown.
- **No silent reinterpretation.** An ambiguous follow-up asks.
- **No wire authority.** `derived_from` and `turn_id` travel; decision, plan and evidence are
  turn-local and are never taken from the wire (§4).
- **No unsealed source.** An unsealed or altered visual context is still drawn and navigated, but
  is never a source for a restyle decision or a saved metric (§4, Task 3b).
