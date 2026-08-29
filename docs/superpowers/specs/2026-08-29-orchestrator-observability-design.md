# Observability for the orchestrator: decisions, not just durations

**Date:** 2026-08-29 · **Scope:** `apps/orchestrator` only · **Status:** approved design, not yet built

## What this is for

A trace today would answer "why did this request run and how long did it take". That is not the
question anybody has asked in this repository. Every defect found in the last week was a figure
that was real and about the wrong question: a window the model invented and nothing overruled, a
scope phrase read as a grouping instruction, a clarification that dropped the metric the sentence
had stated. None of those is visible in a latency waterfall.

So the unit of observation here is **a decision**, and the fact worth recording about a decision is
**whether it changed the answer**. `turn.window` — the one log line that made today's window
defects readable after the fact — already does this for a single field:

```
explicit = últimos 3 meses     planned = últimos 5 meses     inherited = últimos 5 meses
```

This design generalises that line to every point in the pipeline that decides something, and makes
the result queryable in Grafana both as one turn and as a rate.

## The constraint that shapes everything

**Nothing the holder wrote leaves this service.** Not the question text, not a Bizum's concept, not
a contact's name, not a movement's description, not a raw account id. This is already the rule in
`observability.py`, it is already enforced by a test that drives a real turn and asserts the
sentence never appears in what was emitted, and it applies to spans and span attributes without
exception.

That rule is affordable because the interesting facts are all in the service's *own* vocabulary: a
window is a period name and a count, an outcome is an enum, a grouping is a `GroupBy`, a recogniser
is a function name. You can see that a turn lost its window and exactly where, without ever seeing
what was typed.

**Every section below inherits this constraint, and section 1 is where it is enforced.**

## Section 1 — The decision record

A plain value recorded as the turn runs, exported once at the end. No OpenTelemetry import reaches
the application or the domain.

```python
@dataclass(frozen=True)
class Decision:
    step: str            # "explicit_facts.enforce", "capability_guard", "planner"
    verdict: str         # "overrode" | "agreed" | "claimed" | "declined" | "failed"
    field: str | None    # "window", "metric", "group_by", None for whole-turn decisions
    was: str | None      # the service's own vocabulary, never the holder's words
    now: str | None
    changed: bool        # the impact: did this decision alter what the turn answered?
```

`changed` is the whole point. A rule that fires on every turn and alters nothing is noise; a rule
that fires rarely and always changes the answer is where the risk lives. The rate of
`changed=True` per step is the headline metric of this system.

A `TurnRecord` holds an ordered tuple of `Decision` plus the turn's outcome. Call sites reach it
through a `ContextVar`, the way `request_id` already works and for the reason `observability.py`
already argues: a recorder passed as an argument is a dependency every layer must declare in order
to be silent.

**Testability is the reason this layer exists at all.** The record is a value, so a unit test can
assert "this turn recorded that `enforce` overrode the window" without a collector, an exporter or
a running Grafana. Six times in the last week this repository has found an instrument that was
green because it had stopped executing the code it measured. Instrumentation that only exists when
something is listening would reproduce that on day one.

**Privacy test:** drive a real turn with a distinctive sentence, assert none of its words appear in
any recorded `Decision`. Same shape as the existing log test, extended to the record.

Files: `observability/decisions.py` (new). `observability.py` becomes the package
`observability/` — `logging.py` keeps today's contents unchanged, `__init__.py` re-exports so every
existing import keeps working.

## Section 2 — The points that decide

Where `Decision`s are recorded. The pipeline is already documented in
`AskFinancialQuestionHandler`'s own docstring as
`question → capability guard → planner → plan verifier → ledger → sentence`; these are the seams
inside it that make a choice.

| step | what it records | `changed` means |
| --- | --- | --- |
| `intent` | deterministic vs model vs hybrid, and any fallback | the model disagreed with the deterministic router |
| `capability_guard` | allowed / refused, and the family | the turn was refused before the model ran |
| `recogniser` | which deterministic recogniser claimed the turn, or that none did | the model was never called |
| `planner` | outcome, model, retry on invalid structure | — |
| `explicit_facts.enforce` | per field: what the sentence stated vs what the model produced | the sentence overruled the model |
| `inherited_window` | whether a continuation's silence was filled from the previous turn | a window was inherited |
| `metric_without_a_bucket` | whether the pair was settled | a refusal became an answer |
| `plan_verifier` | agreed / disagreed | the plan was rejected after being built |
| `ledger` | rows, and whether the run crossed accounts | — |
| `outcome` | the `PlanningOutcome`, and whether a figure was shown | — |

Ten steps, not forty. A seam earns a row here only if a defect has ever hidden in it or could.

Files: `application/queries/ask_financial_question.py` (the majority),
`domain/services/turn_interpreters.py`, `infrastructure/planning/llm_query_planner.py`.

**Risk:** this is the section that touches the 7000-line file. Recording must be one call per seam
with no branching of its own, or the file gets harder to read in exchange for making it easier to
watch — a bad trade.

## Section 3 — The OpenTelemetry adapter

The only module that imports OpenTelemetry.

- SDK set-up from the composition root, beside `configure_logging`, driven by settings
  (`otel_enabled`, `otel_endpoint`). Disabled by default: a test run and a developer's laptop must
  not need a collector.
- Resource attributes from settings that already exist: `service.name=orchestrator`,
  `service.version=build_sha`, `deployment.ref=build_ref`. `/version` already proves these are
  stamped into the image.
- `TurnRecord` → one span per step with the decisions as span events, plus counters
  (`decisions_total{step,verdict,changed}`) and a histogram for planner latency. **Both from the
  same record**, so a rate and a trace can never disagree.
- `opentelemetry-instrumentation-fastapi` for the inbound span.
- `trace_id` and `span_id` stamped into the existing JSON log lines, so a log line and a trace find
  each other without a second correlation scheme.

Files: `observability/otel.py` (new), `bootstrap/main.py`, `bootstrap/config.py`, `pyproject.toml`.

## Section 4 — The outbound seam

Two things that are the same seam.

**`opentelemetry-instrumentation-httpx` cannot be used here.** This service depends on `httpx2`,
not `httpx` — starlette requires it, `import httpx` raises `ModuleNotFoundError`, and
`tests/test_dependency_provenance.py` holds three tests explaining why. The instrumentation wraps a
module that is not installed.

**And the seven banking clients each construct their own `AsyncClient`.** There is no shared place
to add anything.

That absence is also why a promise in the codebase is unkept: `request_log.py` says it honours the
inbound `X-Request-Id` *"so a request crossing from the web client to here to banking can be
followed end to end"*. It is honoured, stamped on local records and echoed back — and then never
forwarded. The only header any outbound call sends is `Idempotency-Key`, on payments.

So: one client factory in `infrastructure/banking/`, used by all seven, that sets the request-id
header and opens a client span. One seam, both problems.

Files: `infrastructure/banking/client.py` (new) and the seven `http_*.py`.

## Section 5 — The stack

Separate containers, behind a compose profile so `just app` does not pay for them.

- **otel-collector** — OTLP in, Tempo and Prometheus out.
- **Tempo** — traces.
- **Prometheus** — metrics.
- **Grafana** — datasources provisioned from files, no click-ops.

**Loki is deliberately not in the first cut.** The decision log lives in span events, which Tempo
holds and Grafana queries; the raw JSON logs stay on stdout where they are already greppable. Loki
plus a shipper is two more moving parts for a view nobody has asked for yet. Add it when somebody
wants to grep raw logs *in Grafana* specifically.

Files: `compose.yaml`, `ops/observability/` (collector, Tempo, Prometheus and Grafana
provisioning).

## Section 6 — The panels

A provisioned dashboard, in the repository, not drawn by hand in a browser:

- outcome mix over time (`PLANNED` / `NEEDS_CLARIFICATION` / `UNSUPPORTED` / `INVALID_DOMAIN`)
- **override rate per step** — how often each deterministic rule corrects the model, and how often
  it changes the answer. This is the panel the whole design exists for.
- how many turns the model never saw, because a deterministic recogniser claimed them
- planner latency percentiles, by model
- a turn's full waterfall, reachable from any of the above

Files: `ops/observability/dashboards/`, `docs/architecture/`.

## Sequencing

| wave | sections | why |
| --- | --- | --- |
| 1 | **1** (record) · **4** (outbound seam) · **5** (stack) | disjoint files; 1 blocks the rest |
| 2 | **2** (call sites) · **3** (adapter) | both need the record; different files |
| 3 | **6** (panels) | needs something to draw |

## What this design refuses

- **No shared package across services.** Duplication between `banking-api` and `orchestrator` is
  deliberate here. This lives inside the orchestrator, which is the only service that needs it.
- **No OpenTelemetry import outside `observability/otel.py`.** The domain and the application
  record decisions; one adapter exports them.
- **No sentence, ever.** See the constraint above.
- **No decision recorded that nobody would query.** Ten seams, chosen because a defect has hidden
  in them.
