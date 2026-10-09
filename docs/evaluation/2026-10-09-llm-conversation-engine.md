# LLM conversation engine evaluation — status 2026-10-09

## Status

Task 7 is **incomplete**. No rules-vs-model A/B run, local-model run, blind judging, or full generalization run is available in this checkout. The current Groq provider returned HTTP 429 during the live capability replay, so its partial runs cannot support a capability comparison or the phase-1 success decision.

## Available observations

| Run | Scope | Result | Use in the evaluation |
|---|---|---|---|
| `2026-10-09-task8-current-groq-120b-v2` | Initial current-engine control | 5 of 11 turns saved; two asked for clarification, three returned provider errors. Logs from that build showed HTTP 429. | Partial failure record only. |
| `2026-10-09-task8-current-groq-120b-fixed` | Current engine, graph off, full capability file | 10 turns saved; all 10 returned `PROVIDER_ERROR`. Rate-limit logging reports `Retry-After` up to 1881 seconds. | Provider availability only; no semantic comparison. |
| Temporary one-turn control smoke after 240 seconds | Same comparative question, graph off | Groq answered, but the engine returned `NEEDS_CLARIFICATION` asking which periods to compare. | Confirms the first prompt correction did not reliably fix the control. |
| Temporary one-turn graph smoke | Same comparative question, graph on | `PLANNED` in 3.8 seconds; two Groq calls and two successful banking reads. | One graph-on observation only; not paired or judged. |

The prompt now has an additional few-shot example using the exact sentence from the control smoke. Its live behavior is **unverified**: the next attempt was rate-limited. The 31-minute `Retry-After` and quota headers do not identify which account or service limit caused the 429. The isolated orchestrator was stopped after the attempts; the shared stack was not restarted.

Run records are in [`evaluation/engine/runs`](../../apps/orchestrator/evaluation/engine/runs/).

## Interpretation footprint

Measured with `apps/orchestrator/scripts/interpretation_footprint.py` on the branch after the prompt update:

| Side | Count | Scope |
|---|---:|---|
| Rules interpretation | 16,644 lines | 31 files. Includes the complete 7,116-line `ask_financial_question.py`; only part of that file is turn interpretation. |
| Model-engine interpretation | 960 lines | `operations_prompt.py`, `operations_schema.py`, `llm_turn_operations.py`. |
| Engine non-linguistic code | 1,717 lines | Grounding, typed operations, state, transitions, answer handler, executor, presenter and dispatch. |
| Engine storage | 32 lines | In-memory dialogue store. |

The model interpreter footprint is 5.8% of the counted rules footprint. This is a source-size measure, not a result-quality measure, and the rules count overstates interpretation by counting the whole handler.

## What remains before a decision

1. Complete the frozen regression and generalization runs for rules and model engine; complete the hosted Groq run if the provider accepts it.
2. Blind-judge paired runs and record conversation success, continuity, figure correctness, recovery, latency and model calls.
3. Use those measurements to evaluate the predeclared success criteria. No success or failure decision is made by this partial report.

The Groq 429 is currently the external blocker for hosted runs. No Ollama model was loaded during the recorded attempts, so local-model results are also absent.
