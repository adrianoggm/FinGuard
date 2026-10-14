# Groups

> Historical. `scripts/evaluate_planning.py`, which scored these groups, was removed with the
> rules route in October 2026; the groups still label the unit suite (`tests/FAMILIES.md`).

Every case declares the functionality it exercises. A single accuracy number says the
assistant is 85% right and nothing about *what* it is wrong at; per-group scores say
"dates are fine, breakdowns are not", which is a thing you can act on.

The groups are the product's capabilities, not the code's structure. `follow_up` is one
group even though it touches every field, because "asking a follow-up question" is one
thing a user does.

| group | what it exercises | why it can fail |
| --- | --- | --- |
| `period_named` | The nine window names, used directly | Vocabulary — does it know THIS_WEEK exists |
| `period_confusable` | The four pairs a model conflates | Semantics — LAST_MONTH is not the last 30 days |
| `period_absolute` | Named months and years, explicit ranges | Knowing when a window is *not* one of the nine |
| `direction` | gasté / cobré / me devolvieron | Spanish verb sense, from the holder's side |
| `category_single` | One colloquial category word | Mapping "el súper" onto an English identifier |
| `category_multiple` | Two or more at once | Not collapsing to the first one it recognises |
| `metric` | cuánto vs cuántas veces | One word changes SUM to COUNT |
| `grouping` | por semanas / comercios / categorías | Choosing a breakdown, and not adding one unasked |
| `merchant` | Questions about one shop | A contract gap: there is no merchant filter |
| `follow_up` | A sentence that only makes sense after another | Inheriting what carries and dropping what does not |
| `ambiguity` | Sentences that determine no plan | Whether it asks instead of guessing |
| `unsupported` | Averages, comparisons, forecasts | Whether it refuses instead of approximating |
| `noise` | Greetings, filler, typos, mixed case | Whether surface noise changes the plan |
| `injection` | Instructions in the question, or in stored data | Whether the question can rewrite the rules |
| `out_of_domain` | Not about this account's money | Whether it declines rather than improvises |
| `boundary` | Values at the edge of what the domain accepts | Off-by-one at a ceiling, backwards ranges, the future |

## Splits

| file | cases | used for |
| --- | ---: | --- |
| [dev.yaml](dev.yaml) | 120 | Prompt work. Read it, tune against it, expect optimistic numbers. |
| [validation.yaml](validation.yaml) | 79 | **Was held out, and is not any more.** Eleven models were compared on it and one was chosen partly on its `unsupported` score here, which makes it a model-selection set. Still the honest number for a change to the prompt; no longer a release number. |
| [adversarial.yaml](adversarial.yaml) | 34 | Attacks and edge cases. Run it before believing anything. |

Run one with `--dataset`:

```bash
cd apps/orchestrator
uv run python scripts/evaluate_planning.py --model qwen2.5-coder:7b --dataset validation
```

The split matters more than the size. A prompt tuned against 110 cases scores well on
those 110 by construction; the only number that means anything to somebody else is the
one from a set the prompt has never seen.

Some cases carry `paraphrase_of`. Those say the same thing differently, and a model that
gets one right and its paraphrase wrong is matching surface strings rather than reading —
which the aggregate score would hide.
