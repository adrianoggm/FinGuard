# LangGraph Gemini conversation corpus run

**Date:** 2026-10-10
**Runtime:** isolated `finguard-graph-test`, graph enabled, Gemini 3.1 Flash Lite, seeded synthetic bank
**Build provenance:** `/version` reported `sha: unknown` for both runs.

## Full frozen conversation corpus, before prompt repair

The end-to-end evaluator replayed all **67 threads / 142 turns** with a six-second minimum interval between requests. There were **20 property violations** and no HTTP 5xx or provider errors in the service logs. This is a regression diagnostic, not an A/B comparison or a promotion result.

The violations covered several distinct issues:

- An individual largest expense was returned as the merchant with the largest aggregate.
- Count questions could lose `COUNT`; the model also confused count, average, and total semantics.
- Some missing-period turns asked for clarification without exposing their pending typed plan in the response, so the public API evaluator could not verify the saved fields.
- A fuzzy request for movements was rendered with a net total as the lead answer.
- Other misses involved catalogue help, account-name grounding, salary direction, correcting a pending payment amount, and grouped comparison output.

The full per-turn report is in the local temporary directory as `finguard-graph-gemini-full-20261010.json`; it contains only the evaluator's bounded response evidence and is not committed.

## Focused rerun after prompt repair

The prompt now distinguishes a single ranked movement from a grouped merchant total and states the count/list semantics. The rebuilt isolated graph replayed six selected corpus threads (seven turns):

- The largest expense and the largest expense in June both returned one bank movement, not a merchant aggregate.
- “How many movements this month?” returned `COUNT` and seven bank movements.
- A Mercadona purchase-count clarification followed by “last month” returned a count of one and retained the merchant and period.
- “Show me movements from the last four months” retained the requested period and returned movement rows.
- Two violations remained: the evaluator cannot inspect a pending `COUNT` plan on the clarification turn, and the typo-heavy “what movements have I had?” turn leads with a total despite returning movement rows.

The focused JSON report is in the local temporary directory as `finguard-graph-gemini-focus-20261010.json`; it is not committed.

## Decision

The prompt repair is useful but the graph is **not ready for promotion**. The full run still exposes semantic and presentation gaps, and this diagnostic has no rules-engine control, blind judge, generalization set, or complete browser/payment promotion gate. The missing pending-plan evidence is a measurement limitation; it must not be reported as proven pending-state correctness solely because a later turn happened to work. The movement-list presentation mismatch remains a product issue to resolve in the typed operation/renderer boundary.

## Follow-up after the new Gemini key and counterparty fix

The next full replay used Gemini 3.1 Flash Lite with the replacement key, a freshly built local
image, the same synthetic bank, and no request pacing. It completed **67/67 threads and 142/142
turns**, with **16 property violations** and no provider errors. The per-turn report is in the local
temporary directory as `finguard-gemini-counterparty-fix-full-20261010.json`. The lower count than
the earlier 20-violation run is a diagnostic, not a causal comparison: model output can vary, and
several code and prompt changes landed between those runs. This run still does not pass the quality
gate.

The focused retest covered three failures: a period-only reply to an open clarification, a salary
follow-up, and a historical payment query naming someone absent from saved contacts. All six turns
completed with zero property violations. The last case exposed a domain-boundary bug: historical
counterparties were incorrectly resolved against the payment contact directory. Query counterparties
now pass to the banking filter as written; saved contacts remain the payment flow's authority.

The interpreter also has a bounded consistency repair for the narrow case where its typed operation
would prepare or delegate a payment despite a past-transaction question. It asks the planner to
correct the operation once and returns a clarification if the mismatch remains. Unit tests cover
both correction and refusal; the full corpus did not trigger this guard. The pending-window prompt
now includes a direct example and trims unrelated examples while retaining those for the visible
focus. These changes were committed as `1e0469da`.

The focused Python checks passed (**193 tests**) and Ruff passed. The evaluator output contains no
latency or token measurements, and this replay has no rules-engine control or blind judge. The
remaining violations still include query shape and metric errors, account-name grounding, inherited
filters, and grouped comparison presentation. The graph stays behind its feature flag pending the
full migration gates.

## Repeat replay with the latest Gemini key

After a short three-thread smoke (7/7 turns, one continuity expectation failure), the isolated
Gemini 3.1 Flash Lite instance replayed the full corpus again: **67/67 threads, 142/142 turns, 16
property violations**. The violating turn IDs and labels were identical to the preceding
counterparty-fix replay. This is a reproducibility signal for the current failure set, not evidence
that the fixes caused it or that the quality gate passes. In particular, both runs still fail the
pending-payment-amount correction case.

The new run recorded transient Gemini `429` responses early on, then completed the corpus without
further rate-limit log events. No provider or transport errors appeared among the evaluator's
violations. The run used a temporary isolated orchestrator on port 18013, shared only the synthetic
banking/Postgres services, and was removed afterward. The regular stack on port 18000 was left
untouched. The bounded report remains local at
`%TEMP%/finguard-gemini-full-replay-latest-key-20261010.json`; no API key is stored in the repository.

## Gemini 3.5 Flash Lite continuity smoke

To avoid spending another full-corpus run on a model that might not address the known defects, a
separate isolated instance ran three targeted conversations (**9/9 turns**). It recorded three
violations: the same pending Bizum amount correction, bare correction, and category-catalogue
question failures. There were no provider rate-limit events. This is enough to reject it as an
immediate fix for these cases, but not enough to compare overall quality or promote/demote the
model. The report is local at `%TEMP%/finguard-gemini-35-targeted-20261010.json`; the temporary
instance was removed afterward.

## Gemini 3.5 movement-list schema repair

The isolated Gemini 3.5 Flash Lite smoke exposed a provider-adapter defect: `_shallow_operations_schema`
represented the `list_movements` payload as an empty object. The typed reader then filled its defaults,
so a request for five expenses from Cuenta corriente could become an unfiltered page of 20. The adapter
now sends the nested direction, account/category/merchant filters, page size, and continuation flag to
Gemini while the existing typed reader remains the acceptance boundary. The provider contract test
asserts those fields are present.

The focused orchestrator suites passed (**271 tests**) and Ruff passed. After rebuilding only the isolated
orchestrator, a real Gemini 3.5 Flash Lite conversation returned exactly five expenses from Cuenta
corriente. The follow-up “dame otros 5” returned five further movements with no duplicate transaction
IDs and conversation version 2. This verifies the requested page size, account scope, and continuation
for this smoke case; it is not a full corpus evaluation or promotion result. The shared stack was not
restarted.

## Active-chart analytical regrouping

A real Gemini 3.5 Flash Lite graph session reproduced the user-facing failure: after a category
breakdown and a bar chart, “Agrúpalo por mes” returned a generic clarification and discarded the
chart. The interpreter prompt now distinguishes a presentation-only edit from a changed analytical
dimension. For the latter it emits a typed focus patch plus `update_visualization`; the graph validates
that an active chart exists, executes the changed query through banking, and redraws the result in the
same conversation commit. A chart style edit still uses the stored result without a banking query.

The graph generation is now `conversation/24`, invalidating checkpoints created under the previous
routing semantics. The focused interpreter, Gemini adapter and graph-turn suites passed (**273 tests**);
Ruff and `git diff --check` passed. Their graph tests verify both the extra banking read and artifact /
chart lineage on regroup, and reject a combined query/chart update when no chart is active without a
bank read.

The rebuilt isolated stack completed a real three-turn Gemini session: all nine expense categories
were shown for the requested period, the second turn rendered them as bars, and “Agrúpalo por mes”
returned four monthly groups with a line chart at conversation version 3. This is a focused semantic
and API-render-payload smoke; browser rendering, full-corpus quality and promotion gates remain open.

The same three-turn regrouping smoke was repeated with an orchestrator restart between chart creation and
“agrúpalo por mes.” PostgreSQL was left running. The resumed turn returned the monthly query and a
line chart at conversation version 3, confirming that the persisted query/chart state was sufficient
for this real-provider continuation after process restart. It remains a single synthetic account
conversation, not broad recovery or concurrency coverage.

## Browser verification for analytical regrouping

The existing category-breakdown browser flow now continues past chart creation and presentation-only
restyling to “Agrúpalo por mes.” With the isolated Gemini graph stack and Chrome, Playwright passed:
the chat retained its conversation id and version sequence, the regrouped plan was `MONTH` /
`TIME_SERIES`, it received new query and artifact ids, and the monthly chart figure was visible in the
conversation. The run used an explicit web port and the isolated stack's temporary CORS allow-list;
the pre-existing Vite on port 3000 was left running and untouched.

## Ten-turn browser banking session

The attached acceptance critique called for evaluating a whole banking session, including task
changes, implicit references, a chart, payment capability and safe cancellation. I added an opt-in
Playwright flow and ran it in Chrome on the isolated graph stack with Gemini 3.5 Flash Lite. The build
reported by `/version` was `48f18b4c-dirty-session-acceptance-v2`; the stack reported graph mode on.
The browser run passed all ten turns at conversation versions 1 through 10:

1. Balance question -> `BALANCE`.
2. Latest 20 expenses from Cuenta corriente -> 20 bank rows, all rendered in the chat.
3. "Y los de la Hucha?" -> list task and account switch retained; the synthetic ledger has no rows
   for Hucha, so the assistant said so instead of asking for a period.
4. "Dame otros 10" -> recognized the exhausted list and answered that there were no more rows.
5. Restaurant expenses last month -> bank-backed total and six movements.
6. "Comparalo con agosto" -> retained restaurant scope and compared August with September.
7. "Ponme una grafica" -> chart figure rendered in the conversation.
8. "Puedes hacer Bizums?" -> described saved-contact preview and explicit confirmation.
9. Restaurant spending in July plus a 15 EUR Bizum to a saved contact in one message -> the answer
   kept the bank-backed restaurant result and appended the bank-authored preview; the same turn
   committed as conversation version 9 and both confirmation controls rendered.
10. Clicking "No" -> `CANCELLED`, confirmation workflow cleared, version 10.

The synthetic payment intents created by the corrected API and browser runs are `CANCELLED`; none was
confirmed. The graph unit test also verifies that answering the capability question does not call a
payment command. The mixed-turn browser session took 16.5 seconds. Request logs show ten model HTTP
calls for nine planned turns (one turn used a second call) and ten endpoint latencies: median 1.40 s,
maximum 2.52 s; cancellation took 51 ms and used no model call. Token usage is not exposed by the
response or request logs, so this run cannot report tokens. The synthetic dataset has no Hucha
movements, making that branch a truthful empty-list check rather than a populated-account comparison.

A separate payment-recovery smoke prepared a Bizum preview, restarted only the isolated orchestrator,
and cancelled with the same versioned conversation payload. The graph resumed the pending workflow
from PostgreSQL and returned `CANCELLED` at version 2 with the workflow cleared. The bank records
show the created intents as `CANCELLED`; none was confirmed. PostgreSQL and the banking API were not
restarted during this check.

This is one authored acceptance session, not the blind human evaluation or unseen session set
requested in the critique. It does not show broad generalization or recovery after provider/bank
failure, or compare quality against the current engine. The full corpus, UX set, human evaluation,
token instrumentation, and migration/removal of legacy coordination remain promotion gates.

## Corpus replay attempt after the session changes

I started the frozen 67-thread / 142-turn HTTP corpus against the isolated graph stack after the
movement-list and conversation-session changes. `/ready` reported `graph=on`, Gemini 3.5 Flash Lite,
and `/version` reported `48f18b4c-dirty-session-acceptance-v2`. The provider returned repeated HTTP
429 responses with no retry delay or quota metadata. The replay was stopped after the consecutive
failures; it did not complete, and the evaluator produced no report artifact. This is a provider
availability failure, not a quality result. It does not replace the last completed full-corpus
measurement (16 property violations on Gemini 3.1 Flash Lite), and promotion remains blocked until a
complete replay can run against this code state.

The focused local regression suites then found that the legacy route's byte-pinned test failed in
both JSON and streaming mode. I reproduced the same six response hashes in a detached, clean
`48f18b4c` worktree and in this working tree; the checked-in pins predated that clean-tree output.
The pins now describe the clean-tree baseline, and the same route suites pass with the graph changes
present. The selected route, graph-turn, codec, and movement-list suites passed **166 tests**. The
graph-enabled routes also return 503 when their handler is missing, for both JSON and streaming, so
the graph path does not fall through to legacy coordination.
