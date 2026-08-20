# ADR-011 — A chart carries encodings, never figures

**Status:** accepted, planned
**Date:** 2026-08-18
**Numbering:** 010 is real-time voice. 009 is holdings and layered categorisation. 001–007 are in
vision.md.

## Context

Phase 4 was written as "implement the visual planner, generate Vega-Lite, validate the
specifications". It stayed unbuilt while the assistant grew the capabilities a chart is for:
comparisons of two periods, ratios, cumulative totals, frequency, recurrence, group rankings and
breakdowns by category, merchant, counterparty and account. Today every one of those is answered as
prose plus a table, and the web client draws CSS bars over a fixed endpoint because
`apps/web/src/Finances.tsx` declined to install a charting library ahead of this decision.

Two dangerous shortcuts stand at opposite ends.

The first is to make the chart a closed mapping — temporal grouping means a line, a category means
bars, everything else means nothing. It is trivially safe and it is the wrong product: a holder asks
to see a share, a stack, a top three, two periods on one axis, the days of the week where the money
goes. A visualisation layer that answers "no" to most of those is a report generator with a chat box
in front of it.

The second is to let a language model emit a finished chart. Full expressiveness, and a second
surface on which a figure can be invented — a spec with `"value": 1840` in it is a number no ledger
produced, rendered in the same typeface as one that a ledger did.

The resolution is that these two risks are not on the same axis. **What a chart looks like** and
**where its numbers come from** are separable, and only the second is a safety property.

## Decision

### 1. The chart vocabulary is open; the data binding is closed

`ChartSpec` is a typed domain value that describes marks, axes, series, stacking, ordering, limits,
units and multiple views. It is deliberately not restricted to a list of blessed shapes: any
encoding the grammar can express and the result can supply is expressible.

`ChartSpec` carries **no measured value**. Every encoding is a reference to a field of the typed
result that the plan already executed and the verifier already accepted:

```text
FinancialPlan / ComparisonPlan / RatioPlan
    ↓ executed and verified, exactly as a question is
typed result  (groups, periods, totals, counts, movements)
    ↓
ChartSpec: encodings that NAME fields of that result
    ↓ presenter, in infrastructure
Vega-Lite + the result rows, bound at the edge
```

A spec containing a numeric literal in a data position is rejected. This single property is what
buys the open vocabulary: a badly chosen encoding can be ugly, cluttered or a poor way to read a
question, and it cannot state a figure the ledger did not state. Domain scale bounds — an axis
minimum, a currency unit, a percentage — are declared as kinds, not as values.

### 2. Three sources decide the shape, in this order

**A derived default.** A pure function from plan and result to a reasonable `ChartSpec`, with no
model call, so an ordinary turn is charted for free. Every capability that already exists gets its
own default rather than collapsing into bars: cumulative reads as an area, frequency as a
day-of-week distribution, recurrence as a cadence, a comparison as two series on one axis, a ratio
as its two operands with the derived figure beside them, a group ranking as ordered bars honouring
the `order_by TOTAL DESC` and `group_limit` the plan already carries.

**The holder's words.** "mejor en barras", "apílalo por cuenta", "ponlo en porcentaje", "solo el top
3", "júntame los dos meses en el mismo eje". Here a model emits an **encoding patch** — never a
chart, never data — which is validated against the schema and against the fields the result
actually has, then merged by code onto the current spec. This is the same shape as the follow-up
patch in the planner: the model says what changed, code owns the merge.

Two of the three reformulations the old roadmap promised are already built. "muéstralo por semanas"
and "compara con junio" change the *plan*, not the chart, and the chart follows from the new result
with nothing added.

**The screen.** A holder retitles, reshapes and reorders a chart in "Mis métricas", and what they
change is stored.

### 3. Vega-Lite is the grammar, and it lives in infrastructure

Because the vocabulary is open and reconfigurable, hand-writing a renderer would be the expensive
half of this phase. Vega-Lite already is that grammar; `vega-embed` is the part nobody here has to
maintain.

The domain does not import it. `ChartSpec` is protocol-free and a presenter in infrastructure emits
Vega-Lite, per §4.4. This is where the presenter-per-channel item deferred in `next.md` lands: voice
was the second consumer of the answer writer, the chart is the third, and an abstraction shaped by
three cases is a design rather than a guess.

### 4. "Mis métricas" saves structure, and the structure is the plan

The orchestrator gains its first persistence: an `assistant` schema of its own in the existing
PostgreSQL instance, with its own migrations. Banking does not learn assistant concepts and its
migrations do not grow.

```text
saved_metric(id, holder_id, title, plan_json, encoding_json, created_at)
```

- **No rows, no totals, no figures.** Only the plan and the encoding.
- A **relative period stays relative**: "este mes" saved in August is October's month in October. An
  absolute range stays absolute, because that is what it meant.
- Opening the screen **re-executes** each saved plan through the same path a question takes —
  verifier, scope check, banking — so a saved metric can never read what a live question could not.

### 5. A stored plan is untrusted input

This is the invariant the feature turns on. A row in `saved_metric` is data that once passed
validation, not authority:

- `holder_id` comes from the credential on every read, never from the request or the row.
- Row-level security on the table, keyed the way the ledger's policies already are.
- The plan's account scope is **re-checked at execution**, not trusted because it was checked when
  it was saved.
- `plan_json` is re-parsed into the typed plan and rejected on failure, including plans written by
  an older schema version.

Without this, "save a metric" is a route to reading rows a question would refuse.

### 6. What this release does not do

Voice may describe a figure and may not save or reshape a metric by speaking; recognition turning
noise into a stored query is a paper cut, but "guárdame esta métrica" has no confirmation boundary
and the phase does not invent one. Charts are not shared between holders. There is no scheduled
refresh, no alerting on a saved metric, and no export.

## Delivery order

1. `ChartSpec` as a typed domain value, plus the no-literal-values property test.
2. The derived default per existing capability, over the conversation corpus.
3. The presenter and the Vega-Lite emission, with schema validation in CI.
4. The web client renders a chart in the chat.
5. The encoding patch from the holder's words, validated against the result's fields.
6. The `assistant` schema, migrations and row-level security.
7. `POST/GET/DELETE /v1/metrics` behind the existing scopes.
8. The "Mis métricas" screen: save from a turn, re-execute on open, retitle, reshape, reorder.

Each step leaves a runnable slice. Steps 1–4 are shippable without any of 5–8.

## Acceptance for phase 4

- **0** specs carrying a numeric literal in a data position, on the whole corpus.
- **0** specs invalid against the schema or naming a field the result does not have.
- **0** charts whose values disagree with the figure in the answer text — the same reconciliation
  the evidence already passes.
- **0** answers with two or more rows that offer no chart. This is the bar against quietly becoming
  restrictive again.
- **0** charts of a single row, which restate the number above them.
- **0** saved metrics readable by another holder, mirroring the cross-account containment tests.
- A re-executed saved plan produces the same outcome class as the question that created it, over the
  deterministic seed.
- An encoding patch that names an absent field is refused rather than dropped silently.

## Consequences

The orchestrator becomes a stateful service, which it was not: one schema, one table, one set of
migrations and a backup story it did not previously need. A charting library enters the web bundle.

In return the visualisation layer is expressive without being a place where a number can be
invented, the capabilities already built stop being answered only as prose, and a saved metric is a
question that keeps answering itself rather than a screenshot that starts ageing the moment it is
taken.
