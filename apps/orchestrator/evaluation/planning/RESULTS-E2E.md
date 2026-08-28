# The three question sets over HTTP — properties, not accuracy

`RESULTS.md` beside this file measures the plan and stops where the plan is built.
`../conversations/RESULTS-E2E.md` measures the same deployed service over multi-turn episodes.
This one is `scripts/evaluate_e2e.py` against `dev.yaml`, `validation.yaml` and
`adversarial.yaml`: question in over HTTP, figure and sentence out, and a list of properties
that must hold on every answer. A violation is a bug rather than a long tail, which is why the
recipe exits non-zero on any.

```bash
just evaluate-e2e demo-token-laura-0001 dev
just evaluate-e2e demo-token-laura-0001 validation
just evaluate-e2e demo-token-laura-0001 adversarial
```

**Which token, and why the read-only one.** `scripts/evaluate_e2e.py`'s own docstring names
`demo-token-laura-0001`, and these three sets are read benchmarks: no case in `dev` or
`validation` asks for money to move, and the two in `adversarial` that do
(`adv_transfer_request`, `adv_concept_injection`) are cases the set expects to be *refused*.
So the read-only credential is the honest one here, and it is what the figures below used.
The conversation corpus is the opposite case and needs `demo-token-laura-pays`; its own
results file says so, and the corpus proves it — 5 threads and 10 turns assert
`PAYMENT_IN_PROGRESS`, which `payments:prepare` gates.

## Measured 2026-08-28, at `dd1d2d0`

| | |
| --- | --- |
| planner model | `qwen3.5:9b` (`ORCHESTRATOR_PLANNER_MODEL`) |
| planner provider | `ollama` — local, no egress, Ollama 0.33.1 |
| turn interpreter | `hybrid` |
| `num_ctx` | 4096 |
| seed | 0, pinned |
| `keep_alive` | 30m |
| planner timeout | 120 s |
| questions / payments / metrics | all enabled |
| code | `dd1d2d0` |
| ledger | `apps/banking-api/seeds/0001_demo.sql` — verified before and after: 1678 transactions, 3356 entries, 0 payment intents |
| token | `demo-token-laura-0001` (read-only) |
| GPU | RTX 5060 Laptop, 8 GB. Model 6.13 GB resident, 87 % in VRAM / 13 % on CPU |

Two caveats about that block. `ORCHESTRATOR_BUILD_SHA` in the running container reads
`unknown`, so the container cannot attest its own commit — that the image is `dd1d2d0` is the
operator's statement, not a measurement. And the chassis is not the one `RESULTS.md`'s latency
section was written on: that section cites "180 MHz of 3090", this is an 8 GB laptop 5060.

### The result

| set | cases | violations | exit | PLANNED | NEEDS_CLARIFICATION | UNSUPPORTED | INVALID_DOMAIN |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| dev | 120 | **0** | 0 | **0** | 117 | 3 | 0 |
| validation | 79 | **0** | 0 | **0** | 71 | 3 | 5 |
| adversarial | 36 | **0** | 0 | **0** | 25 | 9 | 2 |

Zero violations on all three sets. Every recipe exits 0. `dev` was run twice and reproduced
itself exactly — 117/3 and 0/120 both times, which is what a pinned seed is for.

**And the zero is vacuous.**

### Why the zero says nothing

Not one question in any of the three sets produced an answer carrying a figure. The `PLANNED`
column is zero on all 235 cases.

Every property in `violations()` is guarded on an answer existing:

- `PLAN_ANSWER_MISMATCH` — the COUNT defect this harness was written for, the one that "passed
  235 cases and 163 unit tests" — is inside `if body.get("outcome") == "PLANNED"`.
- `NO_EVIDENCE` is inside the same branch.
- `_reconciliation()` — the *Evidence reconciliation* row of gate 1 — is called only from that
  branch, and returns early unless the answer carries a real breakdown.
- `FIGURE_WITHOUT_AN_ANSWER` needs `total_minor` to be set. It never was.
- `NOTHING_SAID` is the only property a clarification can break, and clarifications are
  sentences.

So a `NEEDS_CLARIFICATION` cannot violate any property, and every one of the 235 cases came
back as one of those, `UNSUPPORTED`, or `INVALID_DOMAIN`. **The suite passed because it tested
nothing, not because the service is right.**

The harness also never reads each case's `expect` field — `run()` uses only `question` and
`id`. So the sets' own statement that 102 of 120 dev cases, 66 of 79 validation cases and 17 of
36 adversarial cases *should* produce a plan is invisible here. Accuracy is
`evaluate_planning.py`'s job, and it puts dev outcome at 95.8 %: the planner is producing good
plans. This path is not executing them.

### The cause: account scope met a single-turn corpus

Asking any of these questions directly returns the account question, with a correct plan parked
beside it:

```
question     ¿Cuánto llevo gastado hoy?          (dev/period_today, expect: plan)
outcome      NEEDS_CLARIFICATION
text         ¿En qué cuenta quieres consultarlo? Puedes elegir Cuenta corriente,
             Hucha, Fondo indexado o Todas mis cuentas.
pending_plan {"direction": "EXPENSE", "period": "TODAY", "group_by": "NONE", ...}
total_minor  null
```

The plan is right. Laura holds three accounts, and `b928d90` (2026-08-21) *feat(orchestrator):
clarify and remember account scope* made a question that names no account ask which one. These
three sets are single-turn — the harness sends one POST per case and never replies — so the
account question can never be answered and the parked plan can never run.

It is structural rather than incidental: **1 of the 120 dev questions mentions an account at
all**, and only in passing ("estaba mirando las cuentas"). None names one.

The dates are the whole story:

| | |
| --- | --- |
| `dev.yaml`, `validation.yaml`, `adversarial.yaml` last changed | **2026-08-03** (`dddf8d6`) |
| `evaluate_e2e.py` created | 2026-08-03 (`b8812ec`) |
| account scope shipped | **2026-08-21** (`b928d90`) |

The conversation corpus was taught about account scope — `ee12f6a` and `68a5263`, both
2026-08-28 — because it has turns to do it in. These three sets were not, and cannot be
without a second turn.

`../conversations/RESULTS-E2E.md` already recorded this shape from the other side: 50 of the 67
violations there were "the script expects a plan; the service asks which account", and it named
the choice — teach the episodes to answer, or ask less. **That decision is still open, and it is
now also what stands between gate 1 and a measurement.** A third option exists that did not
before: give this harness a second turn, so a single-turn set can answer the one question the
service always asks.

### A related gap in the property suite

On that clarification, `conversation.awaiting` is `null`. `asked_slot()` returns `None` unless
`awaiting` is set, so `clarification_run()` never counts a run and the
`ASKED_<slot>_N_TIMES` loop detector cannot fire on the most common clarification the service
now emits. The loop property is real; the account-scope path is invisible to it.

### Against 2026-08-03

The figures carried into gate 1 were `0/120 dev, 0/79 validation, 0/36 adversarial`. They are
not written down anywhere in this repository — `docs/next.md` dates the row to 2026-08-03 but
records no per-set numbers, so this is a comparison against the gate table's claim rather than
against a recorded run.

**The case counts still match exactly** — 120, 79, 36 — because the three files have not been
touched since 2026-08-03. These sets did not grow. (The conversation corpus did: 63 threads and
132 turns on 2026-08-23, 67 and 142 now.)

| | 2026-08-03 | 2026-08-28 |
| --- | --- | --- |
| dev | 0/120 | 0/120 |
| validation | 0/79 | 0/79 |
| adversarial | 0/36 | 0/36 |

**The numbers are identical and they no longer mean the same thing.** On 2026-08-03 the harness
was three weeks old, account scope was eighteen days away, and these questions were answered.
Today none of them is, and a suite with nothing to check cannot fail. "Unchanged" would be the
most misleading true sentence available.

### Latency

**Attach the caveat.** `RESULTS.md` in this directory says no latency figure in it is quotable,
and the evidence is a 13.8× spread on an unchanged model — P50 2.28 s on a cold machine against
31.46 s after eight hours of load. Nothing here escapes that: it is one thermal window on one
chassis, and a different chassis from the one that caveat measured.

Server-side `latency_ms` from the orchestrator's own `request_log`, `POST /v1/questions` only.
**The harness prints no timing at all**, so this is read from the container log rather than from
the report — which also means there is no first/last-ten instrumentation of the kind
`RESULTS.md` asks for, and the columns below are computed here rather than printed by the tool.

| run | n | P50 | P95 | P99 | mean | max | first | first ten | last ten |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| dev | 120 | 2.91 s | 3.74 s | 4.17 s | 2.80 s | 4.24 s | 3.72 s | 3.14 s | 1.73 s |
| dev, again | 120 | 2.91 s | 3.76 s | 3.95 s | 2.80 s | 4.02 s | 3.31 s | 3.10 s | 1.72 s |
| validation | 79 | 2.91 s | 3.79 s | 4.12 s | 2.72 s | 4.21 s | 3.36 s | 2.98 s | 1.25 s |
| adversarial | 36 | 2.96 s | 4.25 s | 4.87 s | 2.83 s | 5.04 s | 2.92 s | 3.11 s | 3.06 s |

Every request in all four runs returned HTTP 200. What is visible *inside* them:

- **No cold start inside any run.** The model was loaded before the first recipe. That load cost
  **18.3 s**; each run's own first case was 2.9-3.7 s.
- **No throttling.** Three of four runs' last ten are faster than their first ten.
  `clocks_throttle_reasons.sw_thermal_slowdown` read `Not Active` throughout, 67-71 °C.
- **No contention in these four.** No `PROVIDER_ERROR`, no `INVALID_STRUCTURE`, and nothing
  within 115 s of the 120 s planner timeout — the worst single request in 355 was 5.04 s.
- **The two dev runs agree to 0.01 s** on P50 and 0.02 s on P95, which bounds the run-to-run
  spread far more tightly than 13.8× — inside this window.

These figures are unusable as a capability statement and useful as a contention check, which is
the only thing `RESULTS.md` permits them to be.

A note on the documented warm-up. `../conversations/RESULTS-E2E.md` gives a `curl` to
`/api/generate` that passes no `num_ctx`, so it loads the model at Ollama's default context
(262144 here) while the service uses 4096. Ollama reloads on the service's first request, so the
documented procedure does not warm the configuration that answers. Observed directly: after the
warm-up `/api/ps` reported `context_length 262144`; after the first service request, `4096` and
6.13 GB at 87 % in VRAM — matching the 14 % CPU offload `RESULTS.md` records as structural for
this model.

### Not measured, and why

- **The conversation corpus over HTTP** (`just evaluate-conversations demo-token-laura-pays`).
  Not run. Ollama's model runner wedged part-way through a fifth run: a request hit the 120 s
  planner timeout and returned `PROVIDER_ERROR` at 120021.5 ms, after which `/api/generate`
  stopped responding entirely while `/api/version` and `/api/ps` kept answering. `ollama stop`
  did not reclaim it — the runner stayed resident holding 7.2 GB of VRAM with an already-expired
  TTL. Recovery needs the runner process killed, which is outside what this session could do.
  Nothing about the four runs above is affected; they had all finished before it.
- **`adversarial` with `demo-token-laura-pays`**, as a control on whether the credential changes
  the property result. It was running when the planner wedged and its partial output is
  discarded rather than reported: 11 of 36 cases, one of them the `PROVIDER_ERROR` above. The
  contention was self-inflicted — repeatedly dumping the whole container log while a model with
  13 % of its layers on the CPU was answering — which is worth recording as the fourth way to
  waste a run.
- **What the properties do when an answer exists.** The interesting measurement, and it needs a
  planner: send the question, reply "todas mis cuentas" the way the client does, and run
  `violations()` over the second response. That is what these three sets used to measure and no
  longer do.
- **Per-case outcomes.** The harness prints outcome totals and only prints case ids for
  violations, so which 3 dev cases were `UNSUPPORTED` cannot be read off a passing run.

### Gate 1

| row | reading |
| --- | --- |
| E2E property violations — **0** | Measured 0, exit 0, on all three sets. **Not a pass**: 0 of 235 answers carried a figure, so no figure-integrity property was evaluated. |
| Evidence reconciliation — **0** failures | **Not measured.** `_reconciliation()` runs only on a `PLANNED` answer carrying a real breakdown. It executed zero times. |

Both rows should stay ⬜ with the blocker named, rather than move to ✅ on a zero that means the
opposite of what the row asks. The bar is right. The datasets can no longer reach it.
