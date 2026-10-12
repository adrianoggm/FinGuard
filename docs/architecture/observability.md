# Watching the orchestrator decide

**Scope:** `apps/orchestrator` · **Design:**
`docs/superpowers/specs/2026-08-29-orchestrator-observability-design.md` · **Section 6, and the two
things it could not draw**

The design's first line is that the unit of observation is a *decision*, not a duration. This
document is about the last section of it — the panels — and about the thing that had to be true
before any panel was worth drawing: that a real question produces a real trace and a real counter
sample, and that the two find each other.

It then covers the two gaps that stopped that subsystem from drawing a **conversation graph**, both
of which are past section 6 and neither of which the spec anticipated: a conversation had no
identity, so N turns of one were N unrelated traces; and the deterministic cascade's path — which
readers the reasoning fell through, in what order, and which called which — was computed on every
turn and thrown away. Both are closed below. Neither changed the counters.

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
  `changed` — confirmed, 9 to 17 spans per turn depending on how far the turn got (and about
  nineteen more since the reader path was added — see *The path the reasoning fell through*)
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

## The LangGraph turn (2026-10-12)

Making LangGraph the only runtime (`c6765847`) deleted every call site that recorded a decision.
The recorder, the exporter and the middleware survived intact, so the counters were wired and
silent: no `turns_total`, no `decisions_total`, no `turn` span — a green-but-dead instrument of
exactly the kind this document exists to prevent. The graph now records what it actually decides,
in the vocabulary that was already there, plus one seam:

| step | recorded in | verdicts |
| --- | --- | --- |
| `planner` | `LlmTurnOperations.interpret` | `agreed` once per reading (a schema repair is the same reading, `detail=repaired`); `failed` on a model error or two unreadable answers; `declined` past the sentence limit |
| `plan_verifier` | the same, when a reading is suspect | `overrode`, `changed` only when the reading that runs is not the one first read (the model's review replaced it, or the rules held it back as a question); `agreed` when the review confirmed it |
| `ledger` | `graph_execution.execute_work` | `agreed` per bank execution, with the work kind and the scope — never a figure; `failed` with the error type |
| `visual_planner` | `VisualPlanningPolicy.decide` | `agreed` the model's chart, verified; `overrode` the rules refusing an invalid proposal; `failed` no proposal in the budget; `declined` none offered; `claimed` the rules alone (deterministic mode) |
| `visual_follow_up` | `graph_visualization` | an edit or a requested style; `overrode` only when the mark on screen was replaced; `declined` when it did not fit |
| `payment_confirmation` | `graph_turn._reply` | `overrode`, `changed`: a spoken yes held back as neither yes nor no (ADR-010 §5) |

**Counted once.** The outcome and the conversation id are set where a turn finishes
(`GraphTurnHandler.answer`), never on a replay: a request resent under its key is the same turn
handed back. Decisions live inside graph nodes, which a resumed checkpoint does not run again, so a
retry that resumes does not repeat them.

**Verified live**, against Gemini 3.5 Flash through this stack, with a query on
`/v1/questions/stream`, three charts and a Bizum answered with a spoken yes:

- Prometheus: `turns_total` 10 (7 `PLANNED`, 2 `AWAITING_CONFIRMATION`, 1 `CANCELLED`);
  `planner agreed` 9 (the cancelling "no" resumes the preview without the model); `ledger agreed`
  4 (the charts read nothing); `visual_planner agreed` 3; `plan_verifier agreed` 1 (the new
  payment's review); `payment_confirmation overrode changed=true` 1.
- Tempo: every turn under its `conversation.id`, the three turns of the Bizum conversation
  together, and the bank calls in the same trace with their ids templated (`/v1/accounts/{id}/…`).
- None of the sentences' words, the contact's name or an account number in any exported span.

**Known limits.** A decision is an instant, not an interval, so a 35 s chart turn shows *what*
was decided and not *where the time went* — the model calls and the pacing wait between them are
not spans; the response headers `X-FinGuard-Provider-Wait-Ms` and `-Request-Ms` carry that per
turn. banking-api propagates `traceparent` but exports nothing of its own. Logs stay in stdout
(no Loki). Voice (Whisper, Piper) is not instrumented. The HTTP histogram's boundaries were the
SDK's, topping out at 10 s, which flattened every chart turn to 10 s; they now reach 60 s.

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
| **Charts chosen by the model** | `decisions_total{step="visual_planner",verdict="agreed"}` / all `visual_planner` | the share of first charts that were the model's |
| How each chart was chosen | `decisions_total{step="visual_planner"}` by `verdict` | model, rules refusing it, timeout, abstention, rules alone |
| Request duration · /v1/questions and /stream | `http_server_duration_milliseconds_bucket` | the HTTP request, **not** the planner |
| Spoken yes held back | `decisions_total{step="payment_confirmation"}` | the voice guard firing; never money moved |
| Bank reads per turn | `decisions_total{step="ledger"}` / `turns_total` | falls as a conversation turns to its charts |
| Recent turns | Tempo, `{name="turn"}` | a waterfall without an exemplar to start from |

The two recogniser panels this table used to hold were fed only by the cascade the legacy planner
ran; it was removed with it (`c6765847`), and they were replaced rather than left empty.

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

## What a conversation is, and how a turn says which one it belongs to

`Conversation` is a value the client round-trips; there are no sessions and no store. That was fine
until somebody wanted the second turn of a conversation and found N turns were N unrelated traces
with nothing joining them.

The fix follows the rule the value already keeps — *"the client no longer decides what the state
means, it round-trips one value"* — rather than inventing a session store. `to_domain` mints a
`uuid4` when the payload carries no `conversation_id`, `to_payload` sends it back, and the next turn
returns it. One field, both directions, no server-side state.

**It is an identifier and nothing else.** `uuid4` and nothing derived: a hash of the opening
sentence would be a function of what the holder typed, and this value ends up on a span attribute
where a label is forever. Random also means two conversations that started the same way cannot
collide into one trace.

**`extra="forbid"` decided the shape in both directions.** Forwards, it is why a client that
predates the field is not refused: `forbid` rejects fields we do not know and has no opinion about
an optional one a caller leaves out, so an old client keeps working and simply gets a fresh identity
every turn — its turns go on being one trace each until it starts echoing the field back. Degraded,
never broken, no version negotiation. Backwards, it is why this had to be a *declared* field rather
than a loose key, and being declared it had to be typed — and typed `UUID` it is the one field on
this payload a client cannot fill with the holder's own sentence. That matters because this is the
value that becomes a span attribute; pydantic refuses anything else with a 422 at the edge, before
any of it reaches a recorder.

One tension worth knowing: `decisions._own_words` **refuses** anything that parses as a UUID,
because the value that rule was written for is a raw *account* id. So the conversation identifier
can never travel through `record()` — it lives on `TurnRecord.conversation` and `otel` stamps it on
the `turn` span from there. A call site that worked around that redaction would be the first crack
in the rule.

In Tempo: `{ span.conversation.id = "…" }` returns every turn of one conversation. The attribute is
on the `turn` span and nowhere else — one trace is one turn, so repeating it on seventeen decision
spans would multiply one fact and buy nothing.

## The path the reasoning fell through

*History: the cascade below was the legacy planner's, removed in `c6765847`. The LangGraph turn
consults no recognisers, so no reader spans are drawn and `otel_recogniser_spans` has nothing to
turn down.*

`@_recogniser` wraps nineteen deterministic readers and runs them in order until one claims. It
records the winner as a `CLAIMED` decision; the eighteen that declined, and the order, existed at
runtime and were thrown away.

**A span per reader consulted, and the counter untouched.** Section 2's argument for recording only
claims is right *for the metric*: twenty "not mine" rows a turn would bury the one that matters, and
telling them apart would need the reader's name as a metric label — unbounded cardinality arriving
by the one route `Step` and `Verdict` do not cover. A trace has neither problem, and there the order
is the whole point. So `decisions_total`, the `recogniser` decision and every panel built on them
are exactly as they were, and the path is a second, separate thing that is only ever drawn.

**The nesting falls out; it is not programmed.** `decisions.consulting` pushes a frame while a
reader runs, so a reader consulted from inside another is its child because its frame opened while
the outer one was still on the stack. Nothing in the decorator, the record or the adapter names a
caller or a callee. It is recorded as a *value* — `TurnRecord.consulted` — rather than as live
spans, which keeps the design's own property: the path is built unconditionally and is assertable
with nothing listening, and the adapter turns one record into spans at the end as it already did.

An ordinary turn, measured:

```
turn  [conversation.id=… outcome=PLANNED decisions=6 changed=true]
├── _ratio_request                    claimed=false   (3.4 ms)
├── _gambling_plan                    claimed=false
│   … ten more, in cascade order, including _direct_period_comparison asked directly …
├── _natural_period_aggregate_plan    claimed=false
│   └── _direct_period_comparison     claimed=false          ← via _a_comparison_owns_it
├── _deterministic_breakdown_plan     claimed=false
│   └── _direct_period_comparison     claimed=false
│   … four more …
├── _average_plan                     claimed=false           ← the cascade's last reader
│   └── _direct_period_comparison     claimed=false
├── capability_guard                  verdict=agreed changed=false
├── explicit_facts.enforce            verdict=agreed changed=false
├── inherited_window                  verdict=agreed changed=false
├── plan_verifier                     verdict=agreed changed=false
├── ledger                            verdict=agreed changed=true
└── outcome                           verdict=agreed changed=true
```

**29 spans**: one `turn`, 22 consultations (19 roots plus 3 nested), 6 decisions. With
`otel_recogniser_spans=false` the same turn is **7**, and the counters, the exemplars and every
decision span are byte-identical.

And the path this change exists for — «¿En qué porcentaje gasté menos en julio que en junio?»:

```
turn  [conversation.id=… outcome=PLANNED]
├── _ratio_request                    claimed=true    (1.5 ms)
│   └── _direct_period_comparison     claimed=true    (0.5 ms)
├── recogniser   verdict=claimed changed=true     ← the two flat rows, exactly as before
├── recogniser   verdict=claimed changed=true
├── ledger       verdict=agreed  changed=true
├── ledger       verdict=agreed  changed=true
└── outcome      verdict=agreed  changed=false
```

The two `CLAIMED` rows are unchanged and still unrelated to each other in the counter. What is new
is the two-node tree above them saying which one asked the other.

### What the decorator claimed, and what survived

`_recogniser`'s docstring named four readers consulted from inside another and said three of them
"cannot double". **That claim is about *claims*, and it is still exactly right** — those three are
asked by the cascade first and consulted again only after they declined, so no turn records two
`CLAIMED` decisions for them.

What it never said, because nothing could see it, is that they are still *consulted* twice.
`_direct_period_comparison` is asked by the cascade and again inside up to four more readers —
`_natural_period_aggregate_plan`, `_deterministic_breakdown_plan`, `_average_plan` and
`_comparison_with_a_named_window`, all of which call `_a_comparison_owns_it`, which is that reader
under another name. **Four of the five fired on the ordinary spending question above**; the fifth
needs a sentence that gets past `_comparison_with_a_named_window`'s own shape check first. None of
the four claimed, so none of them is visible to a counter of claims. The repeats were always
happening; this is the first instrument that shows them, with a duration on each.

### Why a span per reader and not one ordered attribute on the turn span

Both were built and measured against the same turns, not argued from taste. The alternative is
`recogniser.path` on the `turn` span: an ordered, delimited string, `,` between siblings, `>` for
"asked", `!` for claimed. For the ordinary turn above it is exactly this, and it is **524
characters**:

```
_ratio_request,_gambling_plan,_how_many_payees_plan,_internal_movement_request,
_salary_history_plans,_period_comparison_plans,_comparison_with_previous_period,
_direct_period_comparison,_comparison_with_a_named_window,_contextual_follow_up_plan,
_multiple_movement_ranking,_group_ranking_plan,
_natural_period_aggregate_plan>(_direct_period_comparison),
_deterministic_breakdown_plan>(_direct_period_comparison),_contextual_ranking_plan,
_cumulative_plan,_recurrence_plan,_frequency_plan,_average_plan>(_direct_period_comparison)
```

| | span per reader | one ordered attribute |
| --- | --- | --- |
| ordinary turn | 22 spans (29 total) | 524 chars, 7 spans total |
| callback turn | 2 spans (8 total) | 44 chars, 6 spans total |
| nesting | falls out of when a frame was open | **encoded by hand, parsed by hand** |
| per-reader duration | yes | no |
| findable in Tempo | `{ name = "_ratio_request" }` | substring of one attribute |

**Span per reader wins, and the row that decides it is the third.** The whole reason this task
exists is the callback case, and the attribute can only express it by *programming* the nesting:
something has to maintain the stack, invent a syntax, serialise parentage into it, and a reader in
Grafana has to parse it back. The bracket syntax above is that programming — nobody would call
`_average_plan>(_direct_period_comparison)` self-evident, and every consumer would reimplement its
grammar. In the span tree the parentage *is* the tree, and neither the decorator nor the adapter
knows a caller from a callee.

The two further losses are real but secondary: a duration per consultation is how the four repeated
`_direct_period_comparison` calls became a measurable cost rather than a remark, and a span name is
a Tempo query while a substring is not.

The attribute's one genuine advantage is volume — 524 bytes against 22 spans is not close. That is
what the setting is for, and it is why the setting exists rather than the attribute: turning the
path off entirely costs less to reason about than a second encoding of it that is always on and
always needs decoding.

### `otel_recogniser_spans`

**Default `true`, and it is only ever asked once `otel_enabled` is already `true`.** The compound
default is therefore "nothing at all on a laptop or in a test run, and the whole path the moment
somebody turns tracing on" — which is right, because the path is what they turned it on for. A
default of `false` would be the fourth flag in a row whose off position is the one everybody has.

Measured cost: 22 spans on the ordinary turn above — 29 with the path, 7 without — on top of the
9–17 the decisions already produce. Roughly a quadrupling on a turn the cascade falls all the way
through, and nothing at all on one a reader claims early (the percent-change turn adds 2). A
compound question pays the full cost once per clause, and a clause list is bounded at three.

`ORCHESTRATOR_OTEL_RECOGNISER_SPANS=false` turns it down with no redesign, and turns down nothing
else: the record still holds the path, so a test still asserts it, and the decision spans, the
counters and the exemplars are byte-identical either way.

The record is bounded at 128 consultations a turn — a compound question runs the whole cascade per
clause, so three passes of ~24 is real. Past the ceiling it truncates and logs; it never raises.

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

## What the 0.0 % override rate was actually saying

In 69 real turns, `explicit_facts.enforce` overrode the model **0.0 %** of the time, and so did
every step except `intent` (8.6 %). The reading offered was that the deterministic correction layer
does not fire in ordinary conversation — it fires on the sentences the datasets stress. That
reading had never been tested, because nothing had made one of those guards fire on ordinary
Spanish.

`_LEAVING` (see `explicit_facts`) did. It gives the reader authority to state EXPENSE for «me
cobraron» and «me he dejado», which it never had. Eleven turns of direction-stressing conversation
were driven through a host orchestrator on 8010 against the same running banking-api, once on each
side of that change, and read back from Tempo by `conversation.id`:

| | turns | `enforce` decisions | overrode | rate |
| --- | --- | --- | --- | --- |
| before | 11 | 11 | 0 | **0.0 %** |
| after | 11 | 14 | 3 | **21.4 %** |

**The denominator moved, and that is the finding.** `_enforced` records one decision per field *the
sentence stated*, and skips a field the reader had nothing to say about — deliberately, so an
override rate is not diluted by turns a rule was never asked about. Before the change the reader
could state a direction only towards income, so on «¿cuánto me he dejado?» it stated nothing and
there was no direction row to be either an override or an agreement. The three new rows are the
three direction statements it can now make, and all three are overrides. **0.0 % was partly a
measurement of what the reader was able to say, not only of how often it disagreed.**

What the rate does *not* say is that the layer was idle: 11 of 11 turns already carried an
`enforce` decision, all `agreed` — the window, stated and matched. A step that fires on every turn
and alters nothing is exactly the case Section 3 kept `agreed` for, and it is what separates "not
consulted" from "consulted and content".

### Three things the graph shows that the datasets do not

**A turn a recogniser claims never reaches `enforce` at all.** «¿Y cuánto ingresé?», the second
turn of a three-turn conversation, is claimed by `_contextual_follow_up_plan`: 11 consultations
instead of 22, and **4 decisions — `intent`, `recogniser`, `ledger`, `outcome`.** No
`capability_guard`, no `planner`, no `enforce`, no `plan_verifier`. The deterministic reader cannot
correct a direction on a turn the deterministic cascade has already answered, and no dataset case
can show this, because the datasets score one sentence at a time.

**The whole cascade declines on the sentences this defect was about.** «¿Cuánto me he dejado este
mes?» falls through all nineteen readers — 22 consultations, 19 roots plus
`_direct_period_comparison` asked three more times from inside `_natural_period_aggregate_plan`,
`_deterministic_breakdown_plan` and `_average_plan` — and lands on the model, every time. The
reader that eventually corrects it is not in that cascade at all; it runs after the model has
answered. A follow-up costs 23.

**The correction is worth a real figure, not a refusal.** «¿Cuánto me cobraron el mes pasado en
todas mis cuentas?» answered **2.512,07 € in 3 movimientos** before and **1.895,97 € in 28
movimientos** after. Both are real numbers, correctly computed, over the same window; the first is
the money that *arrived*. Nothing downstream could have told them apart, which is the failure this
whole service is arranged around, and it is visible here only because the direction row on that
turn says `overrode`.

One caveat on repeating this. Local inference is not bit-reproducible even at seed 0: one turn
(«¿Y cuánto me cobraron?», third of its conversation) planned before and asked for clarification
after. Three consecutive re-runs planned it. The difference is `planner verdict=declined`, which is
upstream of the seam under test — but a single run of eleven turns cannot tell that apart from a
regression without re-running it.
