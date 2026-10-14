# Historical experiment — a model against deterministic turn routing

> The script that produced it, `scripts/evaluate_interpreting.py`, was removed with the rules
> route in October 2026.

> **A snapshot, not the current state.** Measured on 2026-08-06 against `326e2f6`, with the
> corpus at 13 files and the reference date 2026-08-05. The table below and everything under
> "Open" describe the service as it was *before* the Phase 5 routing work, and two things have
> moved since:
>
> - **`Intent.COURTESY` exists.** The 57 turns of «gracias» reaching a planner — the finding this
>   document argues hardest for — are handled deterministically now, and the corpus asserts that
>   they do not reach a model. Read §"The 57 are a defect in this service" as the reason the
>   intent was added, not as an open problem.
> - **The corpus has grown**, from 130 episodes and 879 turns to 166 and 1125, so the
>   denominators here no longer match the ones `test_conversation_corpus` prints.
>
> Kept whole rather than updated, because the value in it is the experiment: seven local models
> measured under one harness, three prompt changes that failed, and what each of them cost. Any
> figure below is evidence about 6 August. It is not a capability statement about `main`.


```bash
cd apps/orchestrator && uv run python scripts/evaluate_interpreting.py --model qwen3:8b
                                                    --episodes 20     # a sample, ~70 s
                                                    --worst 20        # more confusion pairs
```

130 episodes, 879 turns, reference date 2026-08-05, Ollama seed pinned. The deterministic
rules are the reference and score 100 % by definition, so this measures **where a model
differs from them**, not whether it is right.

Whether the rules are right is a different measurement, and it is `test_conversation_corpus`
over the same files: every turn may state the route that had to answer it and, with `ran`, the
query it had to execute. That second one is what makes `lost_context` an observation rather
than a tautology — before it existed the metric counted only a workflow failing to survive, so
an abbreviated turn inheriting the wrong window or dropping its category scored as correct.
Do not read "100 % by definition" below as "the rules are correct": it means this table is
about the model.

**818 of the 879 turns are scored.** The other 61 are inside an open payment: a preview owns
the next turn whatever its words are, and a half-built Bizum takes the reply that answers its
question, both before anything is classified. No interpreter is asked about those.

## Where it stands

Seven local models measured. None is good enough to promote whole, and the agreement rate is
the least interesting thing about any of them.

| model | agreed | invented a payment | dropped one | ms/turn |
| --- | ---: | ---: | ---: | ---: |
| **qwen3.5:9b** | **83.3 %** | 18 | 0 | 1869 |
| qwen3:8b | 81.3 % | 15 | 4 | 722 |
| qwen3.5:4b | 79.2 % \* | 3 \* | 0 \* | 1194 |
| mistral-nemo:12b | 69.8 % \* | 4 \* | 0 \* | 1419 |
| granite4.1:8b | 68.8 % \* | 2 \* | 0 \* | 7007 |
| qwen2.5-coder:7b | 60.0 % \* | 0 \* | 0 \* | 706 |
| ministral-3:8b | 46.9 % \* | 8 \* | 0 \* | 1164 |

\* sampled over the first 15 episodes and measured before the harness was corrected, so the
payment columns are inflated. Only the top two rows are full, corrected runs.

Two points between 8B and 9B, and the two points are a lie in both directions. What actually
changed is the *shape*.

## 8B: the gap is one confusion, and it is a comprehension failure

| the rules say | the model says | turns |
| --- | --- | ---: |
| FINANCIAL_QUERY | ACCOUNT_BALANCE | **94** |
| FINANCIAL_QUERY | OWN_TRANSFER | 17 |
| FINANCIAL_QUERY | PAYMENT | 13 |
| everything else | | 29 |

94 of 153, and half of those are not ambiguous at all:

    "¿cuánto gasté el mes pasado?"   spending. Not a balance in any context.
    "¿cuánto gasté?"                 the same.
    "El total"                       ambiguous — depends on the question before it.
    "En mi Hucha ?"                  ambiguous — a balance after a balance, a filter after a query.

A model that reads "¿cuánto gasté?" as a balance question answers a real figure to a question
nobody asked, which is the failure this service is built around not having.

## 9B: that failure is gone, and what replaces it is more interesting

| the rules say | the model says | turns |
| --- | --- | ---: |
| FINANCIAL_QUERY | IDENTITY_OR_HELP | **57** |
| FINANCIAL_QUERY | ACCOUNT_BALANCE | 19 |
| FINANCIAL_QUERY | PAYMENT | 16 |
| FINANCIAL_QUERY | OWN_TRANSFER | 13 |
| CORRECTION | FINANCIAL_QUERY | 12 |
| ACCOUNT_BALANCE | FINANCIAL_QUERY | 9 |
| everything else | | 11 |

**The balance confusion collapsed 94 → 19** and «¿cuánto gasté?» is not in it. What is left is
the genuinely ambiguous ellipsis — plus one where the model is right and the rules are wrong:

    "cunato tengo"   the rules say FINANCIAL_QUERY. The typo defeats the regex.
                     The model says ACCOUNT_BALANCE, which is what the person asked.

So the spending/balance ceiling was the model, and one size up clears it.

### The 57 are a defect in this service, not in the model

«gracias», «hola», «buenas», «eso es». The rules have nowhere to put a pleasantry, so the
catch-all takes it — and the corpus records where the catch-all sends it:

    - say: gracias
      route: QUERY        # it reached the financial planner

A language model is invoked to build a financial plan for the word «gracias». The model
answers IDENTITY_OR_HELP because that is the closest of nine intents to "not about money", and
it is more right than we are. There is no tenth intent for small talk, and that is the finding.

Judged that way the 9B makes **80 real errors to the 8B's ~151** — roughly half — and the
headline rate barely moves because most of the difference is a model disagreeing with a bug.

### What the 9B is worse at

`CORRECTION → FINANCIAL_QUERY` went 2 → 12: «no es eso», «me refería a gasolina», «quería
decir el año pasado». A correction read as a new question re-plans from scratch instead of
patching the previous plan, and the user has to say it twice.

`ACCOUNT_BALANCE → FINANCIAL_QUERY ×9` is new: «¿cuánto tengo en el fondo?» dropped into the
query path. The balance confusion did not disappear, it turned around.

**A bigger model is not monotonically better.** It moved the errors; it did not remove them.

### Latency

1869 ms per turn, 2.6× the 8B, 25 minutes for the corpus. Not interactive. Measured anyway,
because the question was whether size is the ceiling, and that is answered before latency
gets a vote.

## Three prompt fixes, all measured, all worse

Each was a hypothesis with a mechanism, and each is written down because the next person will
have the same idea.

**1. Name the holder's accounts in the prompt.** Every model read `pásame 50 al fondo` as
paying somebody named *el fondo*, and nothing in the prompt said *fondo* is an account of
theirs. Added the vocabulary from `account_words._KINDS`.

> **82.3 % → 69.8 %.** ACCOUNT_BALANCE confusions went 8 → 19: naming accounts made the model
> see balance questions everywhere. And `pásame 50 al fondo` *still* read as a payment, so the
> hypothesis was false — it was never missing information.

**2. Strengthen the ACCOUNT_BALANCE line** to say a question about spending is never a balance.

> **82.3 % → 81.3 %.** One turn. The set of confused sentences changed and the count did not.

**3. Give FINANCIAL_QUERY a positive anchor** instead of leaving it as "everything else",
naming a spending question as an example of it.

> **81.2 % → 71.3 %.** The balance bucket shrank and IDENTITY_OR_HELP went to 13 — «gracias»,
> «vale», «hola», «perfecto». Making the catch-all specific stopped it being the catch-all.
> Its vagueness was load-bearing.

The prompt is not the bottleneck on the 8B. Nothing was kept.

Worth reading next to the 9B run: experiment 3 produced, on a smaller model, exactly the
pleasantry bucket the 9B produces unprompted. Two different routes to the same place, which is
what a real gap in the intents looks like rather than a model having a bad day.

## What a promotion would need

`Promoted` already refuses to let a model start a payment the rules did not find, and refuses
to let it drop one they did. The payment columns above are therefore contained by construction.

What is not contained is a balance answered to a spending question — a real figure answering a
different question, which no guard downstream can tell from a right one. On the 8B that is 94
turns and the whole argument. On the 9B it is 19, all of them genuinely ambiguous.

So the bar is that confusion, not the headline rate, and the shape of the answer is a hybrid
rather than a flip: the rules keep the decisions where being wrong is expensive and cannot be
detected, the model widens the rest. `Promoted` is already that shape for payments; the balance
and the correction are the two candidates for the same treatment.

## Open, in the order they are worth doing

1. ~~**Somewhere for a pleasantry to go.**~~ **Done.** It became both of the options named here:
   `Intent.COURTESY` and a deterministic reader in front of any interpreter. No turn of «gracias»
   reaches a planner, and the corpus fails if one does.
2. **Few-shot.** All three prompt experiments were *instructions*. Nobody has tried three
   worked sentence → intent pairs, which is a different mechanism and usually the stronger one
   for classification.
3. **`qwen2.5-coder:14b-instruct`**, installed and unmeasured. The 8B→9B step moved the error
   profile rather than shrinking it, so a third point on that curve is worth having before
   anybody concludes size is the answer.
4. **Reasoning.** The provider runs `think: false`, and that is a correctness requirement with
   Ollama, not a preference: `think: true` plus `format` puts the JSON in the thinking channel
   and returns an empty `response`. Testing it means adapter work first.
