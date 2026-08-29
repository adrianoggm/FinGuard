# Capability families in the unit suite

`3070 passed` says nothing about what the product can do, and a red run does not say
whether it was a window, a follow-up or a payment. This is the label that makes the
suite readable beside the planning report.

The vocabulary is not new. [`evaluation/planning/GROUPS.md`](../evaluation/planning/GROUPS.md)
names sixteen capability groups and argues each one; the planning harness already scores
per group. The unit suite carried no such label, so `follow_up 62%` and `3070 passed`
described the same capabilities and neither knew it. `tests/families.py` supplies the
missing half, and `pytest_terminal_summary` in `tests/conftest.py` prints it.

## Derived, not declared

No test carries a marker. Annotating 3070 tests would be a diff nobody could review, it
would make every new test remember a marker or silently lose its family, and it would
collide with anyone else editing test files. The family is derived from what a test
already says about itself, by two rules and a bucket:

1. **The dataset's own label.** `test_capability_guard` and `test_plan_verifier` are
   parametrised over the cases in `evaluation/planning/*.yaml`, and every one of those
   cases already declares a `group`. Where a test id carries a case id, that group *is*
   the answer. This is the only rule that can label one test inside a large file, and it
   is authoritative by construction: it is the same label the planning report uses.
2. **The file it lives in.** The table in `tests/families.py` maps a path fragment to a
   family. First match wins.
3. **`unmapped`.** Everything else, counted honestly and never hidden.

A file the table has never seen lands in `unmapped`. That is the designed outcome for a
new test, not a failure, and nothing in the derivation may raise.

## What the mapping is worth

| | tests | share |
| --- | ---: | ---: |
| labelled by the dataset's own `group` | 420 | 13.7% |
| labelled by the path table | 705 | 22.9% |
| `unmapped` | 1948 | 63.4% |

**Nearly two thirds of the suite has no capability group, and that is the finding, not a
gap to be closed by stretching the table.** GROUPS.md describes what the *planner* can be
asked. The suite is mostly other things — the conversation corpus (189), the intent
router (119), payment routing and payment words (197), the end-to-end harness (69),
banking and provider clients, accounts, authorisation, logging. A user would recognise
"sending a Bizum" as a capability, but GROUPS.md has no word for it, and inventing one
here would put a second vocabulary next to the one that is already argued.

Per family, out of 3073 collected:

| family | tests | from the dataset | from the path table |
| --- | ---: | ---: | ---: |
| `period_named` | 71 | 42 | 29 |
| `period_confusable` | 38 | 38 | 0 |
| `period_absolute` | 130 | 26 | 104 |
| `direction` | 38 | 38 | 0 |
| `category_single` | 91 | 46 | 45 |
| `category_multiple` | 20 | 20 | 0 |
| `metric` | 42 | 26 | 16 |
| `grouping` | 38 | 38 | 0 |
| `merchant` | 16 | 16 | 0 |
| `follow_up` | 201 | 31 | 170 |
| `ambiguity` | 42 | 13 | 29 |
| `unsupported` | 201 | 11 | 190 |
| `noise` | 140 | 32 | 108 |
| `injection` | 37 | 23 | 14 |
| `out_of_domain` | 6 | 6 | 0 |
| `boundary` | 14 | 14 | 0 |
| `unmapped` | 1948 | | |

## Seven families have no test file of their own

`period_confusable`, `direction`, `category_multiple`, `grouping`, `merchant`,
`out_of_domain` and `boundary` have **zero** dedicated unit tests — the whole "from the
path table" column is 0. Every test they own arrives by being swept into a
dataset-parametrised guard or verifier case. They are measured by the planning harness
and by nothing a person wrote for them, and `merchant` (16), `boundary` (14) and
`out_of_domain` (6) are the thinnest cells in the suite.

## The table

Files covering several groups at once — `test_explicit_facts`, `test_intent_router`,
`test_e2e_checks` — get no row on purpose. Picking one of their groups would be a guess,
and rule 1 already labels the parametrised tests inside them case by case.

| family | files |
| --- | --- |
| `period_named` | `natural_period*`, `holder_timezone`, `*uses_the_holder_timezone` |
| `period_absolute` | `window_words` — months, years and explicit ranges, not the nine names |
| `category_single` | `category_words`, `category_misread` |
| `metric` | `frequency`, `how_many_subscriptions` |
| `unsupported` | `capability_guard`, `the_guard_owns_refusals`, `guard_policy`, `percentages`, `comparison_plan` |
| `follow_up` | `continuation_patch*`, `planning_focus_readers`, `plan_merger`, `plan_patch`, `result_reference`, `a_follow_up_said_differently` |
| `ambiguity` | `plan_draft`, `clarification_round_trip`, `a_question_is_not_asked_twice` |
| `noise` | `a_pleasantry_is_not_a_query`, `accents_do_not_hide_a_rule`, `the_same_question_said_differently` |
| `injection` | `prompt` |

Three judgement calls worth knowing about. `window_words` is `period_absolute` rather
than `period_named` because its tests are about months, named years and explicit ranges.
`percentages` and `comparison_plan` sit under `unsupported` because GROUPS.md names
averages, comparisons and ratios as what that group is for — though both are now
supported, which is a sign the group's name has drifted from its meaning.

And `test_explainer` had a row here — `out_of_domain`, the assistant answering about
itself — until `category_words` was broken on purpose and the wrong family lit up. Over
half that file asserts on the category vocabulary, so the row was removed rather than
kept with a caveat. It is the reason `out_of_domain` now shows 6 tests instead of 24, and
the honest number is the smaller one.

## What it prints

Green, one line:

```
capabilities: 16/16 families green, 1140 tests mapped, 1930 unmapped
3070 passed, 3 skipped, 2 deselected in 967.91s (0:16:07)
```

(1143 mapped tests are collected; three of them are skipped, which is the difference.)

Red, only the families with failures, worst first:

```
--------------------- capabilities limping (1) ----------------------
  follow_up             1 failed    200 passed
  (15 other families green, 1143 tests mapped)
```

It runs once, after the last test, from `terminalreporter.stats` — no per-test cost, no
collection hook, nothing during the run. `-qq` silences it.

## Changing it

Edit the table in `tests/families.py`. A row earns its place only when the capability is
what the file is *about*; a file covering no group belongs in `unmapped`, because the
size of that bucket is a measurement and padding it out would destroy the measurement.
