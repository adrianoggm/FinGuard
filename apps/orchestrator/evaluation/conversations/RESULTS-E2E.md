# The conversation corpus over HTTP — properties, not accuracy

The other results file in this directory measures a model against the deterministic router. This
one measures the deployed service against the same episodes: question in over HTTP, figure and
sentence out, and a list of properties that must hold on every turn. A violation is a bug rather
than a long tail, which is why the recipe exits non-zero on any.

```bash
just back                                     # the stack has to be up: this exercises the service
just evaluate-conversations demo-token-laura-pays
```

**Use the paying token.** `demo-token-laura-0001` holds `questions:read` and the two metric
scopes and nothing else, so every payment episode fails on the capability guard before it reaches
anything worth measuring — 17 violations that say nothing about the service. `demo-token-laura-pays`
is the one in `compose.yaml` that carries `payments:prepare` and `payments:confirm`.

## Measured 2026-08-23, at `858ad77`

63 threads, 132 turns. Planner: `ollama` / `qwen3.5:9b`, warmed before the run. **67 property
violations**, and the number on its own is misleading — most of it is one stale expectation
repeated.

| | violations | |
| --- | ---: | --- |
| `b928d90`, before the Phase 4 visualisation work | 115 | the branch point |
| `858ad77`, the same run | 115 | identical, episode for episode: no regression |
| with `pending_plan` counted as a half-built draft | 80 | 94 false violations removed |
| with the paying token and a local planner | **67** | |

The first two lines are the useful pair. They were run under identical conditions and the
violation sets differ by three lines, each of them one secondary reason on a turn that already
failed on both sides — the spread a model produces between runs. Nothing in Phase 4 moved this
corpus.

### Where the 67 are

| | turns | |
| --- | ---: | --- |
| The script expects a plan; the service asks which account | 50 | a decision, below |
| One Bizum episode | 3 | real |
| Everything else | 14 | real, itemised below |

**The 50 are a corpus that predates account scope.** Laura holds three accounts, so "¿cuánto
gasté el mes pasado?" names no account and the service asks which one. The episodes were written
when it answered directly: their first turn asserts `ran: {window: LAST_MONTH, ...}`, the service
asks a question instead, and every assertion after it is unreachable. That is the whole of
`NOT_PLANNED_SO_NO_WINDOW` and its siblings.

It is not a defect on either side, and it will not resolve itself. Somebody has to pick one:

- **Teach the episodes to answer.** A turn that says «la corriente» after the question, and the
  conversation continues as written. Sixty-odd turns of dataset work, and it makes the corpus
  assert the account-scope behaviour rather than route around it.
- **Ask less.** Default to the current account and ask only where the answer would differ.
  Cheaper in dataset work and a product change: it decides for somebody which of their accounts a
  bare question was about.

### The 14

Six are `DOES_NOT_SAY` — the figure is right and the sentence does not say what it is a figure of.
`the_largest_expense_is_one_movement_not_a_total`, `the_answer_says_what_the_figure_is_of`,
`the_largest_expense_and_the_next_one` (twice), `the_balance_is_not_todays_movements`,
`a_question_in_the_middle_of_a_bizum_is_still_a_question`. This is the complaint the property was
added for, and it is the largest real cluster here.

Two are `CLARIFICATION_RESTARTED` — `rent_then_period` and `rent_then_last_month`. It asked, the
reply supplied exactly what it asked for, and it asked again. Also the defect its property was
written for, and visible only now that the account-scope noise is separated out.

The rest, one each:

| turn | violation | what it means |
| --- | --- | --- |
| `a_grouped_comparison_of_income_then_one_account_of_spending` | `GROUPS_DO_NOT_SUM_TO_TOTAL`, `PLAN_ANSWER_MISMATCH` | a grouped income comparison whose parts do not add up to its own total — the figure-integrity property, and the one worth reading first |
| `the_salary_side_of_an_account` | `WRONG_DIRECTION` | the salary side of an account answered from the other direction |
| `a_habit_asked_about_by_month` | `HTTP_422` | the planner produced a plan the service itself refuses. Appeared with the local planner and not with the hosted one, so read it as model-dependent |
| `a_question_about_people_is_answered_with_people` | `NO_GROUP_ELENA` | a group the episode requires is missing from the answer |
| `an_average_is_refused_by_name` | `EXPECTED_UNSUPPORTED`, `DOES_NOT_SAY` | the episode expects an average to be refused. Averages are supported now (`Metric.AVERAGE`, and `14-averages.yaml` asserts them), so the stale side here is probably the episode |
| `an_unanswerable_question_keeps_the_context` | `EXPECTED_UNSUPPORTED` | |

## Three ways to waste a run

All three cost one here, and none of them is visible in the output as itself.

**Restarting the service under it.** The run is HTTP against a live container for ten minutes or
so. A `docker compose restart` mid-run reports as `TRANSPORT: Server disconnected` on nearly every
episode, which reads like a service that fell over rather than one that was pushed.

**Reading the exit code of the wrong thing.** `just evaluate-conversations ... > log; echo $?`
reports the `echo`, not the recipe. The recipe's own failure is the last line of the log.

**A planner that stops answering.** `PROVIDER_ERROR` on most turns means the planner, not the
service. Two causes seen: a hosted provider running out of quota after several consecutive runs —
113 violations in one run, zero in the three before it — and Ollama unloading the model between
runs, which costs about 50 seconds to reload and can time out the first turns. Warm it first:

```bash
curl -s http://127.0.0.1:11434/api/generate \
  -d '{"model":"qwen3.5:9b","prompt":"hola","stream":false,"keep_alive":"60m"}' >/dev/null
curl -s http://127.0.0.1:11434/api/ps      # the model has to be resident before the run
```

**And which planner answered belongs with the number.** `ORCHESTRATOR_PLANNER_PROVIDER` and
`ORCHESTRATOR_PLANNER_MODEL` in `apps/orchestrator/.env` decide it, that file is git-ignored, and
`config/llm.toml` is a different knob — it chooses who *writes the sentence*, not who plans. A
figure quoted without naming the planner is not reproducible. The local `qwen3.5:9b` measured
better than the hosted `@cf/qwen/qwen3-30b-a3b-fp8` on the one comparison here: no provider
errors, and `DOES_NOT_SAY` at 10 against 18.
