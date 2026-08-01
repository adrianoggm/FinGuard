# Planning accuracy — measured, not argued

Reproduce with:

```bash
just evaluate-planning qwen2.5-coder:7b        # or any Ollama tag
cd apps/assistant-api && uv run python scripts/evaluate_planning.py \
    --model qwen2.5-coder:7b --repeat 3        # to see the spread
```

The dataset is [dev.yaml](dev.yaml): 60 Spanish sentences, reference date pinned to
2026-07-15, three outcome classes. **These are development-set numbers.** The prompt was
tuned against them, so they are an upper bound — the held-out set does not exist yet.

## Where it stands

All four candidates fit a 6.6 GB laptop GPU and run fully resident with `num_ctx: 4096`.
Seed pinned, so a repeated run gives an identical score.

| model | size | outcome | exact plan | **INVENTED** | missed | categories | P50 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| **qwen2.5-coder:7b** | 4.7 GB | **88.3 %** | **85.7 %** | **2** | **1** | 85.7 % | 2.1 s |
| granite4:micro | 2.1 GB | 71.7 % | 63.3 % | 1 | 13 | 64.3 % | — |
| qwen3.5:4b | 3.4 GB | 68.3 % | 59.2 % | 2 | 16 | 78.6 % | 2.6 s |
| phi4-mini | 2.5 GB | 65.0 % | 59.2 % | 3 | 13 | 78.6 % | — |

**Invalid structure: 0 out of 240 calls.** Constrained decoding holds absolutely, which
is why the parser's structural branch is insurance rather than routine.

`qwen2.5-coder:7b` is the configured default. That it wins is the least expected result
here: a coding model beating three instruction-tuned models at reading Spanish. The
reason is visible in the table — the other three abstain far too often (13–16 of the 49
answerable cases), and that is the one number a bigger or newer model did not fix.

## What actually moved the numbers

| change | effect |
| --- | --- |
| **Spanish glossary for the categories** | categories 28.6 % → 92.9 %, exact 71.4 % → 85.7 % |
| **`num_ctx: 4096`** | no accuracy change; P50 5.28 s → 2.58 s, max 31.2 s → 3.6 s |
| **Pinned seed** | no accuracy change; made measurement mean anything |
| Softening the abstention rule | **rejected** — invented plans 2 → 5 |

The glossary is the finding worth carrying to the next model. The schema constrained the
output to a list of English identifiers and never said what they meant, so the model was
guessing that "el súper" is `GROCERIES` — and guessing wrong most of the time. It was not
a model problem, and no amount of downloading would have fixed it.

The seed matters more than it sounds. Before pinning it, the same prompt against the same
model scored 91.7 %, 88.3 % and 86.7 % on three runs. That spread is enough to invent an
improvement, and it nearly did.

## What is still wrong, and why it is left

| case | what happens |
| --- | --- |
| `merchant_netflix` | Answers with the `SUBSCRIPTIONS` total, which also covers Spotify and the gym. **The schema has no merchant filter** — this is a gap in the contract, not the model. |
| `unsupported_comparison` | Answers "¿he gastado más este mes que el anterior?" with one of the two months. A comparison is two plans; the schema expresses one. |
| `direction_me_cobraron` | "Me cobraron" read as income. It is listed under EXPENSE in the prompt and still gets it wrong. |
| `group_by_merchant` | "¿En qué comercios?" grouped by category. |
| `category_farmacia`, `group_by_month` | Emits a full calendar year and is refused for reaching past today — caught rather than answered, which is the intended failure. |

The two INVENTED cases are both contract gaps rather than comprehension failures, and
both would be closed by adding a merchant filter and a comparison shape. That is a change
to what a financial query can express, which is banking's decision to make.
