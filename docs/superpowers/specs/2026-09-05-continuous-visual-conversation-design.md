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

**Hardened, 2026-09-11 (Task 3, Wave 3 hardening, A-8/A-10/B-8).** The vocabulary above claimed its
own §3.1 examples less than the audit found: «Prefiero líneas», «Ponlo en líneas», «solo los cinco
primeros» (word form, not only the digit), and «Ordena las categorías de mayor a menor» either
refused outright (an unknown plural mark, a limit written in words) or, for the last one, answered
`VISUAL_AMBIGUOUS` because `categorías` is itself a `_FINANCIAL` word — none of §3.1's own sentences
is exempt from being read as a restyle by this reader any more. A word-or-digit limit phrase now
reads into a 1-20 range (`_LIMIT_PHRASE`, distinct from `top`'s 1-100) and refuses out of range with
a reason rather than silently clamping. The fix round (F-H1, a second implementer, Critical)
found and closed a regression the first pass introduced: a sentence naming a real financial word
beside a style word, with nothing left for the narrow `_FINANCIAL_QUERY_SIGNAL`/named-category
checks to catch («Ordena mis gastos por categoría y dime el total»), used to fall through to the
restyle path with zero ledger calls — the actual question silently discarded. A `_DATA_ASKING`
check (`dime`, `cuánto(s/as)`, `total`, `gasté`, `ingres-`, `saldo`, a month name, a digit) now
routes that shape to the planner instead; the two named §3.1 examples above are unaffected
(`mixed=False`, still pure restyles).
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

**Landed, 4.3.** The resolver (`domain/services/visual_reference.py`) reads a month by the answer
writer's own vocabulary and a precisely named category over the labels a trusted chart drew, and a
superlative («la barra más alta», «el pico», «el más bajo») only on a chart whose `ranked` is strict
at both ends. Weekday labels and series labels are deliberately not resolved (R14, R15): a frequency
chart's weekday reference and a comparison's window or a recurrence's cadence still go to the planner
exactly as before. **Bound before it narrows (fix round, C-1/I-1):** the reader checks
`newest.plan_digest == plan_digest(conversation.last_plan)` before doing anything else, so a KPI turn
answered after the chart, a restyle of an older chart, or a client-edited `last_plan` all fail this
check and fall to today's path rather than narrow a plan the chart was never drawn from. The bounded
plan (`application/queries/readers/drill_down.py`) is the plan in view narrowed to the resolved
label — for a month, `whole_month(...)` intersected with `newest.window` (the dates the chart's own
rows actually cover, itself the ledger's own answered window bounded by the plan's own `date_range`
— `_answered_window`'s intersection, fix round 2's N-2 — never the ledger's answer alone, which a
lenient ledger or the corpus's own fake can report as whole calendar months for a plan that asked
for a slice of one) before the M2 clamp to today; the two dates are compared before the range is
ever built (fix round 2's N-1), so a label whose calendar month does not overlap `newest.window` at
all falls to today's path rather than raising, and a plan whose own window is a slice of a calendar
month never widens past that slice; `categories=(key,)` for a category — and regrouped by merchant
(`GroupBy.MERCHANT`, `group_limit=None`), with the account scope and every other filter kept
unchanged, run once with `MAX_SELECTED_TRANSACTIONS` movements: the answer is the merchant breakdown
of that bar, and its evidence is the movements behind it. «Back» is unchanged: the drill's own chart
is one more entry in `visual_contexts`, deriving from the chart it drilled rather than replacing it,
so the chart the drill narrowed is still there for a restyle to name — a restyle sentence («el
gráfico anterior», by number) resolves to whichever chart is newest, exactly as before 4.3. Two or
more candidates fitting one reference ask, in the chart's own order (up to four names; more asks for
the label itself instead), with zero banking calls and nothing merged — and only for a chart whose
plan can actually be drilled (`group_by` MONTH or CATEGORY, checked before the ask, fix round I-4):
a MERCHANT-grouped chart with two tied labels never asks, because no reply to that question could
ever be acted on.

**Hardened, 2026-09-11 (Task 3, Wave 3 hardening, A-9/B-6).** This section's own two named
examples were previously misclaimed: «Explícame el gráfico» fell to the planner (a real ledger
call to re-explain a chart already on screen) instead of being claimed by `asks_why`'s zero-call
path, and «¿Qué comercio es responsable?» — this section's own third sentence — likewise reached
the planner instead of the drill, dropping the narrowed window the drill exists to keep. `asks_why`
now claims `explica(me)?`/`cuentame`/`que significa … grafico`; the drill's own `_TOP` vocabulary
widened with `tanto`/`responsable`/`de donde sale` so a "why" that is really a drill (not a request
to re-explain the chart's own shape) is told apart and routed correctly. «¿Por qué tanto?» over a
chart whose plan is `group_by: NONE` is a further, narrower case (Task 8b): the drill has nothing
to narrow into, so it now asks «¿Tanto en qué? Puedo desglosarlo por categoría o por mes.» with
chips, zero ledger calls, rather than reaching the planner for an unrelated total.

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
    labels: tuple[str, ...] = ()          # the dataset's dimension values, tallest first (Wave 4.3)
    series_labels: tuple[str, ...] = ()   # the dataset's distinct series values, producer order
    ranked: bool = False                  # both ends of `labels` are strict; a shape fact, not a value
    window: AbsoluteRange | None = None   # the dates the rows cover (wave's fix round)
    plan_digest: str | None = None        # a fingerprint of the plan that charted them (fix round)
```

The wire payload (`VisualContextPayload`) grows `derived_from` and `turn_id` at the context
level and, since Wave 4.3, `labels`, `series_labels` and `ranked`: the dimension and series values
the chart drew, tallest first — value-free strings already on the client (§3.3), so a later turn
resolves «agosto» or «la barra más alta» against them without a second query; `ranked` is a shape
fact (both ends strict), never an amount — and, from the wave's fix, `window` and `plan_digest`:
the dates the chart's rows cover and a fingerprint of the plan it charted, so a drill intersects a
month with what was drawn and refuses a plan that is not the chart's own — the design's «`last_plan`
for the newest chart's plan» is no longer assumed; it is checked. Its nested profile payload grows the four shape facts
below. `decision`,
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

**2026-09-11 — the seal binds the conversation and the position, not only the holder and the
content (Task 1, Wave 3 hardening, A-1).** `MetricTokenCodec.seal_context`/`verify_context` fold
two more facts into the signed body: `conversation_id` and the context's own index in the ordered
list this process last emitted — the `c2.` envelope, replacing `c1.` (an old `c1.` token still
parses at the wire but opens as reason `"format"`, never a 500). Verification checks four things in
order — holder, conversation, position, content — and `verify_context` now returns which one failed
rather than a bare bool, so a genuinely-sealed context replayed from another conversation or moved
to a different index in this one answers «este gráfico no es de esta conversación» while a changed
byte or a malformed token keeps the older, more general refusal text. A context's position is not
fixed at birth: `Conversation.with_visual_context` evicts the oldest entry once `MAX_VISUAL_CONTEXTS`
is reached, which shifts every survivor down by one index, so `conversation_mapper.
reseal_visual_contexts` re-signs every still-trusted context at its current index whenever the list
changes shape (a new chart appended, an eviction, a compound turn's merge) — the invariant a seal
now states is *"position N of the list the orchestrator itself last emitted"*, never *"position N of
the list a client first saw it in"*. One consequence stated plainly: every visual context sealed
before this task carries a `c1.` token, and the first echo of it after this task lands answers
untrusted once, the same way a stale key rotation already does — the chart is not lost, it is drawn
fresh the next turn and sealed under the new envelope from there.

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

**Hardened, 2026-09-11 (Tasks 3/8, Wave 3 hardening, B-1/B-2/B-4).** Three shapes of follow-up
sentence used to resolve to a wrong-but-confident answer instead of the honest outcome above.
**Exclusions:** «quita restaurantes», «elimina los taxis», and every other imperative or
prepositional exclusion form (`quita(r)`, `elimina(r)`, `borra(r)`, `saca(r)`, `excluye(ndo)`,
`fuera de`) now exclude the named category — before this fix, the sentence read as if the category
were the *only* one wanted, inverting the ask. **Seasons:** «¿En qué he gastado más este verano?»
and its siblings no longer silently answer whatever window was already in view; `season_ask`
opens a `FinancialClarification` (the same `Slot.WINDOW` machinery an ordinary «¿de qué periodo?»
question uses) naming the season's own months as a range chip the window vocabulary can read back
verbatim, so the ask is a real round trip, not only an honest question with no way to hear the
answer. This closes the round-trip half of B-4; **still open (D-H-7):** the draft this ask opens
carries only `direction`, so a season sentence that also implies a category breakdown («¿en qué…»)
still resolves to a total once the window answers, not the ranking the sentence's own wording asks
for — tracked for the next fix wave, not closed here. **Bare words:** an unrecognised bare word or
short phrase («deshaz», «en euros», «nada») used to be read as a merchant filter — a confident,
three-call guess-then-retract for a word that named no merchant at all. A closed stop-list
(`domain/services/merchant_words.never_a_merchant`) now asks instead, zero ledger calls, for every
word or two-word phrase on the list; corpus episodes that pinned the old guess-then-retract as
"expected" behaviour were corrected in the same task (Task 8) once the pinned turns were confirmed
to be confident wrong answers, never an honest retraction. The stop-list is an interim measure,
by design: a general "capitalised-or-known-merchant" rule would close the same ten pinned turns and
the open set the stop-list still leaves unprotected, but only once every episode that would break
under it has been corrected — R-H1, `.superpowers/sdd/2026-09-11-wave3-hardening/progress.md`.

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

**Landed, 4.4.** `chartId` (the answer's `visualization.chart_id`) and `visualTurnId` (the newest
`visual_contexts[].turn_id`) travel on `assistant.text.final`, `audio.started`, `audio.completed`
and `turn.completed` — additive, protocol version unchanged (ADR-010) — and the web client refuses
to play `audio.started` against any `chartId` other than the one its own turn just announced, the
same failure path as a wrong sample rate. Parity holds: the VOICE-only refusal in
`interpret_visual` is gone, its `voice` parameter deleted everywhere it was read; a spoken restyle
reaches `_visual_only_response` exactly as a typed one does, zero banking calls, and the
metric-saving offer stays TEXT-only, pinned both ways. The spoken summary's actual rules: a
top-group sentence fires only when the chart's labels are `ranked` strictly at both ends — never on
a tie — named through a closed table over every `GroupBy` member (categoría, comercio, contraparte,
cuenta, mes, semana, día); the second sentence is keyed off the profile's own shape (temporal,
comparison, donut, or the plain desglose), independent of the plan's grouping; income and expense
plans read «ingreso»/«gasto» off `plan.direction`; a MONTH-grouped top label names a calendar year
(e.g. «agosto de 2026»), read as a calendar fact rather than a ledger value, not the strict
never-a-digit claim it otherwise is. `QuestionResponse.spoken` fills only for VOICE and only when a
chart exists; the gateway speaks it and still shows the full written text.

## 7. Visual explainability

**Why the 2026-09-05 wording cannot stand.** That first draft derived an explanation from
`(spec, profile, decision, source_plan)` — but §4 (as amended) already rules `decision` and
`source_plan` **turn-local**: the orchestrator keeps no conversation store, so both come back
`None` after a round trip, and §13 refuses a second memory that would keep them alive past their
turn. An explanation asked on a later turn — «¿Por qué este gráfico?» typed after the chart has
already scrolled by — cannot be built from either field, because by then neither exists. The
explanation can only be a pure function of what the sealed `VisualContext` actually carries once it
has crossed the wire and come back: `spec`, `profile`, `labels`, `ranked`, `window`, `derived_from`,
and the two **descriptive provenance** facts this wave adds and seals with the rest — `source` and
`fallback_reason` (§4). Vocabulary, not authority: nothing reads either field to decide anything: an
explanation is not a policy, and neither field ever feeds a verifier, a merge, or a drill.

```python
@dataclass(frozen=True)
class VisualExplanation:
    chart_reason: str            # "Barras: comparas categorías entre sí."
    ordering_reason: str | None  # "Orden: de mayor a menor importe."
    scope_summary: str           # "Datos: 4 categorías entre el 1 de junio y el 31 de agosto de 2026."
    provenance: str | None       # "Elegido por las reglas de FinGuard."; None for an echoed chart

    def as_text(self) -> str: ...
```

`provenance` is `str | None`, not `str`: `source is None` — a chart echoed back with no decision
behind it — omits the line entirely rather than guessing at one (see "Provenance wording" below).

`explain_chart(context: VisualContext) -> VisualExplanation` is a pure function of `context.spec`,
`context.profile`, `context.labels`, `context.window`, `context.source` and
`context.fallback_reason` — nothing else, and nothing turn-local (M-2, fix wave: `context.
derived_from` and `context.ranked` are on the wire, but `explain_chart` reads neither — `ranked` is
deliberately not read for `ordering_reason`, which states what the spec's own sort says it draws,
not whether the data happens to make every band distinct). mark → `chart_reason`; the dimension's
own `EncodingSort` → `ordering_reason`; `window` and `len(context.labels)` → `scope_summary`
(I-2, fix wave: not `profile.row_count`, which does not survive the wire — see "Scope" below);
`source`/`fallback_reason` → `provenance`. The noun inside
`chart_reason`/`ordering_reason`/`scope_summary` (mes/día/categoría/grupo/semana/comercio) comes
from the drawn `labels` themselves for the shapes a label alone can name, and — **hardened,
2026-09-11 (Task 2, U-5)** — from the sealed `group_by` (§4) for the one shape a label alone
cannot: a WEEK bucket's key is byte-identical to a DAY bucket's, so `_nouns_of(group_by, labels)`
reads `group_by` first (WEEK → semana(s), MERCHANT → comercio(s)) and only falls back to the label
guess when `group_by` is `NONE` or absent (an older, pre-hardening context). The plan itself is
still not on the wire (§4); `group_by` is sealed as its own shape fact precisely so the noun does
not need it. The measure word (importe/número de movimientos/importe medio/peso) comes from
`profile.measure_metric()` (R11) — **hardened, 2026-09-11 (Task 4, U-2/U-3/A-7/C-2):** `_MEASURE_WORDS`
is now closed over every `Metric` member plus `RATIO_SHARE`/`COMPARISON`, asserted complete at
import time; RECURRENCE moved from «número de movimientos» to «importe» (its rows are amounts, not
counts), and COUNT/FREQUENCY → «número de movimientos», AVERAGE/PERIOD_AVERAGE → «importe medio»
are now covered where they were previously missing.

**Provenance wording** — unchanged by Wave 3 hardening; none of the five sentences below, or the
rule that omits the line entirely when `source is None`, was touched by any hardening task (the
closed sets `VisualSource`/`FallbackReason` actually carry, never the
invented `AI_VERIFIED`/`DETERMINISTIC_FALLBACK`/`USER_PATCH` of the first draft):

- `source == "llm"` → «Propuesto por el asistente y comprobado por las reglas de FinGuard.»
- `source == "deterministic"` with a `fallback_reason` → «Visualización segura seleccionada
  automáticamente.»
- `source == "deterministic"` with no `fallback_reason` → «Elegido por las reglas de FinGuard.»
- `source == "patch"` → «Ajustado a petición tuya sobre el gráfico anterior.»
- `source is None` (a chart echoed with no decision behind it) → the provenance line is omitted
  entirely, not guessed at.

The sentence never names a `RejectionRule`, a provider error, or the proposal the verifier
discarded — the §9 invariant «zero explanations of a discarded spec» restated here: the explanation
describes the chart finally accepted, never the one the model wanted.

**Scope.** `scope_summary` states dates, read from the sealed `window` («entre el 1 de junio y el
31 de agosto de 2026»), and a count of groups. I-2 (fix wave): the count is `len(context.labels)`,
not `profile.row_count` — the labels are sealed and do travel the wire, while the row count does
not, so a later-turn explanation of an echoed chart (every `VISUAL_EXPLAINED` answer, by
definition) would otherwise state a false «0» on its own headline question. The count is omitted
— «Datos: las categorías entre …», «Datos: los meses …» (the article agrees in gender with the
noun) — whenever `labels` is empty or its length hits the wire cap (`MAX_DRAWN_LABELS`,
`visual_profile.py`): a full label list means "at least this many, we stopped counting", and
stating that as a bare number would read as a total rather than a floor. It never
states an amount, a total or a percentage — the evidence (movement) count does not survive the
wire (§4) and so is never claimed, honestly rather than by omission alone.

**D-4.5-3 — RETIRED, 2026-09-11 (Task 2, Wave 3 hardening, U-5).** The limit above stood on a false
premise: a WEEK label's shape is identical to a DAY label's, so a noun read from the label alone
cannot tell them apart — but the noun no longer has to. `VisualContext.group_by` (§4) now seals the
plan's own grouping, and `_nouns_of(group_by, labels)` reads it first: `GroupBy.WEEK` →
«semana»/«semanas»/«una semana», `GroupBy.MERCHANT` → «comercio»/«comercios», falling back to the
label guess only for `GroupBy.NONE` (deliberately absent from the noun table) or an older,
pre-hardening context with no `group_by` sealed. A week-grouped chart's explanation now says
«semanas», never «días» — `test_nouns_of_a_week_group_says_semanas_even_with_day_shaped_labels` and
`test_a_week_chart_says_semanas_after_a_round_trip` pin the round trip, both green. Measure words
(the second half of the noun table, importe/número de movimientos/importe medio/peso) are unchanged
by this fix and are covered separately below (§7, "Landed, 4.5" and Task 4's own table).

**Two surfaces, one derivation.** `explain_chart` runs once per charted answer and feeds both:
`QuestionResponse.explanation` on every answer that carries a chart — a discreet «¿Por qué este
gráfico?» disclosure on screen, inert (§12) — and the question itself, «¿Por qué este gráfico?»
(and its variants), answered as outcome `VISUAL_EXPLAINED` with `as_text()` in `text`. The gateway
is untouched: it already speaks `text` when `spoken` is `None`, so no new protocol surface is
needed to have it spoken. No new endpoint (§13).

**What is refused.** Binding the explanation to `turn_id` — saying *which recoverable turn* asked
for a chart — is refused for this wave: it needs a store this design does not keep (§13's second
memory) or a replayable record the orchestrator has nowhere to put; the explanation does not need
it, because it is a function of the chart, not of the turn that asked about it. The model's
free-text `rationale` is refused too, as it always was: the explanation is derived from the
accepted decision's closed vocabulary, never from what the model said about itself. M-11 (fix
wave): «¿Por qué barras de gasolina?» is not a why-question — `asks_why`'s grammar is anchored
(a sentence naming a period, a month or a category, like «gasolina», is a drill or a financial
question, never a why-ask) and abstains, so `interpret_visual` claims it instead and reads it as a
restyle to bars. Pre-existing behaviour, not something this wave changed, and the safe ordering
(`asks_why` runs before any restyle is applied on a sentence it DOES claim) is in place; a wider
`_WHY` vocabulary that could tell the two apart is left for later.

**Landed, 4.5.** Both surfaces run as specified above: `QuestionResponse.explanation` on every
charted answer (TEXT and VOICE, restyle included), and «¿Por qué este gráfico?» (and its close
variants) answered outcome `VISUAL_EXPLAINED` with `as_text()` in `text`, zero ledger calls, zero
model calls, the conversation unchanged. Probed strings, a `CATEGORY`-grouped bar over four
categories: `chart_reason` "Barras: comparas categorías entre sí.", `ordering_reason` "Orden: de
mayor a menor importe.", `scope_summary` "Datos: 4 categorías entre el 1 de junio y el 31 de
agosto de 2026.", `provenance` "Elegido por las reglas de FinGuard." — the four sentences joined by
`as_text()`. What it refuses, exactly as ruled: the `turn_id` → recoverable-turn binding (needs a
store this design does not keep); no amount, total or percentage, ever; no rule name, provider
error or discarded proposal. The fix wave closed one limit this section used to name here as open:
a later-turn `scope_summary` now counts `len(context.labels)` rather than the wire-zeroed
`profile.row_count` (I-2/D-4.5-8, see "Scope" above). D-4.5-6/M-2 (the R7 digest guard in
`llm_mapper.py`) is **closed by Task 7 (R14)**, not by that digest: a digest of `metric_definition`
cannot tell a borrowed plan from an owned one, since the seal and any guard both read it off the
same field within the same call. Only `ask_financial_question.stream()`'s own back-fill
(`~:630-633`) knows whether `metric_definition` came from `conversation.last_plan` rather than this
turn's own producer, so it says so directly: `AskFinancialQuestionResult.metric_definition_borrowed`,
set True in the same `replace` that back-fills the plan. `question_to_dto` hands the presenter's
`spoken_summary` a plan only when the flag is False; `visual_plan_digest` stays sealed as data for
4.3's drill, unread by the presenter now. Round 3 (R15): the main query producer
(`_answered`/`_visual_dataset`, `~:5873`) HAS the `FinancialPlan` that drew the chart in its own
local scope and now stamps `metric_definition` there directly, so `stream()`'s back-fill never
runs for it and an ordinary, single-turn VOICE query keeps its 4.4 noun («Restaurantes fue la
categoría con mayor gasto…», «…por categorías»). The other four producers (`_how_many_payees`,
`_compare_periods`, the ratio branches, `_compare_salary_history`) genuinely run no single
`FinancialPlan` of that shape, stamp nothing, and stay on the back-fill — a chart that follows one
of those, over whatever plan was previously in view, speaks only the generic desglose line, by
design, not as an unclosed limit. **Hardened, 2026-09-11 (Task 2, Wave 3 hardening):** a
week-grouped chart's explanation no longer says «días» — D-4.5-3 is RETIRED, above; the noun now
comes from the sealed `group_by`, not a guess off the label's own shape.

## 8. Performance

Once banking has answered, the answer writer and the visual planner are independent; they run
concurrently on the same verified result. Objectives, each one a test:

- a presentation-only change makes **zero** banking calls (assert on the banking fake's call
  count) — `test_a_presentation_only_restyle_makes_zero_banking_calls_and_no_policy_call`, both
  channels (4.4);
- a deterministic presentation change is near-immediate — no model call on that path — the same
  test's policy-call count (4.4);
- the chart never delays the *written* answer — §6 makes `spoken` itself a function of the
  accepted chart for a charted turn, so a charted turn's speech necessarily waits for it by
  design; an uncharted turn's speech never waited for a chart in the first place;
- the visual planner keeps Wave 3's budget; on expiry the floor is shown —
  `test_a_non_positive_budget_is_refused` and `test_the_three_failures_are_told_apart` (Wave 3);
- adding the planner never delays the first visible sign of progress —
  `test_the_first_frame_is_a_stage_and_arrives_before_the_policy_is_consulted` (4.4).

**The gate is stated against Wave 3's measurement**, not a number invented here: a follow-up turn's
P95 may not exceed Wave 3's measured single-turn P95 plus the presentation-only path's own cost, and
that cost is measured before it is promised. **Measured, 10 September 2026 (fake providers):** the
gateway's follow-up-turn benchmark reports `followUp:inputStartToTurnCompletedMs` P50 = P95 = 16 ms
over 20 samples (`apps/voice-gateway/evaluation/RESULTS.md`); the gate reads Wave 3's real-provider
single-turn P95 (5,076 ms, 17 August 2026) plus this fake-provider row; the real-provider follow-up
row itself is pending the operator's run (Task 9's runbook).

**Hardened, 2026-09-11 (Task 7, Wave 3 hardening, M-1/M-3/M-4/M-9).** The paragraph above summed two
numbers that were never comparable — a real-provider P95 measured on Linux against a fake-provider
P95 measured on Windows, on a path where the fake provider never restyles a chart — under the word
"measured", to 5,092.250 ms. `apps/voice-gateway/evaluation/RESULTS.md`'s §8 now states the two
quantities separately, each with its own provenance (OS, date, provider class), says plainly they
are not addable and not comparable, and names what a real gate needs: a same-machine,
same-provider-class pair. A new script, `apps/orchestrator/scripts/latency_gate.py` (stdlib only),
computes the gate from two such numbers and refuses to compare across provider classes rather than
silently mixing them; `benchmark_e2e.py` gained `--follow-up-pcm-file` so a real-provider follow-up
row is now producible at all (it never was before this task — the follow-up turn's audio was always
UTF-8 text standing in for PCM16). **Task 9's own runbook status:** the runbook
(`docs/superpowers/plans/2026-09-05-visual-planner-shadow.md`'s own Task 9) is now runnable end to
end — every command in it exists and reaches the setting it names (M-3: the wrong default model, a
no-op env var, a nonexistent `--mode` flag, and a dead compose key were each fixed or rewritten to
describe the real mechanism) — but **it has never been run.** The GPU measurement itself is the
user's own action, standing authorisation given separately; this document states the runbook is
ready, not that a number exists. The `visual_planner_budget_seconds` config comment no longer claims
to be "set from the P95 of `latency_ms` in the evaluation, not chosen" (M-4): it now says plainly
that the 6.0 s figure is a guess, not a measurement, and that the method is right-censored — a run
made *at* budget B can never report a P95 above B, so "measuring" the budget from such a run would
only ratify the guess.

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

**Hardened, 2026-09-11 (Task 8, Wave 3 hardening).** "Zero text/chart disagreement" above was an
invariant with no test behind it (B-5) — nothing compared what the explanation's prose said against
what the sealed chart actually carried. `tests/support/episodes.py`'s harness now has one:
`chart_text_agrees`, a new episode-turn field wired to `_chart_text_complaints`, which compares the
last `FinancialPlan` a turn actually ran (`ran[-1].group_by`) against the chart's own sealed
`VisualContext.group_by` — never a second reading of the prose, so it cannot be fooled by an
explanation that merely sounds consistent with itself. It fails (never passes vacuously) whenever
the turn has no chart or no `FinancialPlan` — a comparison, a ratio, a donut with no direct plan
lineage — so `chart_text_agrees: true` is a claim that must be earned per turn, exercised for real
in episode 34's «por semanas» row (WEEK vs WEEK). Two more harness kinds landed alongside it in the
same task: `explanation_says` (a case-insensitive substring check on the explanation's own text) and
`chips`/`no_chips` (what the answer offered as its next restyles) — closing the blind spot where a
turn's `route`/`ran` assertions passed while its actual words or offered chips went unchecked.

## 10. Incremental delivery — each sub-wave is a plan of its own

| sub-wave | delivers | done when |
| --- | --- | --- |
| **4.1 Context and classification** | `VisualContext` extended (§4); `VisualFollowUpKind` and the deterministic readers (§5); `Step.VISUAL_FOLLOW_UP`; invariant tests | every follow-up kind is classified and recorded; no visible change yet |
| **4.2 Continuous visual conversation — done (3b, 4.2b)** | presentation patches through the verifier with `source="patch"`, on a context the server has sealed (3b); reshape linkage `derived_from`; the corpus's episode reshaped, regrouped and restyled in §9's own words (4.2b) | done — `evaluation/conversations/32-visual-continuation.yaml` runs every turn end to end **with the
stub planner and the fake ledger** (M-6, Wave 3 hardening — the corpus harness's own docstring says
so, and every claim site that could be read as "verbatim against the real service" now says so too)
with the banking-call assertion: turn 2, «Separa transporte público y gasolina.», re-queries the plan in view (one call, `derived_from: true`) instead of a planning-dataset stand-in; turn 3, «Ahora compáralo por meses.», regroups it into a month-by-month series instead of the comparison reader's refusal; turns 4-5 restyle a bar by number (turn 3's own chart is a line, which refuses an orientation change) and by «el gráfico anterior», each an effective, verified change. Turn 1 stays the planning-dataset anchor sentence — `window_words.py` excludes «este verano» by design, so nothing here could give it a window without guessing. Turns 4-5 of §9's own sketch — a drill-down into a split category — are 4.3's. **Fix wave, I-4:** in §9's own order the restyle bullet (§11) is refused right after turn 3's month line, with no chart between them for a bar to be turned — episode 32 reaches it only because turn 4 names a bar by number first; the scenario in §9's own sequence closes once 4.3 leaves a chart behind for «el gráfico anterior» to resolve to |
| **4.3 Financial drill-down — done** | reference resolution over result labels; bounded drill-down plan; movements linked to evidence; "back" | done — turns 4-5 pass verbatim in episode 32; an ambiguous reference asks (`DRILL_AMBIGUOUS`, harness route `CLARIFY`); the drill's chart is a merchant bar deriving from the chart it drilled |
| **4.4 Multimodal continuity — done** | `chart_id`/`visualTurnId` on voice events; the chart-derived spoken summary; the multimodal cases | done — four of the five multimodal cases run in episode 33 (a, b, d, e); the fifth (c, the interruption) is the gateway's own protocol cancellation test; full-turn latency measured with fake providers, the real-provider row pending the operator's run |
| **4.5 Explainability and product — done on the code side** | `VisualExplanation`; «¿Por qué este gráfico?»; the video's polish | done on the code side — the §11 scenario's code path is proved by episodes 32/33 **with the stub
planner and the fake ledger** (M-6, Wave 3 hardening; `docs/superpowers/plans/2026-09-11-visual-explainability.md`); the on-camera run is the operator's |

Sub-waves 4.1-4.2 are worth landing on their own: they make the corpus see charts and make every
visual follow-up verified, without a single new product surface.

## 11. Acceptance scenario

Complete when this runs end to end, on camera:

- By voice: «¿En qué he gastado más este verano?» — FinGuard answers by voice, shows the ranking, and
  discreetly explains why bars. Episode 32's turn 1 anchors on «¿Cuánto gasté el mes pasado?» instead,
  because `window_words.py` excludes a season on purpose. **The voice answer and its chart are
  landed, 4.4** (episode 33 turn 1: VOICE, charted, `spoken: true`); **the discreet explanation is
  landed, 4.5** — episode 33 turn 1 also asserts `explanation: true`, and episode 32's why-turn right
  after turn 1 answers it in text, both from `explain_chart` alone.
- By text: «Separa transporte público y gasolina.» — the summer and the account are kept, the data is
  re-queried, the chart updates, `derived_from` points at the first chart. **Landed, 4.2b** —
  episode 32 turn 2, verbatim; the window kept is turn 1's own (last month, not a summer — the same
  reason as above), one banking call, `derived_from: true`.
- «Ahora compáralo por meses.» — the query becomes a time series and a suitable chart is chosen.
  **Landed, 4.2b** — episode 32 turn 3, verbatim: a month-by-month line, one banking call,
  `derived_from: true`, where this reader used to refuse as an incomplete comparison.
- «¿Por qué agosto es tan alto?» — August is resolved inside the previous result, the responsible
  movements are queried, the answer carries evidence. **Landed, 4.3** — episode 32 turn 4, verbatim:
  «agosto» resolves against the month line's own label rather than reaching the planner, the plan
  turn 3 left in view narrows to that bar and regroups by merchant, clamped to the days the harness's
  fixed today had actually seen (2026-08-01..2026-08-05, never the whole calendar month); one banking
  call, `derived_from: true`, its chart a merchant bar. **Unchanged by 4.5** — this turn's own
  drill answer carries an `explanation` field like any other charted answer, but nothing about the
  drill itself changed.
- «Pon el gráfico anterior en horizontal.» — the previous visual context is recovered and only its
  presentation changes, with zero banking calls. **Landed, 4.2b** — episode 32 turn 5 at the time
  (now turn 7, since 4.3's own Task 5 inserted the drill's two turns ahead of it), verbatim:
  «el gráfico anterior» resolves to the chart the prior restyle just appended, zero banking calls,
  an effective change. **Fix wave, I-4:** landed on a bar named explicitly, not on §9's own order —
  probed in that order, right after turn 3's month line with no restyle between, «el gráfico
  anterior» is that line and this turn refuses (orientation is a bar's concern, not a temporal
  series's), which is why episode 32 inserts turn 4, «Pon el primer gráfico en vertical.», naming a
  bar by number before this bullet's own wording can land on one. The ordered scenario itself closes
  only once 4.3's drill-down turns leave a chart of their own behind for «el gráfico anterior» to
  resolve to. Now that those turns exist: probed right after them, in §9's own order, «el gráfico
  anterior» resolves onto the drill's own merchant bar — already a horizontal bar (the dimension on
  the y axis) — so this exact wording is a no-op there, `VISUAL_UNCHANGED`; «en vertical» is the
  effective change instead, which is why episode 32 turns the first chart by number rather than
  reusing §9's own wording for that turn, and only reaches §9's own «en horizontal» wording one turn
  later, verbatim, against the bar that turn just made vertical. **Unchanged by 4.5** — probably
  nothing beyond the explanation itself: every restyle in this sequence now also carries an
  `explanation` field, but no restyle mechanics moved.

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
- **No seal that outlives its own place.** A seal seals a holder, a conversation and a position, not
  only a holder and a byte string — a genuine seal replayed from another conversation, or moved to
  an index it was never issued for, is exactly as untrusted as a tampered one (§4, Task 1, Wave 3
  hardening, A-1). Nobody's chart is trusted merely because the signature over its bytes still
  checks out.

**Hardened, 2026-09-11 (Wave 3 hardening) — re-confirmed and what still stands as refused.** Every
bullet above still holds exactly as stated; nothing this wave landed loosened one of them — the
seal's four bindings, the drill's narrowed plan, the honest exclusion/season/bare-word asks, the
group_by-carried noun, and the two-number latency gate are all additions on top of these refusals,
never exceptions to them. One thing the seal still does not bind, named by Task 1's own review
rather than left implicit: a *duplicated* context (the same `chart_id` sealed twice in one echoed
list) is refused before the trust gate ever runs, by `Conversation.__post_init__`'s own
chart-id-uniqueness invariant (a 422) — not by `verify_context` itself. The plan's own wording
implied the seal would be what catches a duplicate; in practice a stronger, earlier check does, and
the seal's own four bindings are proven instead by reorder and drop-middle, the shapes where a
*distinct* context moves to an index its seal does not name. Still refused, unchanged: no cross-device
trust; no expiry beyond key rotation; no server-side store. The loopback default is not a trust
boundary either way (A-13) — restated here rather than left only in `docs/next.md`, since a reader
of this design's own refusal list should not have to go looking for it elsewhere.
