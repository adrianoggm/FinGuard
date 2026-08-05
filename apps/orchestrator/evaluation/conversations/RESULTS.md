# Reading a turn — can a model replace the rules?

```bash
cd apps/orchestrator && uv run python scripts/evaluate_interpreting.py --model qwen3:8b
                                                    --episodes 20     # a sample, ~70 s
                                                    --worst 20        # more confusion pairs
```

130 episodes, 879 turns, reference date 2026-08-05, Ollama seed pinned. The deterministic
rules are the reference and score 100 % by definition, so this measures **where a model
differs from them**, not whether it is right.

**818 of the 879 turns are scored.** The other 61 are inside an open payment: a preview owns
the next turn whatever its words are, and a half-built Bizum takes the reply that answers its
question, both before anything is classified. No interpreter is asked about those.

## Where it stands

`qwen3:8b` is the best of six local models measured. It is not good enough to promote.

| model | agreed | invented a payment | dropped one | ms/turn |
| --- | ---: | ---: | ---: | ---: |
| **qwen3:8b** | **81.3 %** | 15 | 4 | 722 |
| qwen3.5:4b | 79.2 % \* | 3 \* | 0 \* | 1194 |
| mistral-nemo:12b | 69.8 % \* | 4 \* | 0 \* | 1419 |
| granite4.1:8b | 68.8 % \* | 2 \* | 0 \* | 7007 |
| qwen2.5-coder:7b | 60.0 % \* | 0 \* | 0 \* | 706 |
| ministral-3:8b | 46.9 % \* | 8 \* | 0 \* | 1164 |

\* sampled over the first 15 episodes and measured before the harness was corrected, so the
payment columns are inflated. Only the `qwen3:8b` row is a full, corrected run.

## The gap is one confusion

| the rules say | the model says | turns |
| --- | --- | ---: |
| FINANCIAL_QUERY | ACCOUNT_BALANCE | **94** |
| FINANCIAL_QUERY | OWN_TRANSFER | 17 |
| FINANCIAL_QUERY | PAYMENT | 13 |
| FINANCIAL_QUERY | LIST_CATEGORIES | 9 |
| FINANCIAL_QUERY | LIST_CONTACTS | 6 |
| everything else | | 14 |

94 of 153. Fix that one and the rate is ~92 %.

It is not one thing, though. Two kinds of sentence are in it:

    "¿cuánto gasté el mes pasado?"   spending. Not a balance in any context.
    "¿cuánto gasté?"                 the same.
    "El total"                       ambiguous — depends on the question before it.
    "En mi Hucha ?"                  ambiguous — a balance after a balance, a filter after a query.

The ambiguous half is the interesting one and the unambiguous half is the worrying one. A
model that reads "¿cuánto gasté?" as a balance question answers a real figure to a question
nobody asked, which is the failure this service is built around not having.

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

The prompt is not the bottleneck. Nothing was kept.

## What a promotion would need

`Promoted` already refuses to let a model start a payment the rules did not find, and refuses
to let it drop one they did. Those 19 turns are therefore contained by construction. What is
not contained is the 94: a balance answered to a spending question is a real figure answering
a different question, and no guard downstream can tell.

So the bar is that confusion, not the headline rate. Either a model that does not make it, or
a reason the rules keep the balance decision while the model widens everything else.
