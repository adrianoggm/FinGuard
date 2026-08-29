# Watching the orchestrator decide

**Scope:** `apps/orchestrator` · **Design:**
`docs/superpowers/specs/2026-08-29-orchestrator-observability-design.md` · **Section 6**

The design's first line is that the unit of observation is a *decision*, not a duration. This
document is about the last section of it — the panels — and about the thing that had to be true
before any panel was worth drawing: that a real question produces a real trace and a real counter
sample, and that the two find each other.

## The seam that had not been crossed

Sections 1 to 5 were built and merged separately, and each verified its own half:

| section | what it proved | with what |
| --- | --- | --- |
| 2 | the ten seams record | no exporter running |
| 3 | `TurnRecord` becomes spans and counters | an **in-memory** exporter |
| 5 | collector → Tempo/Prometheus carries OTLP | **hand-rolled** OTLP, no application |

Nothing had ever carried a byte produced by an actual question from end to end. A gap between two
green halves is where the seventh green-but-dead instrument would live, so it was closed first.

**Result: it works, and nothing had to change to make it work.** No Python and no `compose.yaml`
edit was needed. The four requirements, checked against a live stack:

- a `turn` span per question, with a child span per decision carrying `step` / `verdict` /
  `changed` — confirmed, 9 to 17 spans per turn depending on how far the turn got
- `decisions_total{step,verdict,changed}` and `turns_total{outcome}` in Prometheus — confirmed,
  all ten `Step` values and all four `Verdict` values observed
- **the exemplar link** — confirmed, and this is the one that had never been tested with both
  halves present. Every exemplar on `decisions_total` carried a `trace_id` that resolved to a real
  trace in Tempo, and its `span_id` resolved to *the exact decision span for that step*. 33 of 33,
  none missing.

One thing to know before repeating that check: Tempo's `/api/traces/{id}` speaks OTLP proto-JSON,
where `spanId` is a *bytes* field and therefore base64 — not the hex that the search API and the
exemplar labels use. Comparing the two without decoding makes every span look like a miss, which
is a false negative shaped exactly like the failure you are looking for.

## Running it

The stack is on its own compose profile and nothing outside it depends on it:

```sh
docker compose --profile observability up -d --wait
```

Grafana is on **3001** (the web client owns 3000), Prometheus 9090, Tempo 3200, the collector 4317
and 4318. Verify the profile is genuinely inert with `docker compose config --services` — with no
profile selected it must list none of the four.

Then point an orchestrator at it. **`otel_enabled` is false by default and that default is
load-bearing**, so it has to be turned on explicitly:

```sh
ORCHESTRATOR_OTEL_ENABLED=true \
ORCHESTRATOR_BANKING_BASE_URL=http://127.0.0.1:8001 \
ORCHESTRATOR_DATABASE_URL=postgresql://finguard_orchestrator:finguard_orchestrator@127.0.0.1:5442/finguard_orchestrator \
ORCHESTRATOR_OLLAMA_BASE_URL=http://127.0.0.1:11434 \
uv run --directory apps/orchestrator uvicorn finguard_orchestrator.bootstrap.main:app --port 8010
```

`otel_endpoint` needs no override on the host: it defaults to `http://127.0.0.1:4318`, which is
where compose publishes the collector's OTLP/HTTP port, and that is why the default was chosen.
Inside compose it would be `http://otel-collector:4318`.

Startup is confirmed by one log line, and log lines then carry trace context:

```json
{"event": "otel.configured", "endpoint": "http://127.0.0.1:4318", "service": "orchestrator"}
{"event": "request", "trace_id": "527a6f…", "span_id": "6c4630…", "path": "/health", "status": 200}
```

**A turn that decided nothing is not exported.** Every HTTP request opens a recorder, `/health`
included, and a `turn` span per readiness poll would bury the turns anybody wants to read.

## The panels

One provisioned dashboard, `ops/observability/grafana/dashboards/orchestrator-decisions.json`, uid
`finguard-orchestrator-decisions`. It is a file in the repository and never a thing drawn in a
browser: the provider in `../provisioning/dashboards/dashboards.yaml` already points at that
directory, and datasource uids are pinned so the JSON's references resolve on every machine.

| panel | reads | answers |
| --- | --- | --- |
| Outcome mix over time | `turns_total{outcome}` | what turns ended as, and when |
| Outcome mix | `turns_total{outcome}` | the same totalled, exactly |
| **Override rate per step** | `decisions_total{verdict="overrode"}` | how often a rule corrected the model |
| **Change rate per step** | `decisions_total{changed="true"}` | how often that altered the answer |
| Decisions by step, verdict and impact | `decisions_total` | the counts behind the two rates |
| Overrides and changes over time | `decisions_total{changed="true"}` | when, plus the exemplars |
| Turns the model never saw | `decisions_total{step="recogniser"}` / `turns_total` | how much never reached the planner |
| Claimed by a recogniser, or planned | `decisions_total{step=~"recogniser\|planner"}` | the split over time |
| Request duration · POST /v1/questions | `http_server_duration_milliseconds_bucket` | the HTTP request, **not** the planner |
| Recent turns | Tempo, `{name="turn"}` | a waterfall without an exemplar to start from |

The waterfall is reachable two ways. The primary one is the exemplar: the dots on the three
`increase()` panels each carry one turn's `trace_id`, and clicking one opens that turn in Tempo.
That route starts from a rate you were already reading and lands on one of the turns that produced
it. The "Recent turns" table is the route for when you have no particular rate in mind.

### Two ways these panels could have lied, and what was done about them

**`increase()` cannot see what a pushed counter was born holding.** These counters are remote
written every 60 s, not scraped, so a series' first sample in Prometheus already carries whatever
the process had counted before that first push. `increase()` measures growth from the first sample
inside the window, so everything before it is invisible. Measured on this stack: with two `intent`
overrides already recorded, `increase(decisions_total{verdict="overrode"}[30m])` returned **0**,
and an override-rate panel built on it would have shown a confident, healthy-looking `0.0%`.

The two override panels therefore use `last_over_time(...[$__range])`, which reads the cumulative
value itself — exact, no extrapolation, no lost birth value. The over-time panels keep `increase()`
because a trend is inherently windowed and shape is what they are for.

**A division with a missing numerator returns no row, not zero.** A step that has never overridden
has no `verdict="overrode"` series at all, so `overrode / total` silently drops it from the panel —
and a step missing from an override panel reads exactly like a step that is behaving. Every rate
here is 0-filled as `X or 0 * Y` so that a step which never overrides shows a measured `0.0%`.

### The time picker decides which processes are counted

`instance` is a per-process UUID, so restarting the orchestrator starts a fresh set of counters
rather than resetting the old ones, and the old ones stay for Prometheus' full 15 days. The panels
sum over `instance` on purpose — "what did this service decide" is a question about the service —
but widening the range can pull an entire earlier run into the totals. Seen while verifying this
dashboard: one override read `0.0%` at 1 h and `8.3%` at 3 h, and both were correct answers to
different questions. Tempo keeps 72 h, so an exemplar older than three days points at a trace that
no longer exists.

## Latency: the gap, stated rather than closed

**The design asked for planner latency percentiles by model. They are not built.**

`TurnRecord` holds no timing at all — a `Decision` is a step, a verdict, two values and a bool — so
the only two ways to get planner percentiles are a second timer inside the planner or a
`spanmetrics` connector in the collector. Both are a second source of truth about one turn, which
Section 5 ruled out in writing, and a figure that is real and about the wrong question is precisely
the defect class this design was built to catch. Section 3 reached the same conclusion and said so.

What genuinely exists and is drawn: `http.server.duration` from the FastAPI instrumentation, which
is the whole HTTP request — planning, the ledger, the sentence and the wire. The panel says so in
its own description, because a panel labelled "latency" next to nine decision panels will be read
as the planner's unless it argues otherwise. The `turn` span also carries a real duration in Tempo,
so any single turn can be broken down in the waterfall; what does not exist is a *percentile* over
turns, because that needs an aggregation the record cannot feed.

**Recommendation, not implemented here.** If planner percentiles are wanted, the honest fix is a
duration on the planner's `Decision` — one field on the record that already exists, exported by
the one adapter that already exports it, keeping the design's "both from the same record" property
intact. That is a change to a shape that call sites are written against, so it belongs in a diff
somebody argues for, not in the section that draws the panels. The two rejected alternatives stay
rejected: a second timer and a `spanmetrics` connector both create a second source of truth.
