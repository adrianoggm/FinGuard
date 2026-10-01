# Visual planner evaluation

Model: `ministral-3:14b` on `ollama`; temperature 0.0, max_output_tokens 384, budget 6 s.

16 cases · 14 enter the policy · 4 equivalent paraphrase pairs · 2 instructed

One run is not a number -- see the script's own docstring. Run three times with the GPU alone and take the minimum before treating any of these as a release figure.

| measure | value |
| --- | --- |
| cases entering the policy | 14 |
| declined (the floor itself found nothing to draw) | 0 |
| validity | 3 / 14 |
| suitability (mark and structure) | 3 / 3 |
| changed_from_baseline | 2 / 3 |
| **valid, suitable and changed** | 2 / 14   <- the figure that carries the argument |
| stability under equivalent paraphrase | 0 / 1 (3 pairs had no proposal) |
| sensitivity (the instruction's own effect, on verified proposals) | 0 / 0 (2 instructed had no proposal) |
| no-chart cases kept out | 2 / 2 |
| added turn latency P50 / P95 (ms) | 5137 / 6031   <- P95 is the budget candidate |
| fallback reasons | {}; rejected rules {} |

Latency is added to every turn the model runs in shadow or hybrid mode: the answer waits for the planner or for its budget.

Stability breaks:
- temporal_evolution_p1: differs from temporal_evolution

## Per group

| group | entered | valid | suitable | valid_suitable_changed |
| --- | --- | --- | --- | --- |
| breakdown | 4 | 0 | 0 | 0 |
| comparison | 2 | 0 | 0 | 0 |
| cumulative | 1 | 0 | 0 | 0 |
| frequency | 1 | 0 | 0 | 0 |
| no_chart | 0 | 0 | 0 | 0 |
| ranking | 1 | 0 | 0 | 0 |
| ratio_share | 2 | 1 | 1 | 1 |
| recurrence | 1 | 0 | 0 | 0 |
| temporal | 2 | 2 | 2 | 1 |
