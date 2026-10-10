# Task 8 report: LangGraph capability-addition experiment (read-only)

**Status of the original Task 8 comparison: PARTIAL (after fix round 2; live comparison incomplete).** The graph is built, tested with fake providers and committed.
- The current-engine live run started, then Groq returned repeated `PROVIDER_ERROR`s. The graph run and blind judging remain pending; the partial run is recorded below.
- **At that original run, durability was unproven.** A later test against the current unified LangGraph stack is recorded below.

Where it lives:
- Worktree: `C:/Users/adriano.garcia/Desktop/Developer/FinGuard-wt-langgraph`
- Branch: `task/langgraph-experiment`
- Base: `c907c6fa` (verified before starting). Not pushed, not merged.

| Commit | Subject |
|---|---|
| `6552244e` | test(evaluation): capability conversations for the investigation experiment, written before the graph |
| `61d4203e` | build(orchestrator): add langgraph for the investigation graph experiment |
| `68d7595e` | feat(graph): the merchant investigation as pure functions, its checkpoint codec and the assessor's closed schema |
| `60da19e7` | feat(graph): the bounded investigation StateGraph with reservations, resume and a checked narrative *(the narrative was removed in fix round 1)* |
| `5dcd37e8` | feat(graph): the investigation graph behind its own off-by-default flag, held to the engine's boundary |
| `7fd8334e` | fix(graph): no model-written text reaches the person; the rise is a closed explanation the templates render (R42) |
| `192c61ae` | fix(graph): every exit that saved nothing releases the turn, cancellation included (R43) |
| `7e4acdca` | fix(graph): every bank port call counts and is checkpointed; a resume reads only what it never read (R44) |
| `36981c01` | fix(graph): a request id is bound to its sentence, the model's choice is checkpointed as a position, the tripwire covers an inspection, and the shared helpers are public (R45) |
| `a506ec01` | fix(graph): the release survives a failed or cancelled persist, and a saved turn is never marked resumable |

**Tests, after fix round 1.** I ran the full orchestrator suite in two foreground halves of 104 test files each, with `test_ollama_provider.py::test_a_real_daemon_honours_the_schema` deselected.

| Half | Passed | Skipped | Deselected |
|---|---|---|---|
| First | 3503 | 3 | 0 |
| Second | 3355 | 1 | 1 |
| **Total** | **6858** | **4** | **1** |

Nothing failed.
- **New tests: 71.** They are 23 in `test_graph_investigation.py`, 37 in `test_graph_turn.py`, 6 in `test_graph_wiring.py` and 5 in `test_engine_boundary.py`.
- **Generated tests: 11.** Existing architecture tests are parametrized per source module, so the new modules added 11 cases.
- **Collection:** 6781 tests at the base, 6863 now.

**Lint and types.** `ruff check` and `ruff format` are clean on every touched file, and `mypy` reports no new errors. The errors it still reports were there at the base:
- 16 in `tests/support/episodes.py`;
- 3 in `test_engine_boundary.py`, at its `record_context_untrusted` control and one existing unused ignore.

## Step 1: the capability, the contracts, the claims

**The question.** «¿En qué comercios gasto más que antes y se debe a más compras o a importes mayores?»

**The banking contracts, checked in code.**
- `FinancialQueryPort.run` with `group_by=MERCHANT` returns one `Group(key, total_minor, movement_count)` per merchant. Banking's `query_compiler.py` groups by `COALESCE(t.merchant_name, 'UNKNOWN_MERCHANT')` and returns up to 500 rows.
- `FinancialQueryPort.ranked(TopMovements(merchants=(m,), rank=1))` returns a merchant's largest movement in a window.
- Two grouped reads and one ranking are therefore enough to answer.

**Answerable claims.** Each is integer arithmetic on banking's figures from this turn:
- which merchants rose;
- why each one rose, as a **closed explanation** (see fix round 1, R42);
- whether **one purchase** explains the rise. It does when, without the largest purchase of the latest window, that window would not be above the earlier one (`largest >= rise`). Otherwise the rise is spread over several purchases.

**The only allowed follow-up.** `Inspect(merchant)` reads the largest purchase of one merchant.
- It is allowed only for a merchant whose explanation is HIGHER_AMOUNTS or BOTH, and only once per merchant.
- Deterministic code (`investigation.worth_inspecting`) computes this allow-list. The model chooses from it and cannot add to it.

**Budget per turn.**
- **At most 3 bank port calls**, failed calls included.
- **At most 3 model calls.** That is the interpreter (1, or 2 with its repair call) plus the assessments.
- Deterministic code checks both limits before each step. LangGraph's `recursion_limit` (20) is only a backstop.

**What must stay unknown.** The bank holds no evidence of why the person buys more or pays more: a price rise, a move, a habit.
- No template states such a reason, and no text from a model reaches the person (R42).
- A new merchant gets no explanation beyond its counts. A merchant that held level or fell is not named, and `UNKNOWN_MERCHANT` is never named.
- A currency mismatch between the two windows is refused.

**The capability conversations.** `apps/orchestrator/evaluation/engine/capability/conversations.yaml` was committed first, before any graph code (`6552244e`).
- It has 4 conversations and 11 turns.
- Every turn has `say` and `expect.want`, in the same shape as the regression set.
- `generalization/` was not opened.

## Step 2: the baseline (the current engine, unchanged)

**The current engine cannot answer the capability.** A `Compare` carrying `group_by=MERCHANT` becomes `Clarify(OPERAND)` through `transitions._as_said`. `test_the_current_engine_refuses_a_comparison_by_merchant` pins this as the control.

Baseline code size of the model engine, in code lines (blanks, comments and docstrings excluded):

| File | Code (total) |
|---|---|
| domain/engine/state.py | 86 (154) |
| domain/engine/operations.py | 63 (109) |
| domain/engine/grounding.py | 57 (100) |
| domain/engine/transitions.py | 409 (593) |
| domain/ports/turn_operations.py + dialogue_store.py | 39 (101) |
| application/engine/answer_turn.py | 158 (220) |
| application/engine/executor.py | 116 (171) |
| application/engine/presenter.py | 397 (495) |
| infrastructure/engine/llm_turn_operations.py | 94 (140) |
| infrastructure/engine/operations_prompt.py | 414 (527) |
| infrastructure/engine/operations_schema.py | 287 (511) |
| infrastructure/engine/memory_dialogue_store.py | 23 (45) |
| infrastructure/controllers/engine_dispatch.py | 190 (309) |
| infrastructure/llm/counting_provider.py | 46 (85) |
| **Total** | **≈2,379 (3,560)** |

**Adding the capability to the engine without a graph: an estimate, not built.** I estimate about 215 code lines, with no new persistence, reservations or codec:

| Piece | Code lines |
|---|---|
| A transition rule that keeps `MERCHANT` on a `Compare` | ≈15 |
| An executor branch that reads by merchant | ≈20 |
| A presenter branch | ≈15 |
| `investigation.py` as it now stands | 153 |
| A deterministic inspection of the largest HIGHER_AMOUNTS or BOTH rise | ≈15 |

The Task 6 and Task 7 runs and reports were not touched.

## Step 3: what was built

**The flag.** `ORCHESTRATOR_CONVERSATION_GRAPH=off|on`.
- It is `off` by default in `config.py` and `.env.defaults`, and pinned in `conftest.py`.
- It takes effect only with `ORCHESTRATOR_CONVERSATION_ENGINE=model`.
- With `on`, `app.state.engine` is a `GraphTurnHandler`, which has the same `answer(...)` signature and returns `EngineAnswer`. `engine_dispatch.py` is untouched.
- With `off`, the app does not import the graph or LangGraph. `test_graph_wiring.py` checks this in a fresh interpreter, with `on` as the control.
- The flag-off pin in `test_engine_routes.py` stays green.

**The graph shape:**

```
load_and_reserve → plan → validate_and_apply → execute_read_only ⟲ → assess_result → (plan | present_and_commit)
                              ├→ present_and_commit (an ordinary turn, a reply, a refused operation)
                              └→ END (Delegate: money flows leave, nothing saved)
```

`execute_read_only` loops on itself: it makes one bank read per step, the earlier window and then the latest. Each read is checkpointed (R44).

**What each node does and reuses** (`application/graph/graph.py`):
- **`plan`.** It calls the existing interpreter (`TurnOperationsPort`) with the same prompt, the same closed schema and the same Groq strict call. On a loop it makes no call.
- **`validate_and_apply`.**
  - It calls `answer_turn.apply_guarded`, which is `transitions.apply` plus the invariant guard. Before that, it removes `MERCHANT` from a `Compare`, so the transition validates the pair of totals and sets the focus.
  - For the assessment's choice, it checks the deterministic allow-list and the bank budget.
- **`execute_read_only`.** It calls `executor.execute` for every bank read. Every port call goes through a counting wrapper.
- **`assess_result`.** It makes a separate structured call through `InvestigationAssessorPort`, implemented by `LlmInvestigationAssessor`.
  - The schema is closed per call, externally tagged, and uses no tool calling. It is `{"inspect": {"merchant": <allow-list enum>}}` or `{"stop": {}}`: the stop carries nothing.
  - The prompt is a value-minimized summary: names, explanations and counts. It never includes the sentence.
  - The assessment is not asked when nothing can be inspected or when either budget is spent.
- **`present_and_commit`.**
  - For ordinary turns it reads and answers in one step through `presenter.answered` or `presenter.replied`, as the engine does.
  - For the investigation it renders **only from the checkpointed results**, with `investigation.answer_text`.
  - Then it runs the canonical `store.save` compare-and-set.

**Parity.** An ordinary turn through the graph gives exactly the canonical engine's result and saved state (`test_an_ordinary_question_is_answered_as_the_engine_answers_it`).

**State and recovery** (`application/graph/graph_turn.py`). These stay in FinGuard code, not in LangGraph.
- **Reservation.** Before any model or bank work, the handler reserves `(account_id, conversation_id, expected_version, request_id)`. The request id is bound to a hash of its sentence (R45).
- **Duplicate request.**
  - After success it gets the stored answer back, with no model or bank work.
  - After a failure it resumes from the checkpoint.
  - While the first is still in flight, it is stale.
  - The same id with another sentence raises `RequestReusedError`.
- **Different request.** It is stale against a turn in flight or done, and may take over a turn that failed.
- **Compare-and-set.** The final compare-and-set is kept.
- **Release.** Every exit that saves nothing releases the reservation in `finally`. That covers cancellation and unexpected errors (R43).

**What the checkpoint holds.** It is written after each node:
- the graph and schema versions;
- the turn id, account, conversation and expected version;
- the validated operations, as allow-listed JSON (`codec.py`);
- the status;
- the bank-call and model-call counters;
- the earlier window by merchant, until the latest window is read;
- for each risen merchant, its totals, counts and explanation;
- the inspected merchant's largest amount and date;
- the assessment's choice, as a position in the allow-list.

It holds no sentence, no prompt, no credential, no movement description and no text written by the assessor. A checkpoint from another graph or schema version is not resumed: the turn restarts.

## Step 4: live run attempted; comparison pending

The current engine was run in an isolated orchestrator built from this branch, with `ORCHESTRATOR_CONVERSATION_GRAPH=off`, Groq `openai/gpt-oss-120b`, and the capability set. The banking fixture's newest movement was 2026-10-07 while the run date was 2026-10-09, so preflight accepted a three-day age. The shared stack was not restarted.

The replay stopped after five of eleven turns:
- `comercios-crecen-mes`: both turns returned `NEEDS_CLARIFICATION` (the current engine asked for a period, then a subject);
- `supermercados-trimestre`: three turns returned `PROVIDER_ERROR`, including after one configured 60-second retry.

The runner's record does not include provider details, but the orchestrator logs confirm HTTP 429 responses from Groq. The logs from that build did not retain rate-limit reset headers, so whether this was a per-minute token cap or a daily cap is not confirmed. The first conversation also exposed a prompt/evaluation mismatch: the prompt required asking for a missing window while the case expected the model to choose and state one. The graph run and blind judging were not started because the baseline did not complete. This partial run is not a capability comparison and not a result for or against LangGraph.

**Follow-up fixes after this run:** the interpreter prompt now defines the comparative default (two latest complete months unless a duration is named) and gives an independent grouped-comparison example; the Groq adapter logs only safe rate-limit headers on HTTP 429; the runner renders a report when no engine has successful latency measurements. These changes are not included in the recorded run, which used build `aaa96c01`.

**Bounded live smoke after the prompt fix (2026-10-09):** one query on the isolated graph-enabled build completed as `PLANNED` in 3.8 s. The model selected September 2026 vs August 2026, grouped by merchant, and explained newly appearing merchants plus Uber's higher count and average transaction amount. It made two successful Groq calls (structured plan and grounded answer) and two successful banking queries; no clarification or provider error occurred. This validates the targeted prompt path only. It is not an A/B result, does not establish overall accuracy, and did not retry the eleven-turn baseline or the 56-case generalization set. The earlier 429 cause remains unconfirmed because its response headers were not captured.

**Further control attempts (2026-10-09):** after adding the comparative default, a replay of the complete three-conversation capability file in graph-off mode saved 10 turns; every turn returned `PROVIDER_ERROR` from Groq HTTP 429. The safe headers now show `Retry-After` values up to 1881 seconds, while the request/token remaining and reset fields do not identify a depleted rolling limit. After waiting 240 seconds, a one-turn control smoke reached Groq successfully but still returned `NEEDS_CLARIFICATION` for the missing comparison windows. I added a few-shot example using that exact sentence; the next attempt was rate-limited again, so that last prompt change is not live-verified. This shows the first prompt change did not reliably fix the control. Do not treat the earlier graph smoke as a paired result: it only establishes that one graph-on request succeeded. The current-versus-graph evaluation and blind judging remain pending until Groq is available.

Run artifacts: `apps/orchestrator/evaluation/engine/runs/2026-10-09-task8-current-groq-120b-v2.{json,md}` (initial partial run), `apps/orchestrator/evaluation/engine/runs/2026-10-09-task8-current-groq-120b-fixed.{json,md}` (10-turn retry, all provider errors).

Artifacts: `apps/orchestrator/evaluation/engine/runs/2026-10-09-task8-current-groq-120b-v2.{json,md}`.

When provider availability permits, the controller should run the same capability set through both modes and blind-judge A/B:

```
ORCHESTRATOR_CONVERSATION_ENGINE=model ORCHESTRATOR_CONVERSATION_GRAPH=off  # A: current engine
ORCHESTRATOR_CONVERSATION_ENGINE=model ORCHESTRATOR_CONVERSATION_GRAPH=on   # B: graph
uv run python scripts/real_conversation.py replay --file evaluation/engine/capability/conversations.yaml --out <path>
```

The controller then blind-judges A against B on:
- correct answers and continuity;
- unsupported claims;
- model calls and tokens (`X-FinGuard-Model-Calls` includes the assessor);
- latency, retries and recovery, and failures.

**No comparative result is claimed.**

## Step 5: code size by responsibility (recounted after fix round 1)

| Responsibility | What it is | Code lines |
|---|---|---|
| **Graph orchestration** (LangGraph-specific) | graph.py: state, context, types and the budget error (110), the six nodes (145), routing and `build` (32) | **287** |
| **Adapters** over existing pieces | graph.py: canonical load, grounds, transition, merchant grouping, counting port wrappers, execute, inspect (106); `llm_assessor.py` (84); `graph_wiring.py` (42); main.py and config.py wiring (≈25) | **≈257** |
| **Capability logic and its templates** | `investigation.py` (153); graph.py state read-back and presentation (61); `investigation_assessor.py` port (30) | **244** |
| **Persistence and concurrency** | `graph_turn.py` (206); `turn_reservations.py` port (28); `memory_turn_reservations.py` (48); `codec.py` (67) | **349** |
| **Tests** | `test_graph_investigation.py` (221), `test_graph_turn.py` (541), `test_graph_wiring.py` (66), boundary additions (≈125) | **≈953** |
| **Production total** | | **≈1,137** |

**Existing orchestration removed: none.**
- The engine changed by 4 lines only: `_applied` became `apply_guarded`, and `_scope_note` became `scope_note`.
- The graph's nodes re-express `AnswerTurnHandler.answer`'s sequence: interpret → apply → execute → present → save, with failures and delegations unsaved.

**Did LangGraph reduce specialized coordination code? No.**
- The capability logic is about 150 lines whether or not a graph hosts it.
- Hosting it in the graph took about 290 lines of graph orchestration and about 350 lines of persistence and concurrency that LangGraph does not own:
  - reservations;
  - deciding between resume, replay, takeover and refusal;
  - the version gate;
  - persisting the counts of calls that failed;
  - the codec. It exists because LangGraph's serializer turns tuples into lists, and because it warns that loading arbitrary classes will be blocked.
- That is roughly five times the ≈215-line deterministic estimate, for the same answerable claims. What the extra code buys is a model choosing which merchant to inspect.

**What LangGraph gave for free:**
- conditional routing, including the self-loop for per-read checkpoints;
- re-entry at the failed node (`ainvoke(None, thread)`);
- `aupdate_state`, used to write the calls a failed step made into the checkpoint;
- a uniform checkpoint per node.

Every safety rule stays deterministic FinGuard code: the allow-list, the budgets, the version, idempotency, compare-and-set and the closed explanation.

## What is demonstrated, and what is not

**Demonstrated with fakes, in process:**
- **Budgets.** The loop terminates at both budgets, and every bank port call counts, failed ones included.
- **Resume.** A failure on the second read, then a resume, makes 3 port calls in total. The first window is not read again and the interpreter is not asked again.
- **Allow-list.** A merchant outside it is refused without a bank read.
- **No model text.** No string the model writes reaches the reply or the checkpoint. This is shown with a marker in the output of the real assessor adapter, and across four shapes: text in a stop, a merchant off the allow-list, extra keys, and output that is not JSON.
- **Rendering.** Each explanation value renders with its figures, and the whole reply equals the template rebuilt from the bank's figures.
- **Outages.** An assessor outage gives a grounded partial answer; an interpreter outage saves nothing.
- **Request handling.**
  - A duplicate request is replayed.
  - A request id reused with another sentence is refused.
  - A stale concurrent turn and a compare-and-set race are refused.
  - A cancelled turn, or one that hits an unexpected error, leaves the conversation usable at the same version.
- **Versions.** A checkpoint version mismatch restarts the turn.
- **Money.** Money flows leave without a read and without a save.
- **Parity.** Ordinary turns are identical to the current engine's.
- **Reader boundary.** It holds over HTTP on both routes, also for an investigation that is assessed and inspects a purchase.

**Unproven:**
- **Durability (UNPROVEN).** The checkpointer, the reservations and the dialogue store are all in memory, and a restart loses them. No crash recovery is claimed.
  - The Postgres checkpointer was not used, because the only reachable Postgres (127.0.0.1:5442) is the demo database.
  - Its transaction and retention behaviour is therefore unverified.
- **Live behaviour.** Whether the interpreter emits `Compare` with `group_by=MERCHANT` for these sentences is unmeasured. No prompt example teaches it. Without it the graph never investigates.
  - Whether the assessor holds to the closed schema on Groq, and the latency and tokens, are also unmeasured until Step 4.
- **Idempotency over HTTP.** It is not wired: the dispatch passes no request id. Replay, resume and binding a request id to its sentence are demonstrated only at the handler level.

## Limitations (all of them)

1. **Durability is unproven**, as above.
   - A crash between the commit and `reservations.done` turns the duplicate stale instead of replaying it.
   - The persisted count of failed calls lives in the in-memory checkpoint, so a crash loses it too.
2. **Production gaps:**
   - durable checkpoints;
   - durable, shared reservations;
   - retention: threads are never pruned;
   - migration: versions only discard old checkpoints;
   - an HTTP idempotency key.
3. **The explanation's threshold is a choice.**
   - "Importes mayores" needs a mean at least 5 % above the earlier one (`MEAN_RISE_PERCENT`).
   - CANNOT_TELL covers a rise with no more purchases and a mean that rose by less than 5 %.
   - The answer gives the figures in every case, so a reader can check it.
4. **The capability is one turn deep.** The follow-ups in the conversations file are answered by the canonical engine, from a focus on the pair of totals.
5. **The happy path now often ends partial.** With two inspectable merchants, the budget (2 reads + 1 inspection) leaves the second one uninspected, and the answer says so ("No he investigado más…"). This is the honest consequence of counting every call.
6. **Ordinary turns' bank calls are counted but not capped.** Their work is the measured engine's, read and presented in one step, so a resume after a failure there reads again. Only failed reads are repeated: nothing was kept from them.
7. **The resume path re-reads the directories.** On a resume, the holder's account and contact directories are read again, to re-derive the pure transition. They are directory ports, not the query ports the budget counts.
8. **Model-call accounting** uses `max(1, delta)` per call site. On a resume, the reported `model_calls` covers that request only.
9. **New-conversation ids differ from the engine's.** They are derived from the request id (`uuid5`); the engine uses `uuid4`.
10. **Dependency footprint.**
    - langchain-core, langsmith and `httpx` are now installed, and websockets went down from 17.0 to 16.1.1.
    - Nothing outside `application/graph` imports them, and langsmith tracing is not configured.
11. **TDD order.**
    - Step 1 came first.
    - In the original pass, the pure tests were written alongside the module.
    - In fix round 1:
      - R43, R44 and the R45 request-id tests were written and seen red first.
      - The R42 tests were written with the new module.
      - The tripwire extension passed on first run.
12. **Task 7's phase-1 decision is separate.** With the graph flag off, the measured engine is unchanged.

## Fix round 1

The review returned SPEC ❌. Each item below was fixed in its own commit.

- **R42 (Critical): no model-written text reaches the person** (`7fd8334e`).
  - **Removed:**
    - the free narrative;
    - `checked_narrative` and `unsupported_numbers`;
    - the `Stop.why` field. The schema's stop is now an empty closed object, and the reader drops whatever a model writes inside it.
  - **Added:** `investigation.Explanation`, a closed enum computed from the turn's bank figures. With `more` meaning the count rose and `higher` meaning the mean per purchase rose by at least `MEAN_RISE_PERCENT` = 5 % (integer cross-multiplication):

    | Explanation | Rule |
    |---|---|
    | NEW_MERCHANT | no purchases in the earlier window |
    | BOTH | `more` and `higher` |
    | MORE_PURCHASES | `more` only |
    | HIGHER_AMOUNTS | `higher` only |
    | CANNOT_TELL | neither |

  - **Rendering.** Each value has a template that shows the counts and the means it was decided from.
  - **What the model still does:** it only chooses the next allow-listed read, or stop.
  - **Tests:**
    - each value renders with its figures, and every value has its own template;
    - the exact threshold;
    - a marker in the output of the real assessor adapter, over four shapes (text in a stop, a merchant off the allow-list, extra keys, non-JSON), never reaches the reply;
    - the whole reply equals the template rebuilt from bank figures.
  - **Report corrected:** the earlier claim of "no unsupported numeric claims" for a narrative is withdrawn; the narrative no longer exists.
- **R43 (Important): the reservation is released on every exit** (`192c61ae`). It is released in `finally` (shielded), including on `CancelledError`, an unexpected port exception and a failed assert. Tests: a cancelled turn, a `RuntimeError` and an `AssertionError` each leave the conversation usable at the same version.
- **R44 (Important): the bank budget counts every port call** (`7e4acdca`).
  - Every port call counts, failed ones included. A counting wrapper counts each call before it is made.
  - The count is persisted:
    - nodes add their calls to `bank_calls` in the checkpoint;
    - after a failed step, the handler writes its calls in with `aupdate_state`.
  - The investigation reads one window per step and checkpoints what it read, so a resume never reads it again. `present_and_commit` renders from the checkpoint.
  - Once the budget is spent:
    - a pending inspection is dropped and the answer is marked partial;
    - an unfinished pair answers "límite" without a fourth read.
  - Tests:
    - a failure on the second read, then a resume, makes 3 port calls in total, with June read once and the persisted `bank_calls == 3`;
    - a failed inspection is not retried past the budget;
    - the budget holds across resumes.
  - `GRAPH_VERSION` and `STATE_SCHEMA_VERSION` were bumped.
- **R45 (Minor)** (`36981c01`).
  - **Request id bound to its sentence.** A request id is bound to a SHA-256 of its sentence. The same id with another sentence raises `RequestReusedError`, for replay, for resume and for a new conversation. The boundary AST check lists that hash as the one extra place the sentence reaches.
  - **No model text in the checkpoint.** The assessment's choice is checkpointed as a position in the allow-list (`next_inspect`, -1 for a merchant off the list), never as the string the model wrote. The marker test also checks every checkpoint and every pending write. The validated operations are still checkpointed as the brief requires; they are the interpreter's typed output.
  - **Tripwire extended.** The runtime tripwire now covers an investigation that is assessed and inspects a purchase: 3 reads and the inspection line in the answer, on both routes, with nothing tripped.
  - **Private helpers made public.** `answer_turn._applied` became `apply_guarded` and `presenter._scope_note` became `scope_note`, with their callers updated. The engine tests stay green.

## Fix round 2

The re-review confirmed R42, R44 and R45, and found two R43 residuals in `graph_turn.py`. Both are fixed in one commit, `a506ec01`. The tests were written first and seen red: each failed with a stale `StaleDialogueError`, or with the persist error masking the answer.

**1. The release no longer depends on the persist (Important).** Every exit that does not close normally now goes through `GraphTurnHandler._close`.
- For a turn that did not save:
  - It persists the failed step's calls (R44) inside `try`, and releases the reservation in that `try`'s `finally`. A cancellation during the persist therefore still runs the release.
  - A persist that raises is logged and swallowed. It no longer masks the failure that ended the turn, or the answer already given for it.
  - Both steps are shielded.
- Tests:
  - `test_a_cancellation_while_persisting_the_calls_still_releases_the_turn`: the persist blocks, the request is cancelled, and a new request against the same version then saves version 2.
  - `test_a_persist_that_raises_neither_holds_the_turn_nor_masks_the_answer`: the turn still answers `BANKING_UNAVAILABLE`, and the next request saves version 2.

**2. A saved turn is never marked resumable (Minor).** The handler now tracks "saved" separately from "`done()` returned". The answer exists only after the compare-and-set went through.
- If `done()` raises or is cancelled after the save, `_close` retries `done()`, shielded, and never calls `failed()`.
- If the retry also fails, the reservation stays in flight. A duplicate is then stale, which is true: the version has moved.
- Test: `test_a_turn_saved_before_its_reservation_closed_is_replayed_not_resumed`, in two variants (`done()` raises; `done()` is cancelled). In both, the duplicate request replays the saved answer at version 1 and the interpreter is not asked again.

**Verification.** `test_graph_turn.py`, `test_graph_investigation.py` and `test_engine_boundary.py`: 79 passed. ruff check and format are clean on `graph_turn.py` and `test_graph_turn.py`. mypy reports no new errors; the remaining 16 are in `tests/support/episodes.py` and predate this work. The full suite was not rerun in this round, as instructed.

**Limitation that remains.** A saved turn whose `done()` fails twice stays in flight in the reservations until the process restarts. The memory adapter has no expiry for that case. Its duplicates are answered stale, never run again.

## Targeted Qwen rerun after account-scope grounding

On 2026-10-10, the isolated local Qwen 3.5 9B graph stack was rebuilt with an account-catalogue
hint in the planner prompt. The hint includes only bank account names whose meaningful words
overlap the current utterance; it does not map aliases or choose an account. This addresses a
concrete grounding gap: the planner previously saw only `Accounts: every account`, even when the
bank had supplied account names.

- The two-turn case `an_account_and_a_month_both_stated_outright` now replays with 0 property
  violations. “in the hucha last month” is rendered as consulted in Hucha, and the follow-up for
  gasoline retains both Hucha and last month.
- The broader targeted subset replayed 11 conversations / 23 turns with 12 property violations
  (down from 14 in the pre-change run). This is a useful diagnostic subset, not the full frozen
  corpus and not evidence of passing the phase-1 acceptance criteria.
- Remaining failures include year-to-date vs. rolling-year interpretation, unsupported salary
  percentages, category negation, ranking a single largest movement vs. ranking groups, ordinal
  continuation, explicit three-month windows, previous-year continuity, Bizum superlatives, and
  preserving a window when the follow-up changes the category. These failures show the current
  small model is still unreliable on semantic composition; they should not be papered over with
  more phrase-specific deterministic routing.
- Evaluation now supports repeatable `--case` selection and an optional privacy-bounded
  `--report-file` for inspecting turn-level evidence. It deliberately omits bank identifiers,
  payment contact data and raw conversation state.

Verification for this change: 225 focused tests passed, Ruff check/format passed, and mypy passed
for all 245 orchestrator source files. The local service was rebuilt; the Gemini stack and shared
services were left running and untouched.

## Gemini 3.1 Flash Lite continuity and visualization smoke (2026-10-10)

The isolated `finguard-graph-test` stack ran the current `ORCHESTRATOR_CONVERSATION_ENGINE=model`,
`ORCHESTRATOR_CONVERSATION_GRAPH=on` build against its seeded banking API. These are live-provider
checks, separate from the frozen comparison corpus and not a production-readiness claim.

- “Desglósame mis gastos por categorías en los últimos 3 meses” returned all 9 bank groups with
  `group_by=CATEGORY` and no `group_limit`. “No quiero el top 1; muéstrame todas las categorías”
  returned all 9 again and advanced the conversation version.
- “¿Cuáles han sido mis gastos totales por mes durante el último año?” returned the bank total and
  month groups (`group_by=MONTH`, rolling `LAST_N_MONTHS:12`). “¿Y en octubre?” narrowed the
  displayed window to October while retaining expense direction and month grouping.
- Asking for the graph of the category result returned a validated bar chart with all 9 categories.
  “Cámbialo a líneas” returned a line-chart derivation from the saved chart context and advanced
  the conversation version. The orchestrator tests also assert that visualization restyling reuses
  the banking result without another query.
- Prompt changes taught a follow-up to patch only the category and keep the active period. Gemini
  then passed the exact two-turn frozen case `a_new_category_over_a_period_that_stays`: gasoline
  followed by restaurants both returned `LAST_MONTH`, with 0 violations in the isolated rerun.
- The focused 7-turn Gemini run also exposed an outcome mapping defect: the model emitted
  `Delegate(UNSUPPORTED)` for a salary-denominator ratio, but the graph labelled it
  `INVALID_DOMAIN`. The graph now returns `UNSUPPORTED`, preserves the previous focus, and performs
  no banking read; a graph-turn test covers that terminal path. The post-fix live subset had one
  transient provider error in the category case, so that run was not scored; rerunning that case
  alone completed both turns with 0 violations.

Verification after these changes: 309 focused tests passed, Ruff passed, and mypy passed for all
245 orchestrator source files. Remaining work includes rerunning a larger Gemini corpus cleanly,
closing its other semantic failures, and the full legacy-coordinator deprecation audit.

## PostgreSQL restart, replay and stale-turn smoke (2026-10-10)

Against the isolated Gemini graph stack, I exercised the HTTP contract with PostgreSQL-backed
checkpoints, canonical dialogue storage and turn reservations:

1. A new gasoline query returned version 1.
2. After restarting only the orchestrator container, the same conversation asked for restaurants.
   It returned version 2, kept the same conversation id and retained `LAST_MONTH`.
3. I repeated that second request after another orchestrator restart with the same
   `Idempotency-Key` and original version-1 conversation payload. It replayed the exact same answer,
   returned version 2 and reported `X-FinGuard-Model-Calls: 0`.
4. A different request sent with the old version-1 payload returned HTTP 409 (`STALE_CONVERSATION`).

This is direct evidence for restart continuity, durable idempotent replay and stale-version
rejection in the tested single-instance stack. It does not prove multi-instance deployment behavior,
checkpoint/reservation retention policy, schema migration across releases, or a complete provider A/B
comparison. The shared development stack was not restarted.

## Metric and counterparty follow-up repair (2026-10-10)

The live Gemini subset identified two additional regression failures, so the prompt now states that
an unqualified “how much” means `SUM` and cannot inherit `AVERAGE` or `COUNT`. With an average query
active, the planner gets a context-matched example that changes only the metric. When no query is
active, a representative Bizum example shows the bank-backed `COUNTERPARTY` grouping for a stated
five-month window.

- `an_average_may_not_answer_a_total`: 2 turns, 0 violations. The follow-up changed `AVERAGE` to
  `SUM`, kept `GROCERIES` and `LAST_N_MONTHS:12`, and returned €5,798.07 from 105 bank movements.
- `a_question_about_people_is_answered_with_people`: 1 turn, 0 violations. The plan used
  `TRANSFER_BIZUM`, `COUNTERPARTY`, and `LAST_N_MONTHS:5`, returning the bank's named groups.
- A live query “Which months had the highest expenses in the last year?” returned the top three
  month groups with `MONTH`, `SUM`, `LAST_N_MONTHS:12`, and `group_limit=3`.

These prompts were adjusted from regression failures. Their reruns are regression checks, not an
unbiased generalization score. The generalization set remains unopened. The broader Gemini subset
still needs a clean rerun after all current changes.
