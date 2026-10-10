# LangGraph visual continuity browser check

**Date:** 2026-10-10
**Environment:** isolated `finguard-graph-test` stack, graph flag on, seeded synthetic bank, Chrome headless.

The browser conversation asked for all expense categories over the last three months, requested a chart, then changed it to lines. The query returned nine categories and 5,867.59 EUR across 87 movements. The first rendered chart showed eight of nine categories. After the display-limit fix below, the rerun rendered all nine and the line restyle produced a Vega line mark. Neither run produced browser console errors.

The run exposed three presentation issues. The chart acknowledgement used the malformed phrase “desglose de en los últimos…”. The local model classified a requested restyle as chart creation. And the renderer's default limit hid the ninth category. The graph now uses a neutral, grammatical acknowledgement, and the active-chart state tells the structured planner to emit `update_visualization` for presentation changes. The graph passes the category row count as its display limit for an unbounded category breakdown; other charts retain the default of eight. Rebuilt the isolated orchestrator and repeated the browser flow: the restyle returned “He actualizado el gráfico con la misma consulta.”, all nine category labels were visible, and the DOM contained a `line mark`.

Focused tests cover the active-chart instruction, the create/update acknowledgements, all rows of a complete category grouping, retained result lineage, and no additional banking reads for chart operations. Four focused tests passed; Ruff and mypy passed. This is a browser smoke check, not the full corpus gate or promotion of the feature flag.

## Conversation continuity follow-up

The live API run then exercised annual monthly totals and an interrupted comparison. “Which months
had the most expenses in the last year?” returned the top three month totals; “show all months”
patched the same query and returned every monthly bucket. A category query followed by “which
category increased most and why?” opened a comparison clarification. The next “return to the previous
query” exposed a state bug: model-planned clarifications were not persisted as pending, so restore
looked for older history and failed even though the last answered query remained on screen.

The transition now persists an open question when an operation cannot apply, including a comparison
that lacks periods. Restoring while that question is open clears the unfinished question, removes
its stale turn from recent context, and reruns the current answered focus. The real-browser sequence
now continues from that restore with “and in October?” and patches the same category query to October.
It rendered the chart and line restyle, displayed all nine categories, and showed October's 7
movements; Chrome reported no JavaScript errors.

The API also returned the top three months for the last year, then all monthly buckets after “show
all months”. Focused tests cover restoring an interrupted comparison, preserving the next period
follow-up, and ordinary history restoration. Six focused tests pass; Ruff and mypy pass. These checks
used the isolated synthetic stack. The full 10-turn acceptance and corpus evaluation remain before
changing the feature flag.

## Rolling comparison, investigation, chart and restore (2026-10-10)

The isolated `finguard-graph-test` stack ran with the graph flag enabled, seeded banking data, and local Qwen 3.5 9B. A real Chrome conversation completed these eight turns:

| Turn | Result |
| --- | --- |
| Expenses by category, last three months | 5,867.59 EUR, 87 movements, all nine category totals |
| Show all categories | Retained the same query and all nine totals |
| Compare with the prior three months | 6,510.80 EUR (2026-04-10–2026-07-09) vs 5,867.59 EUR (2026-07-10–2026-10-10) |
| Show a chart | Rendered the comparison |
| Change it to lines | Updated the same chart; DOM contained two Vega line marks |
| Why the difference? | Investigated merchant totals and purchase counts over the exact comparison windows |
| Return to the previous query | Restored the full category query |
| And in October? | 1,030.42 EUR, seven movements, with October category totals |

The comparison prompt uses a typed `compare_previous=N` operation. Domain transitions resolve it against the active rolling query and validate that its period matches. For a cause follow-up, `investigate_comparison` reuses the stored pair and filters; the graph reads merchants and may ask its bounded assessor to inspect supporting transactions. Thus the planner does not have to regenerate period dates or restate filters. Investigation does not add a duplicate focus to history, so restore returns to the category query.

The run produced no browser JavaScript errors. Unit and graph tests cover exact window reuse, checkpointed scalar comparison reads, recovery, restore history, and the chart update operation. The corpus evaluation and hosted Gemini/Groq provider comparison remain outstanding; this run establishes the local model path only.

## PostgreSQL continuity across an orchestrator restart (2026-10-10)

On the isolated `finguard-graph-test` stack, the API answered a supermarket-expense query at
conversation version 1. I restarted only `finguard-graph-test-orchestrator-1`; PostgreSQL was left
running. After the orchestrator became healthy, I posted `¿y en agosto?` with the exact conversation
id and version returned before the restart. The graph answered 351.76 EUR in six supermarket
movements for 2026-08 and returned the same conversation id at version 2. This verifies the
canonical query focus and its filter survive an orchestrator process restart; it is an API check,
not a second browser run.

## Payment draft continuity through contacts and ambiguity (2026-10-10)

The local Qwen 3.5 9B graph was exercised through `/v1/questions` with the same five-turn Bizum
sequence that previously lost the draft: start a Bizum with an unspecified recipient, list contacts,
name the ambiguous contact Marta, provide `15 euros`, then select Marta Solis Vega. The contacts turn
returned four account-scoped payees while retaining the draft. The amount turn retained both Marta
candidates and the 1,500-minor-unit amount; selecting Marta Solis Vega produced a bank-authored
confirmation preview for 15.00 EUR. The confirmation was left pending and no payment was executed.
The repeated API check also covered a model response that emitted both `prepare_bizum` and a
redundant `clarify`: the graph now lets the guarded payment flow ask for its missing slot. A fresh
three-turn API run continued from generic recipient to Marta Solis Vega to `12 euros con concepto
tapas`; it returned the concept in the bank preview and stopped at explicit confirmation. No payment
was confirmed.

The typed planner now has a `list_contacts` operation that leaves the canonical payment draft in
place. Payment grounding ignores a model-written recipient unless the user's current sentence names
it, and it rejects a spoken recipient outside an already offered candidate set. Focused graph tests
cover the full draft, contact list, ambiguous choice, amount and preview sequence, plus the
out-of-candidates refusal. A later corpus run replayed all 142 turns and reported 69 violating turns;
the interrupted-Bizum case no longer appeared. Its report included 44 `WRONG_WINDOW` checks, but the
graph response kept the executed plan server-side while the evaluator only read the legacy
client-echoed `last_plan`. Those window counts cannot be treated as verified model errors. The API
now returns a typed, non-sensitive `plan_context` with the query answer, and the evaluator uses it
for current and prior plan assertions. Unit checks cover both a matching and mismatching graph plan
trace. The corpus must be rerun against that contract before its window results are usable; the
feature flag remains opt-in.

The suspended-payment workflow now has an explicit canonical bit and a checkpoint migration. An
unrelated completed turn marks an open draft suspended; a later bare slot value returns the paused
message without preparing a payment. The only continuation that fills it is explicit resumption.
An isolated API sequence verified this across an intervening expense query, and the focused suite
now passes 329 graph, codec, interpreter, boundary and evaluator tests. Ruff and mypy pass. The
separate restart-through-Postgres API check could not be repeated because Docker Desktop's engine
pipe became unavailable; earlier query continuity across an orchestrator restart remains recorded
above.

## Gemini corpus rerun and nth-movement correction (2026-10-10)

The isolated stack was rebuilt with its own Postgres volume and ports (5544, 18001 and 18000),
seeded from the synthetic banking fixture, and run with LangGraph enabled and Gemini 3.1 Flash
Lite. The first unpaced corpus attempt replayed all 142 turns but was not a valid quality run:
Gemini returned HTTP 429 on 108 planning calls after its free-tier per-minute quota was exceeded.
The evaluator now accepts `--min-request-interval`; Ruff and its 73 focused checks passed. The
second run paced requests by 4.5 seconds and replayed all 142 turns without a provider-error
outcome.

That run found 30 property violations across 142 turns. The main families were incomplete plans
for direct questions, metric and window inheritance failures, and mismatches between ordinal
movement requests and the answer. Payment drafts were not lost in this corpus, and no turn
confirmed a payment.

The ordinal mismatch had a deterministic cause in the presenter: for rank 2 or above it returned
the first N ranked movements and their summed total, although the domain operation means “the Nth
movement”. It now selects only the requested row for the sentence, amount, count and movement
evidence; if that rank does not exist, it returns no movement evidence. Two regression tests cover
both a present and a missing rank. `tests/test_answer_headline.py` passes (37 tests), and Ruff
passes for the presenter and that test file. The 30-violation corpus result predates this fix; it
must be rerun after the rebuilt container before evaluating the remaining planner findings.

After rebuilding the isolated orchestrator, the real API sequence “highest expense in June” then
“and the second?” returned the June 3 rent movement at rank 1 and the June 22 Mercadona movement
at rank 2. The second response carried only that movement, `movement_count: 1`, and its 8,657-minor
unit amount. This verifies the correction through the graph, checkpointed conversation and public
DTO; the full corpus still needs a post-fix rerun.

A separate post-fix full run used local Qwen 3.5 9B, because the free Gemini endpoint became
unstable during the next long pass. Qwen replayed all 142 turns and reported 49 property
violations. This is a different model from the 30-violation Gemini run and is not a before/after
comparison. The post-fix Gemini rerun stopped after 23 corpus requests when logs showed nine HTTP
503 responses; its partial results are not scored. The first completed Gemini run remains the
full provider baseline, and the two-turn Gemini API check verifies the ordinal fix specifically.
The feature flag remains off by default while the other planner failures are addressed.
