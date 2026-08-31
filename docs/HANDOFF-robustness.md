# Handoff — FinGuard, 2026-08-31

Written to be read by somebody with no context. Everything here is measured unless it says
otherwise.

## Where the code is

| | |
| --- | --- |
| branch | `task/visualization` |
| HEAD | **`986469a`**, pushed, working tree clean |
| suite | **3300 passed, 3 skipped, 2 deselected**, capabilities 16/16 green |
| linters | `ruff check`, `ruff format --check`, `mypy src` clean |
| corpus | 173 episodes / 1167 turns, all four rates 0.0 % |
| unfinished work | branch `worktree-agent-ae15c0861ca65d01e` at **`b2fc1b3`**, parent `986469a`. **Not merged, not pushed.** |

## What is finished

**Gate 1 passes eight of eight** (`docs/next.md`). Its last failing row was `Unsafe execution
≤ 10 % on every set`; the five sets were re-taken in one sitting at `7156038` with `qwen3.5:9b`:

| set | unsafe | outcome |
| --- | --- | --- |
| validation | 5/79 · 6.3 % | 74/79 · 93.7 % |
| dev | 7/120 · 5.8 % | 115/120 |
| adversarial | 3/36 · 8.3 % | 28/36 · 77.8 % |
| drift | 4/75 · 5.3 % | 71/75 · 94.7 % |
| metamorphic | 0/19 · 0.0 % | 14/19 · 73.7 % |

**Do not call `validation` "held out".** The harness's own `--dataset` help says it *"selected a
model and is no longer held out"*. `drift` is the only genuinely blind set: *"the only one nobody
here wrote"*.

Four defects were fixed to get there, all the same shape — a deterministic reader blind to
something the sentence stated, so the model's reading stood unopposed: two window phrasings, a
follow-up inheriting the window its silence did not name, a `CUMULATIVE` with no buckets becoming
the total, and «me cobraron» / «me he dejado» reading as spending.

**Observability**, six sections, all merged and proven end to end (`docs/architecture/observability.md`,
`docs/superpowers/specs/2026-08-29-orchestrator-observability-design.md`). Decisions with impact,
OTLP → collector → Tempo/Prometheus/Grafana behind a compose profile, plus a conversation graph:
`conversation.id` on the turn span and a span per reader consulted, so the callback tree draws
itself. 33 of 33 exemplars resolved from a real counter sample to a real waterfall.

**Section A1** of the language-surface migration
(`docs/superpowers/specs/2026-08-30-scaling-the-language-surface-design.md`): the 19-reader cascade
became a registry with declared preconditions, **proven equivalent over 1,341 sentences, 0
disagreements**. Consultations for an ordinary spending question: 22 → 19; `_direct_period_comparison`
asked 4 times → 1.

## What is half-finished: Section C, the robustness corpus

On `worktree-agent-ae15c0861ca65d01e` at `b2fc1b3`. Two WIP commits, neither reviewed nor merged.

**What exists and works:**

- `evaluation/planning/robustness.yaml` — **52 bases, 389 rewordings, 235 recorded rejections, 16
  bases not admitted**. 624 generated, **37.7 % rejected**, every rejection carrying its reason
  verbatim.
- `scripts/generate_paraphrases.py`, `scripts/paraphrase_rules.py` — the generator and its
  admission rule. **The rule imports nothing from the planner, the recognisers or the prompt**, and
  is deliberately broader than any recogniser: *"a marker this table misses becomes a rejection, and
  a rejection is only ever a case not measured — never a false pass."* That is what makes the 37.7 %
  credible rather than suspicious.
- `evaluate_planning.py --dataset robustness`, with two new report rows: **ROBUSTNESS**
  (`invariance_held/invariance_total`) and **OVER-REFUSAL** as a rate over cases the dataset says are
  answerable.
- `tests/test_the_generated_corpus_is_not_the_generator.py` — 8 honesty properties, **7 green**.

**The design decision worth preserving:** `paraphrase_of` chains each sentence to the one it
rewrites, and the evaluator compares the two **produced** plans, never either against its
expectation. A base the system gets wrong whose every rewording is wrong the same way is robust —
that is a coverage defect, one fix closes them all, and separating the two is the entire point.

**What is missing:**

1. **The robustness number.** Never successfully measured — see the blocker below.
2. **The gate row in `docs/next.md`.** Untouched.
3. **The deliberate break.** Nothing has yet been broken on purpose to prove the gate can fail.
   This matters: the repository has found **seven instruments that were green because they had
   stopped executing what they measured**. A gate that can pass without doing anything is the eighth.
4. **One red honesty property**, and it is calibration rather than data — see next section.

## The one red property, measured

`test_the_meaning_rule_rejects_a_paraphrase_of_the_wrong_question` — an accepted paraphrase of one
base must be rejected against the others, *"or its yes means nothing"*. The bar is
`len(plan_bases) // 10`.

| | plan bases | bar | flagged | max fit |
| --- | ---: | ---: | ---: | ---: |
| before the re-filter | 41 | 4 | **0** | 4 |
| now | 33 | 3 | **38** | 4 |

**The maximum fit is 4 both times — nothing about any sentence changed.** Removing 8 plan bases
dropped the bar from 4 to 3. That test was passing with one unit of margin; it was one base-removal
away from failing all along. It is not only rounding, though: with 33 bases, fitting 4 genuinely is
above a tenth.

Root cause, anticipated by the rule's own docstring: the corpus holds **near-duplicate bases across
groups** — «¿cuánto gasté el mes pasado?» appears under three — and a surface rule cannot tell them
apart.

## The next step, and it is not any of the three obvious ones

The nine `follow_*` bases were removed because a continuation *"cannot be rewritten by somebody who
has not seen the turn before it"*. The agent's own docstring records that it tried and the generator
invented context: «¿Y este mes?» came back as «¿Cómo está mi saldo este mes?», «¿Y cuántas veces?» as
«¿Cuántas veces se ha producido este hecho?» — sixty sentences, none a rewording of the question
asked.

**That reasoning is right about a generator with no context, and `grep previous|context
generate_paraphrases.py` returns nothing — the generator never had a context to give.**

**Eight of the nine carry `previous` in `dev.yaml` already.** So: give the generator the preceding
turn, make the continuation bar conditional on the *absence* of `previous`, and restore them. Plan
bases go 33 → 41, the promiscuity bar returns to 4, and the red property closes **without touching a
ratchet and without shrinking the corpus**.

The ninth, `follow_without_context_must_ask`, has no `previous` **on purpose** — its content is the
absence of context — and stays out for a good reason rather than a mechanical one.

**One obstacle, and it decides the shape of the work.** The generator is forbidden to see *"the
planning prompt, the schema, the enum values, or which phrasings this system already handles"*. And
`previous` in the dataset is a **plan**, not a sentence:

```
follow_period_change_keeps_category  «¿Y este mes?»
  previous -> {'period': {'period': 'LAST_MONTH'}, 'direction': 'EXPENSE', 'categories': ['TRANSPORT_FUEL']}
```

Handing that over leaks the schema. Rendering it into Spanish needs a renderer that knows the enums:
the same leak by another door. So giving context means **writing the eight preceding questions in
Spanish by hand** — for `{LAST_MONTH, EXPENSE}` the preceding question is «¿Cuánto gasté el mes
pasado?». Eight human-written sentences, no leak.

Also worth recording, and it reframes the metric: a surface that fits four different bases is
**genuinely ambiguous**, and the correct product behaviour there may be to ask rather than to guess.
That would change dataset expectations, so it is a product decision and not a calibration fix.

## The blocker: no number is quotable on this machine right now

```
NVIDIA GeForce RTX 5060 Laptop   utilisation 14 %   VRAM 7440 / 8151 MiB   59 °C   throttle 0x0
```

**VRAM at 91 %, GPU at 14 %.** That pair means the work is not happening on the GPU. Ollama holds
5.4 GB; the remaining ~2 GB is taken by Teams (×2), Discord, Notion, Chrome (×2), Edge, about ten
`msedgewebview2.exe`, VS Code, M365Copilot, WidgetBoard, two NVIDIA overlays and Docker Desktop.
With no room for the `num_ctx 4096` KV cache, Ollama spills to system RAM.

Measured with planner-sized requests, not with a trivial prompt:

| | |
| --- | --- |
| this morning, via the harness | P50 **3.1 s** · P95 3.9 s |
| now, three attempts | **77 s · 32 s · 71 s** |

10–25× slower. The harness's per-request timeout is **120 s**, so some requests cross it and the run
dies with `provider failed` — a message that does not say "it does not fit in VRAM". Two attempts
died on **different** cases, which is how you tell this from a cold load or a poisoned case.

It also puts a footnote on every latency figure taken earlier today: they were measured with more
free VRAM than there is now.

## Environment gotchas that have each cost real time

- **Subagent worktrees are cut from `main` (`d062f03`), never from the current branch — 19 out of 19
  times.** Every brief must name the base commit and a *tell*: a file that exists only on the current
  branch. An agent cannot *be on* `task/visualization` because it is checked out in the main
  worktree; it resets its own worktree branch to that commit, which is the same tree under another
  name.
- **`evaluate_planning.py` buffers all output until the end.** A run in progress reads as an empty
  file. Do not conclude from silence that it did not start.
- **Long `pytest` runs on this machine intermittently stall**, including on a pristine base. Running
  in two halves works.
- **Never `docker compose --profile observability down -v`** — `finguard_postgres-data` lives in the
  same project and that flag takes it. Remove volumes by explicit name.
- **This service depends on `httpx2`, not `httpx`.** `import httpx` raises `ModuleNotFoundError`;
  `tests/test_dependency_provenance.py` holds three tests explaining why. So
  `opentelemetry-instrumentation-httpx` is not an option — outbound spans go through
  `infrastructure/banking/client.py`.
- Project rules: **atomic commits, never a `Co-Authored-By` trailer, never edit a `Ceiling` or a
  `Floor`, never edit a dataset case or corpus episode to move a number, and no shared package
  between `banking-api` and `orchestrator`** — the duplication is deliberate.

## Still open, beyond Section C

**The migration** (`docs/superpowers/specs/2026-08-30-scaling-the-language-surface-design.md`):

- **A2** — move the 19 readers into modules over A1's proven-equivalent base. A1's report says the
  hard part is the **dispatch**, not the selection: each claim routes differently. There is a
  `test_the_cascade_order_is_still_what_the_handler_does` that reads `_steps`' source; deleting it is
  the signal A2 is finished.
- **B** — the lexicon as data. Scope is measured: `explicit_facts.py` is 29 regexes and **one**
  capture group and becomes a table; `window_words.py` is 33 and **twenty-nine**, a date grammar, and
  stays code. Move a reader, its precondition and its patterns as one unit.
- **D** — declared override-rate bands per guard, over the observability counters.

**Product decisions nobody has taken.** These are not bugs:

- **«¿Dónde he gastado más dinero este año?»** — the reader answers `CATEGORY` and `enforce` imposes
  it, so **no model can pass the case**. Needs a decision about whether «dónde» means merchant or
  category. This is the largest remaining defect cluster: merchant-vs-category shows up on four of
  five sets, and the mechanism is that a sentence naming both a category and a merchant loses the
  category.
- **Two merchant cases** whose dataset expectation may be stale since `merchantFilter` shipped.
- **«bastante»** asks for a judgement, not a figure.
- **The two-turn cost of every spending question.** 22 of 69 real turns ended in
  `NEEDS_CLARIFICATION` because the holder has three accounts. Correct behaviour, expensive UX,
  now measured.

**Known findings recorded and not fixed:**

- `_how_many_payees_plan` has never claimed a turn across 1,341 sentences — dead code with a live
  regex, or a corpus gap.
- `_cumulative_plan` and `_contextual_follow_up_plan` hold the same branch twice; only ordering kept
  them apart.
- `_cumulative_plan` runs in the handler *before* the planner and is absent from the evaluation
  harness — a real gate the score cannot see.
- `plan_merger.merge` can still produce `CUMULATIVE + NONE` from a follow-up that resets the
  grouping; no set exercises it.
- `infrastructure/llm/` has four inline `AsyncClient` sites that still send no request id, so Ollama
  calls are uncorrelated.
- `GROUPS.md` has drifted: comparisons and ratios are still filed under `unsupported` while being
  shipped capabilities, and there is no group for rankings, recurrence or account scoping.
- `docs/architecture/monorepo.md` lists **OpenTelemetry** and a shared `finguard-observability`
  package. It is written in the conditional and describes an aspiration; neither exists, and the
  shared package would violate the service-independence rule.
