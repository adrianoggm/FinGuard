# The LLM chooses the chart, and never the figure

**Date:** 2026-09-03 · **Scope:** `apps/orchestrator`, `apps/web` · **Status:** approved design, not yet built

## Why this exists

An intermediate rubric review scored the project 69/95 and put **15 of the 26 lost points** in two
criteria this design addresses:

| criterion | score | what the review said |
| --- | --- | --- |
| **No fixed templates** | **1/10** | specs are built at runtime, but *their structure* is selected by predefined deterministic mappings |
| **Visual logic** | **4/10** | the rules pick reasonable charts, but the AI does not reason about the best one; categorical sorting and grouped comparisons are also open |

Nothing else in the rubric is worth more than four points. And the review's own conclusion is the
sentence this design has to make true as *structure* rather than as intention:

> **The LLM decides how to represent the information, but it never decides what the financial
> information is.**

## The shape this already has, one layer down

The repository has built this exact pattern for the financial plan, and the visual planner is the
same pattern over a different question:

```
LlmQueryPlanner (infra)   proposes a typed plan
plan_verifier   (domain)  checks it against the sentence, and refuses
capability_guard(domain)  refuses whole families before the model runs
derive_chart    (domain)  a pure deterministic default
```

So the design is small, because most of the parts exist. `ChartSpec` already carries `mark`,
`encodings`, `sort`, `sort_field`, `stack`, `limit`; `__post_init__` already refuses an unknown
mark, a stacked donut, duplicate channels and a `sort_field` with no `sort`; and `valid_for()`
already requires **every charted field to exist in the result**. What is missing is a port, an
adapter, a verifier for the semantic rules, and a record.

## Section A — the decision leaves the mapper

`derive_chart` is called from `_chart_for_dataset`, a **synchronous function in
`infrastructure/mappers/llm_mapper.py`**. A mapper builds transport DTOs; a model call cannot live
there. So the decision moves up, and this is a precondition rather than a tidy-up:

- **The application decides.** The handler is already `async` and already holds ports. It asks for
  the visual plan *after* banking has returned the figures, because real cardinality and sign do
  not exist until then, and they are two of the facts that most decide a chart.
- **The mapper renders what it is handed.** `_chart_for_dataset` stops choosing and receives a
  verified `ChartSpec`. It stays synchronous and stops having opinions.

## Section B — what the model sees

### The profile changes direction

Today `VisualProfile.from_spec(spec)` derives the profile **from the chart**. The new flow needs the
opposite, and this is the largest structural change in the design:

```
VisualDataset → VisualProfile → planner → VisualPlanDraft → verifier → ChartSpec
```

`from_spec` has exactly **one** caller (`llm_mapper.py:330`), so inverting it is cheap.

```python
@dataclass(frozen=True)
class VisualField:
    name: str
    type: FieldType                                    # nominal | quantitative | temporal
    role: Literal["dimension", "measure", "series"]

@dataclass(frozen=True)
class VisualProfile:
    fields: tuple[VisualField, ...]
    cardinality: int
    metric: str
    unit: str                                          # money | count | ratio | unknown
    positive: bool
    mutually_exclusive: bool
    verified_total: bool
    comparison: bool
    order_semantics: Literal["MEASURE_DESC", "INPUT", "TEMPORAL"]
```

**`role` is load-bearing, and not because of the measures.** The dataset's fields are `dimension`,
`value_minor` and `series`. There is one quantitative field — but **two nominals**, and type alone
cannot tell the planner which is the axis and which is the colour split. `role` is what says
`series` is a partition. It also settles an ambiguity that already exists: `value_minor` is titled
«Importe» in one branch of the mapper and **«Movimientos»** in another, depending on metric;
`role: measure` with `unit` carries that, and the field name alone does not.

**Three roles, not four.** A temporal dimension is `type=temporal, role=dimension`; a `timestamp`
role would be the same fact written twice, and this repository has spent two days finding defects of
exactly that shape — «entre» in two tables, «semana a semana» in two tables, `GroupBy.MONTH` spelled
in two places.

**Temporal-or-categorical is not a field.** It is `any(f.type == "temporal" for f in fields)`.

### Presentation is not a property of the data

```python
@dataclass(frozen=True)
class PresentationContext:
    channel: Literal["TEXT", "VOICE"]
    viewport: Literal["MOBILE", "DESKTOP"]
    max_visible_rows: int
```

`VisualProfile`'s docstring says *"value-free facts about a chart, safe to send in a conversation
round trip"*, and it means it: `conversation.py:427` validates it **coming back from the browser**.
So anything put in the profile ends up in a transport DTO. Viewport and channel are circumstances of
one execution, not properties of the data, and they belong outside it.

`channel: Literal["TEXT", "VOICE"]` **already exists** on the request DTO (`llm_dto.py:613`). Only
`viewport` and `max_visible_rows` are new wire surface, and they need a default so the planner does
not wait on a web deployment.

### The port

```python
class VisualPlannerPort(Protocol):
    async def plan(
        self,
        question: str,
        profile: VisualProfile,
        presentation: PresentationContext,
        *,
        budget_seconds: float,
    ) -> VisualPlanDraft | None: ...
```

**The question is an argument, never a profile field.** The model sees it — the financial planner
already does, same trust boundary — but it never enters a DTO that crosses to the web, the
conversational state that round-trips, or a log.

### What the guarantee actually is

An earlier draft of this design said *"nothing the holder wrote leaves the service"*. **That is
false**, and the repository proves it: `openai_compatible_provider.py` and `cloudflare_workers_ai.py`
exist, so with a remote provider configured the question does leave. The precise, demonstrable
guarantee is:

> The question may be sent to the configured LLM provider, exactly as the financial planner already
> sends it. It is never added to `VisualProfile`, to the conversational state returned to the client,
> or to the logs. **The visual model never receives rows, amounts, movement descriptions or dimension
> values.**

### Proving the amounts never travel

"No number appears in the payload" is untestable — the payload legitimately contains cardinality and
`limit`. Two checks instead, and the structural one is the strong half:

```python
async def test_visual_planner_never_receives_financial_values() -> None:
    result = financial_result(
        total_minor=734_291,
        rows=(VisualRow("Comida", 193_847), VisualRow("Transporte", 278_413)),
    )
    await handler.handle(question, result)
    payload = fake_visual_planner.received_request

    assert "734291" not in repr(payload)          # sentinels, not "any digit"
    assert "193847" not in repr(payload)
    assert "278413" not in repr(payload)
    assert set(payload.profile_keys()) <= {       # and the shape, which is stronger
        "fields", "cardinality", "metric", "unit",
        "positive", "mutually_exclusive", "verified_total",
        "comparison", "order_semantics",
    }
```

The structural assertion is what proves `rows`, `data`, `total_minor`, `value_minor`, merchant names
and movement descriptions cannot travel — a sentinel only proves the three values it names.

## Section C — the model proposes, the domain authorises

The port returns a **draft**, not a `ChartSpec`:

```python
@dataclass(frozen=True)
class VisualPlanDraft:
    mark: str                                  # plain str, deliberately
    encodings: tuple[ProposedEncoding, ...]
    sort: str | None = None
    sort_field: str | None = None
    stack: str | None = None
    limit: int | None = None
    rationale: str | None = None

@dataclass(frozen=True)
class ProposedEncoding:
    channel: str                               # plain str, like `mark`
    field: str
    type: str
```

Every enumerated field is a plain `str` on purpose. `ChartEncoding` validates channel and type in
its own `__post_init__`, so typing them here would move the refusal back into the adapter — which is
the whole thing this split exists to prevent.

If the port returned `ChartSpec`, `__post_init__` would **raise on a bad model output** — turning a
model error into an exception in the adapter instead of a rejection in the domain. Plain strings put
the refusal where it belongs. The repository already has this shape in `FinancialPlanDraft`, which
exists for the same reason.

`VisualPlanVerifier` is a pure domain function, `verify(draft, profile) -> ChartSpec | Rejection`. It
**refuses and never repairs**, for `plan_verifier`'s reason: a silent repair turns a disagreement
into a plausible answer.

## Section D — the rules

**Structural**, already written: constructing the `ChartSpec` gets them for free. The verifier's job
is to catch the `ValueError` and return a reasoned rejection rather than let it escape.

**The role/channel matrix**, which the types cannot express:

| role | allowed channels |
| --- | --- |
| `dimension`, nominal | `x`, `y`, `color`, `tooltip` |
| `dimension`, temporal | `x`, `y`, `tooltip` |
| `measure` | `x`, `y`, `theta`, `tooltip` |
| `series` | `color`, `xOffset`, `yOffset`, `tooltip` |

Plus: `theta` takes a measure and only with `donut`; offsets take only `series`; `series` cannot
stand in for the main dimension; **a measure never goes in `color`**; a donut needs exactly one
nominal dimension in `color` and one measure in `theta`. (An earlier draft said the dimension goes
in `theta` — backwards. In a donut the *angle is the magnitude*.)

**A comparison is grouped, never stacked — and `stack=None` does not achieve that.**

The presenter emits `if visualization.spec.stack:`, so `None` means *the property is omitted*, and
Vega-Lite's default for bars with a quantitative measure and a colour series **is to stack**. A rule
saying "stack must be `None`" would be a no-op wearing a fix's clothes, with a green test on top.
Disabling the stack without an offset gives overlapping bars, not grouped ones. Both are needed, so
the vocabulary widens:

```python
Channel = Literal["x", "y", "color", "tooltip", "theta", "xOffset", "yOffset"]
Stack   = Literal["zero", "normalize", "disabled"]
```

and the presenter translates `disabled` to an explicit `encoding[measure]["stack"] = None`.

> A comparison over a categorical dimension must be drawn as grouped series: the series takes
> `color` and the matching offset, and the measure declares `stack: null` explicitly. A comparison
> can never be stacked, because its series are not additive parts of one total.

**Sorting follows the profile, not the field type.** "Categorical sorts by value" is too broad: the
FREQUENCY chart is `ChartEncoding("x", "dimension", "nominal", "Día")` with **`sort="input"`**, and
that order is lunes→martes→miércoles. A blanket rule would have broken a working chart to fix a
different one.

> When `order_semantics == "MEASURE_DESC"` the chart must sort by the `measure` field. When it is
> `INPUT`, the model may not replace a semantic order with an alphabetical or quantitative one. When
> `TEMPORAL`, the temporal field orders it.

**A donut asserts that the parts sum to the whole**, so it requires `mutually_exclusive` **and**
`verified_total` **and** `positive`. Without those the chart claims something the data does not.

**The compile gate.** `Conversation.test.ts:41` already imports `compile` from vega-lite and compiles
three manual fixtures — so compilation exists; what does not exist is anything that walks the specs
the backend actually generates. Two distinct checks, because `compile()` will not necessarily fail
merely because `$schema` says v5 while the package is v6:

```ts
expect(spec.$schema).toBe("https://vega.github.io/schema/vega-lite/v6.json");
expect(() => compile({ ...spec, data: { values: fixture.data } })).not.toThrow();
```

The adapter emits `v5.json` today (`vega_lite.py:24`) and the client has `vega-lite ^6.4.3`. That is
one line plus a test that fails the build if it drifts again.

## Section E — the policy, and both planners at once

The deterministic planner is **not** the port's default implementation. It cannot be: computing
`changed_from_baseline` needs the model's proposal *and* the floor's, at the same time.

```python
class DeterministicVisualPlanner:                      # sync, pure, always present
    def plan(self, profile: VisualProfile) -> ChartSpec | None: ...

class VisualPlanningPolicy:
    def __init__(
        self,
        model: VisualPlannerPort | None,
        fallback: DeterministicVisualPlanner,
        verifier: VisualPlanVerifier,
        mode: Literal["deterministic", "shadow", "hybrid", "promoted"],
    ) -> None: ...
```

The floor is three things at once: **availability, fallback, and the experimental baseline.**

**`None` means abstention and nothing else.** A single value that also meant timeout and provider
failure would make the review's requested "fallback reason" unwritable. This is `PlanningOutcome`'s
own argument — *"a single boolean would collapse cases that need different answers and carry very
different risk"* — applied again:

The port returns `VisualPlanDraft | None` and raises the three failures; the **policy** is what
resolves any of them into a `ChartSpec`, because only the policy holds the floor:

| what the port does | what the policy records | what the user gets |
| --- | --- | --- |
| returns a draft that verifies | `selected_source: llm` | the model's chart |
| returns `None` | `fallback_reason: abstention` | the floor's chart |
| raises `VisualPlannerTimeout` | `fallback_reason: timeout` | the floor's chart |
| raises `VisualPlannerUnavailable` | `fallback_reason: provider_error` | the floor's chart |
| returns a draft the verifier refuses | `fallback_reason: invalid_proposal` | the floor's chart |

Every row ends in a chart. The column that matters for evaluation is the middle one.

**The rollout reuses a ladder the repository already has.** `turn_interpreters.py` runs
`deterministic → shadow → hybrid → promoted` for the intent router, with the same problem: a
probabilistic reader beside a deterministic one. The visual planner takes the same path —
`shadow` runs both and shows the floor, which is how the baseline comparison gets collected before
anything is risked.

**The budget is measured, not chosen.** An earlier draft fixed it at 1.5 s; with the planner's P50
around 3 s that would fall back more than half the time, and the planner would appear in the
architecture while deciding almost nothing. The budget is configurable and set **after** measuring
the visual prompt with a warm model.

And the earlier claim *"the response never degrades"* was too strong. The fallback guarantees a
planner failure does not remove the visualisation; **the attempt still costs latency**, because the
budget is spent before the floor answers. That is why shadow comes first.

## Section F — what is recorded

Eight fields per turn, hung off the decision record the observability work already built:

```
proposed · valid · accepted · changed_from_baseline
suitable · selected_source · fallback_reason · latency_ms
```

`selected_source` is `llm | deterministic_fallback`; `fallback_reason` is
`abstention | timeout | provider_error | invalid_proposal`.

**`changed_from_baseline` compares canonicalised specs.** Reordering encodings or changing a title
is not a different visual decision, and counting it as one would inflate the number that is supposed
to prove the planner adds something.

**And `changed` alone does not prove quality.** An earlier draft said a zero would make the planner
decorative. That is too quick: zero can mean the deterministic floor already chooses correctly, or
that the model converges with it — and a high number can mean the model is choosing *worse*. The
figure that carries the argument is:

> the share of cases where the model proposes a **valid and suitable** visualisation that differs
> **semantically** from the baseline.

## Section G — how it is measured

Three numbers, and none of them is "it worked":

- **Validity** — of the model's proposals, how many pass the verifier. Low means prompt or schema,
  not product.
- **Suitability** — whether the chosen chart is the one a person would have chosen. Needs annotated
  judgement; over the robustness corpus's 56 bases that is an afternoon, not a month.
- **Latency and fallback reason**, by the four causes.

**The 416 rewordings are a free visual-robustness test, grouped by expected equivalence.** Not every
paraphrase should produce the same chart: some add an explicit visual instruction and *should* differ.
So the property is stated the way the robustness corpus already states its own:

> Two phrasings with the same financial intent and **no explicit visual instruction** must produce
> the same canonicalised `ChartSpec`.

Measured separately: stability under equivalent paraphrase, sensitivity to explicit visual
instructions, invariance of the financial fields, and consistency of the fallback.

## Sequencing

| wave | work | why |
| --- | --- | --- |
| 1 | the profile inverted · `PresentationContext` · the decision leaves the mapper | everything else needs the direction fixed first, and none of it involves a model |
| 2 | `Stack.disabled` + offsets · `order_semantics` · schema v6 · the compile gate | the floor and the guard rails, so a planner failure lands somewhere better than today |
| 3 | port · adapter · verifier · policy in `shadow` | the model proposes and nothing is shown yet |
| 4 | measure, set the budget, promote to `hybrid` | the numbers decide the mode, not the calendar |

Waves 1 and 2 are worth landing on their own: they close the two chart defects the review names
under *Visual logic* without a single model call.

## What this design refuses

- **No raw Vega-Lite from the model.** Maximum freedom, but the validation surface goes from a dozen
  fields to a whole language, and that is where a spec that renders *and lies* gets in. With a bounded
  draft, "it cannot name a field that does not exist" is a test rather than an aspiration.
- **No second model.** The visual planner uses the model already loaded, with a different prompt.
  Loading a second one evicts `qwen3.5:9b` from an 8 GB GPU and pays a cold load past the harness's
  120 s timeout — measured this week, not assumed.
- **No repair in the verifier.** It returns a rejection with a reason. A repaired plan is a
  disagreement nobody can see.
- **No visual decision in a mapper.** Not even a small one.
- **No amount reaches the visual model.** Ever, under any provider.
