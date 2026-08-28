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
So the read-only credential is the honest one here, and it is what the figures below used. Its
scopes were read out of the running container to confirm it: `questions:read`, `metrics:read`,
`metrics:write`, and no payment scope at all. The conversation corpus is the opposite case and
needs `demo-token-laura-pays`; its own results file says so, and the corpus proves it — 5 threads
and 10 turns assert `PAYMENT_IN_PROGRESS`, which `payments:prepare` gates.

## Re-measured 2026-08-28 — the previous entry's zero was vacuous, and now it is not

The entry this replaces recorded **0 violations on all 235 cases** and then spent most of its
length explaining why that zero meant nothing: not one case had reached an answering outcome, and
every figure-integrity property in `violations()` is guarded on an answer existing. This run is
the same three sets after the harness was repaired, and **the numbers are worse, which is the
point**. A real violation count over 175 real answers is worth more than a zero over none.

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
| service code | container built at `dd1d2d0`, **not rebuilt for this run** |
| harness code | this commit — `scripts/evaluate_e2e.py` runs on the host, so it changed without touching the container |
| ledger | `apps/banking-api/seeds/0001_demo.sql` — verified before, during and after: **1678** `ledger_transactions`, **3356** `ledger_entries` |
| token | `demo-token-laura-0001` (read-only) |
| GPU | RTX 5060 Laptop, 8151 MiB. Model 6.13 GB resident, 5.37 GB in VRAM (87 %). 68 °C, `sw_thermal_slowdown` **Not Active**, 79 % utilisation |

Three caveats on that block.

- `ORCHESTRATOR_BUILD_SHA` in the running container still reads `unknown`, so the container cannot
  attest its own commit. That the image is `dd1d2d0` remains the operator's statement, not a
  measurement.
- **The ledger's read surface is byte-identical to the previous entry's; its payment surface is
  not.** `payment_intents` holds **10** rows where that block recorded 0 — 6
  `AWAITING_CONFIRMATION`, 4 `CANCELLED`, left by a sibling's payments-enabled corpus run. None
  reached `EXECUTED`, and `ledger_transactions` is unchanged at 1678, so no movement was posted and
  no figure below can have moved because of them. Recorded rather than glossed: the reference block
  says "0 payment intents" and that is no longer true of this database.
- The chassis is not the one `RESULTS.md`'s latency section was written on: that section cites
  "180 MHz of 3090", this is an 8 GB laptop 5060.

### What the harness does now, and why

Two changes. Both exist because this benchmark spent a week reporting zeros it had not earned.

**1 · The account scope is settled in the harness.** `b928d90` (2026-08-21) made every question
that names no account ask which one first, and **119 of the 120 dev questions name none**. These
sets are single-turn, so the account question could never be answered and the parked plan could
never run.

The sentences were not available to change: `evaluate_planning.py` scores these same three files
and there the sentence *is* the input being scored, so editing 235 questions would have silently
changed what that benchmark measures. So the harness opens each case with
`{"account_selection_mode": "ALL"}` — which is the same state the service stores the moment a
holder answers the account question, so a case is *resuming a real thread* rather than inhabiting a
synthetic state. Every case still reads that selection back through the directory-backed branch of
`_scoped`, which is the branch that had never run.

This is deliberately the same move `tests/support/episodes.py` makes for the conversation corpus.
That file's `opening()` and its `scope:` vocabulary are where the idea already lived, and a test
now pins the two together so they cannot drift:

```python
assert e2e.SETTLED == episodes.opening(settled)
```

**And a subset still answers the account question for real.** Engineering away the commonest thing
the service emits would be the same mistake in a new place, so one in eight plan-expecting cases
opens with nothing settled, receives the clarification, and replies «Todas mis cuentas» the way the
client does. The stride is derived rather than chosen: only `expect: plan` cases can reach the
account question, the smallest such pool is adversarial's 17, and two cases is the minimum coverage
that survives one flake — 17 // 2 = 8.

**This costs almost nothing, and the instruction that commissioned this work was wrong about why.**
The brief assumed answering the clarification would cost "an extra model call per case … ~12
minutes of GPU". It does not. `evaluation/conversations/31-account-scope.yaml` asserts the chip
reply reaches no planner (`no_model: true`) — `account_words.asks_for_all_accounts` parses it — and
that is visible in the timings below: **11 of the 132 requests in the dev run returned in 13–55 ms,
against a clean 16× gap to the next slowest at 873 ms.** The second turn is a round trip, not a
generation. Seeding is still the right default, because turn *one* of an unsettled case costs the
same planner call either way and seeding halves the round trips; but the real-path subset could
have been much larger than 25 cases for no GPU at all.

**2 · A run that measured nothing now fails.** `ANSWERING_FLOOR = 0.5`. If fewer than half the
cases a set's own `expect` field says should produce a plan reached an answering outcome, the
recipe prints `THE BENCHMARK DID NOT RUN` and exits non-zero instead of printing zeros. The
argument for the number is below.

### The result

| set | cases | violations | exit | `PLANNED` | `NEEDS_CLARIFICATION` | `UNSUPPORTED` | `INVALID_DOMAIN` |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| dev | 120 | **1** | 1 | **101** | 16 | 3 | 0 |
| validation | 79 | **1** | 1 | **57** | 13 | 3 | 6 |
| adversarial | 36 | **0** | 0 | **17** | 8 | 9 | 2 |

Beside the zeros it replaces:

| set | | violations | `PLANNED` | `NEEDS_CLARIFICATION` |
| --- | --- | ---: | ---: | ---: |
| dev | before | 0 | **0** | 117 |
| | now | **1** | **101** | 16 |
| validation | before | 0 | **0** | 71 |
| | now | **1** | **57** | 13 |
| adversarial | before | 0 | **0** | 25 |
| | now | 0 | **17** | 8 |

**`PLANNED` went from 0 to 175 across the three sets.** The clarification count fell from 213 to
37, which is the account question no longer swallowing the entire corpus.

### What the properties actually had to work with

This block is the one that was missing, and it is printed on every run whether or not anything
broke — because the failure this file exists to prevent looked exactly like a clean run.

| set | `expect: plan` | answered | floor | answers with a figure | breakdowns reconciled |
| --- | ---: | ---: | ---: | ---: | ---: |
| dev | 102 | **96** | 51 | 101 | **7** |
| validation | 66 | **54** | 33 | 57 | **4** |
| adversarial | 17 | **14** | 9 | 17 | **0** |

Two notes on reading it.

- **"answered" and "answers with a figure" count different populations on purpose.** The first is
  only the plan-expecting cases, so numerator and denominator describe the same set of questions and
  the ratio is a ratio. The second is every answering case, because any answer is something the
  properties can inspect — including the 5 dev and 3 validation cases that answered although their
  `expect` said `clarification` or `unsupported`. An earlier draft of this harness conflated the two
  and printed dev as "101/102"; the honest figure is **96/102**, and finding that is the reason the
  two counters are separate.
- **`breakdowns reconciled` is what lets gate 1's *Evidence reconciliation* row be filled at all.**
  `_reconciliation()` returning `[]` reads identically whether the parts added up or there were no
  parts, so a violation count alone cannot distinguish 0 failures from 0 checks. 11 breakdowns were
  inspected across the three sets and 1 failed.

### Every property violation, in full

Two, both on real answers with real figures. Neither is fixed here; both are findings.

#### 1 · `dev / unsupported_comparison` — `GROUPS_DO_NOT_SUM_TO_TOTAL`

| | |
| --- | --- |
| sentence | «¿He gastado más este mes que el anterior?» |
| the set says | `expect: unsupported`, group `unsupported` — *"a comparison is two plans; FinancialQuery expresses one"* |
| outcome | `PLANNED` |
| text | «En lo que va de mes: 0,00 €; el mes pasado: 1.895,97 €. El gasto fue 1.895,97 € mayor el mes pasado.» |
| `plan` | `en lo que va de mes, expense frente a el mes pasado, expense` |
| `total_minor` | **−189597** |
| `movement_count` | 28 |
| `groups` | `[{en lo que va de mes, 0, 0}, {el mes pasado, 189597, 28}]` |

`sum(groups.total_minor)` is **189597** and `total_minor` is **−189597**, so the property fires.
`sum(groups.movement_count)` is 28 and equals `movement_count`, so `GROUP_COUNTS_DO_NOT_SUM` does
not — which is what makes this precise rather than a shrug.

The mechanism is `ask_financial_question.py:3225`:

```
total_minor=(None if latest.metric is Metric.COUNT else latest_value - earlier_value),
```

On a comparison, **`total_minor` is a signed difference and `groups` are two operands**, not parts
of a whole. The reconciliation property — and gate 1's *Evidence reconciliation* row with it —
assumes groups are parts and `total_minor` is their total. Both halves are internally coherent and
the payload contract between them is ambiguous. The consequence is not academic: a client that
renders `total_minor` as "the total" shows **−1.895,97 €** for a question about spending. Whether
the fix belongs in the service or in the property is a judgement this run does not make.

Note also that the answer arrived at all, on a case the set marks `unsupported` — comparisons are
evidently supported now and the dataset's expectation is stale. That is accuracy, which is
`evaluate_planning.py`'s business; this harness deliberately does not score `expect` per case.

#### 2 · `validation / t_noise_typos` — `PLAN_ANSWER_MISMATCH`

| | |
| --- | --- |
| sentence | «cuando gaste en alqiler el mes pasdo?» (typos for «¿cuánto gasté en alquiler el mes pasado?») |
| the set says | `expect: plan` — `period: LAST_MONTH`, `direction: EXPENSE`, `categories: [HOUSING_RENT]` |
| outcome | `PLANNED` |
| text | «11 veces en alquiler en los últimos 12 meses: una al mes, normalmente entre el día 1 y el 4.» |
| `plan` | `en los últimos 12 meses, expense, housing_rent, frecuencia` |
| `last_plan` | `metric: FREQUENCY`, `period: LAST_N_MONTHS n=12`, `categories: [HOUSING_RENT]` |
| `total_minor` | 1012000 |
| `movement_count` | 11 |
| `groups` | `[{total, 1012000, 11}]` — one ungrouped row, so reconciliation correctly skipped |

**This one is mostly a gap in the property, and that is the more useful finding.** It fired on the
second branch:

```
if not counting and not money_shown and body.get("movement_count"):
```

`counting` compares `metric == "COUNT"`. The metric here is **`FREQUENCY`** — a third answer shape
the rule does not know — so `counting` is false; the headline leads with "11 veces" and carries no
euro figure, so `money_shown` is false; `movement_count` is 11. The rule therefore reads a
*correct* frequency answer as a mismatch. `_ANSWERING` just above it records the same lesson being
learned once already, for `BALANCE`: *"PLANNED was the only one when the rule below was written."*

Underneath the property gap there is a real planner miss, and it belongs to the other benchmark:
the typo'd «cuando» for «cuánto» was read as «cuándo» — *when*, how often — and the window widened
from `LAST_MONTH` to `LAST_N_MONTHS:12`.

One further oddity, flagged as an observation rather than a conclusion: `across_accounts` is
`False` on this plan while the answer text says «Consultado en todas tus cuentas» and the harness
opened with `account_selection_mode: ALL`. Finding 1's plan has it `True` under the same opening.
Worth a look by somebody who knows what that field is for.

### Why the floor is half, and why half is not a round number

The vacuity guard needs a threshold that sits **far below any plausible accuracy result** — or it
becomes an accuracy gate, fires on ordinary model variance, and gets switched off the first time it
flakes — and **far above zero**, or it permits the silence it exists to catch. `RESULTS.md` in this
directory bounds both ends, and it is where the number comes from:

- Its model table is eleven models measured over these same three sets. The **worst outcome
  accuracy any of them scored on any of the three is 50.0 %** — `phi4-mini` on adversarial, a model
  that was measured and rejected. (Its `inject` column goes lower, but that is a group inside
  adversarial rather than a set this recipe runs.)
- The shipped `qwen3.5:9b` scores **95.8 / 87.3 / 77.8**.
- That file records the run-to-run band as **±1 case**.

So a run below half is below the worst model anybody has ever pointed at this data. No model swap,
no seed, and no ±1 case of reproducibility reaches it. What *does* reach it is a feature landing
between the sentences and the properties, a credential quietly losing a scope, or a ledger
re-seeded out from under the run — the entire family of failures that produced the vacuous zero.

The denominator is the dataset's own claim rather than the case count, because a set may
legitimately be mostly `unsupported` — adversarial nearly is, at 17 of 36 — and demanding answers
from cases written to be refused would be a bar the data cannot meet. This is the first time the
harness reads `expect` at all, and it is used **only** for the denominator; scoring per-case
correctness stays `evaluate_planning.py`'s job.

There is a second prong, and it is an absolute zero rather than a fraction: **if no answer carried a
figure, the run failed** even if every case answered. An answering outcome with `total_minor: None`
passes every property in `violations()` for want of anything to check, so a whole set of them is the
same silence one field lower. A fraction would be wrong there — the count of answers moves with the
model, but no accuracy story explains a whole set of figureless answers.

And because the floor now depends on a YAML field, a mistyped `expect:` would silently shrink the
denominator — the disease in the cure. `EXPECTATIONS` and a test in `test_e2e_checks.py` guard the
three datasets against that, the way `TURN_KEYS` already guards `conversations.yaml`.

**What it would have caught.** On the previous run the floor would have been 51 of 102 dev, 33 of 66
validation, 9 of 17 adversarial, against 0 answers in all three. All three recipes would have
failed on 2026-08-21, the day the account-scope feature landed, instead of passing for a week.

### Reproducibility

`dev` was run twice in this session with a pinned seed and **reproduced itself exactly**: 101
`PLANNED`, 16 clarifications, 3 `UNSUPPORTED`, and the same single violation on the same case both
times. `validation` moved by one case between its two runs — 58 `PLANNED` / 5 `INVALID_DOMAIN` then
57 / 6 — which is precisely the ±1 band `RESULTS.md` documents, and its violation was the same case
both times. The figures tabled above are the second run of each.

### Latency

**Attach the caveat.** `RESULTS.md` in this directory says no latency figure in it is quotable, and
the evidence is a 13.8× spread on an unchanged model. Nothing here escapes that: it is one thermal
window on one chassis, and a different chassis from the one that caveat measured. These figures are
useful as a **contention check** and as nothing else.

Server-side `latency_ms` from the orchestrator's own `request_log`, `POST /v1/questions` only. The
harness still prints no timing, so this is read from the container log rather than from the report.

| run | n | P50 | P95 | P99 | mean | max | first | first ten | last ten |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| dev | 132 | 2.91 s | 3.73 s | 4.09 s | 2.62 s | 4.25 s | 3.67 s | 2.94 s | 1.53 s |
| validation | 89 | 2.91 s | 3.70 s | 4.24 s | 2.54 s | 4.24 s | 2.86 s | 2.76 s | 1.38 s |
| adversarial | 40 | 3.03 s | 4.86 s | 4.90 s | 2.76 s | 4.90 s | 3.23 s | 3.36 s | 3.12 s |

`n` exceeds the case count because the real-path subset spends a second request, and each window
catches one boundary row from the run before it.

- **dev's P50 of 2.91 s is identical to the figure the previous entry recorded for dev**, and
  validation's matches too. The account scope being settled did not change what a question costs.
- **No contention in these three.** No `PROVIDER_ERROR`, no `INVALID_STRUCTURE`, and nothing within
  115 s of the 120 s planner timeout — the worst single request in 261 was 4.90 s.
- **No throttling.** `sw_thermal_slowdown` read `Not Active` at 68 °C throughout; all three runs'
  last ten are faster than their first ten.
- **The chip turns are free.** 11 of dev's 132 requests and 8 of validation's 89 returned in
  13–55 ms, with a clean 16× gap to the next slowest. That band holds the chip replies and the
  deterministic refusals together and latency alone cannot separate them, but it confirms the order
  of magnitude the corpus asserts.

**One run in this session was contended and is excluded.** The first `dev` run overlapped ~2 minutes
of a conversations run this session started by accident — `--limit 0` is falsy and therefore means
*no limit*, so it ran the whole corpus. Latency rose to 11.2 s at worst; no request timed out and no
`PROVIDER_ERROR` occurred, so that run's property results stand and match the clean re-run exactly,
but its timings are discarded rather than reported.

### Gate 1

Both rows can now be filled. They were `⬜ not measured` at `d439648`, corrected there by `dd1d2d0`
— they were **not** still carrying a ✅.

| row | bar | reading |
| --- | --- | --- |
| E2E property violations | **0** | ❌ **2** — `dev/unsupported_comparison` `GROUPS_DO_NOT_SUM_TO_TOTAL`, `validation/t_noise_typos` `PLAN_ANSWER_MISMATCH`. Over **175 answering outcomes** carrying **175 figures**, so the count now means something. adversarial is a clean 0 over 17 answers. |
| Evidence reconciliation | **0** failures | ❌ **1 of 11 breakdowns inspected** — the comparison above. Measured for the first time: `_reconciliation()` had executed zero times on every previous run. |

Both are failures rather than passes, and both are worth more than the zero they replace. The
reconciliation failure in particular is the row's first real reading in the table's history.

### Not measured, and why

- **Which cases clarified, and which six plan-expecting dev cases did not answer.** The harness
  prints outcome totals and case ids only for violations, so per-case outcomes still cannot be read
  off a run. This is the same gap the previous entry recorded and it is unchanged.
- **`previous` is still ignored.** 9 dev and 6 validation cases carry a `previous` plan for
  follow-up context and `run()` never sends it, so those cases are asked cold. Pre-existing, not
  introduced here, and a plausible contributor to the six dev cases that did not answer.
- **The conversation corpus over HTTP.** Not run; it is a sibling's measurement and needs
  `demo-token-laura-pays`.
- **Whether the seeded opening and the real path agree on the *answer*.** What is measured is that
  all 25 real-path cases were asked the account question, all 25 accepted the chip, and none
  re-asked it — no `CLARIFICATION_RESTARTED` fired anywhere. That the two paths reach the same
  *figure* case by case would need the same case run both ways and compared, which this does not do.
- **`adversarial` with `demo-token-laura-pays`** as a control on whether the credential changes the
  property result. Not attempted.

### Other silent passes in this script

Found while repairing the one above, in the same shape: *a check that passes because the field it
reads is absent.* Reported, not fixed — several would change what a benchmark measures, and one path
is a sibling's live measurement.

1. **A `PLANNED` answer carrying no figure violates nothing.** Verified: `violations()` returns `[]`
   for `outcome: PLANNED, total_minor: None`. The exact mirror of `FIGURE_WITHOUT_AN_ANSWER`. The
   vacuity guard's second prong defends the set-level case; the per-case hole is still open.
2. **`expect_shape` silently passes when the outcome is not `PLANNED`.** Its two siblings do not:
   `expect_plan` and `must_change` both emit `NOT_PLANNED_SO_NO_<field>`. Verified asymmetry, so a
   turn demanding `expect_shape: GROUPS` that came back as a clarification passes.
3. **The clarification-loop detector cannot fire on the commonest clarification.** `asked_slot()`
   reads `conversation.awaiting`, which is `null` on an account-scope clarification, so
   `ASKED_<slot>_N_TIMES` can never trigger there. Already noted in the previous entry; the read
   path now routes around it with an explicit `CLARIFICATION_RESTARTED` check that does not depend
   on `awaiting`.
4. **`run_conversations()` has no vacuity guard at all.** An empty or fully-skipped corpus prints
   `PROPERTY VIOLATIONS 0` and exits 0 — the same defect, in the same file, on the path this repair
   did not touch.
5. **`--limit 0` means "no limit".** `cases[:limit] if limit else cases` is a falsy guard, in both
   `questions()` and `run_conversations()`. It cost this session a contended run.
6. **`_reconciliation()` raises `KeyError`** on a group missing `total_minor` rather than reporting
   it. A crash rather than a silent pass, so lowest priority.
7. **Already recorded elsewhere:** `adr-011-derived-charts-and-saved-metrics.md` notes that the same
   reconciliation never reads `visualization`, so a chart disagreeing with the figure in the text is
   invisible to it.
