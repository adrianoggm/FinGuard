# Conversation state, slice 1 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** a conversation that leaves the conventional path keeps working: a reset, a new intent or a reply to a pending question is arbitrated before any reader claims the turn; follow-ups patch what was understood; analytics stop asking for the account — and real end-to-end conversations, judged against the ledger, decide whether it worked.

**Architecture:** one arbitration function at the top of `AskFinancialQuestionHandler._steps` (and the payment gate it feeds), reusing the existing readers; deterministic follow-up patches over the committed base (single plans and comparisons); a setting for the account default; a FinGuard skill plus a runner script for real conversations against the running stack, with figures re-computed in SQL.

**Tech Stack:** Python 3.12, FastAPI, pytest (asyncio auto), uv, ruff, mypy strict; Postgres 18; Ollama (`qwen2.5-coder:7b`, context 4096); Playwright MCP for the web path.

**Spec:** `docs/superpowers/specs/2026-10-07-conversation-state-slice-1-design.md` (read it first; §1 lists the measured failures this plan targets).

## Global Constraints

- Worktree `C:\Users\adriano.garcia\Desktop\Developer\FinGuard-wt-step2`, branch `task/conversation-step2`, base `df32ac4` (develop 9c42b58 + the spec). Never touch `C:\Users\adriano.garcia\Desktop\Developer\FinGuard` (the primary tree holds another assistant's uncommitted work).
- No model-facing text changes: the planner, turn-interpreter, visual and payment prompts and schemas stay byte-identical (`tests/test_the_query_in_words.py`, `tests/test_prompt.py` pin them).
- No figure is ever written by a model; answer texts stay templates.
- Code, comments, tests, commits in English; what the person reads in Spanish; match the files' style (they explain *why* in comments and docstrings).
- One coherent change per commit; `git commit -m "<subject>" -- <exact paths>`; never `git add -A`; no `Co-Authored-By` trailer; no push, no merge.
- The live stack is the demo's: never load, truncate or migrate the database at 127.0.0.1:5442 (the dataset is the person's to load); read-only SQL only.
- Full orchestrator suite = four foreground calls, each Bash call with `timeout: 600000`: `uv run pytest -q tests/test_[a-l]*.py --deselect tests/test_conversation_corpus.py`, `uv run pytest -q tests/test_[m-s]*.py`, `uv run pytest -q tests/test_[t-z]*.py`, `uv run pytest -q tests/test_conversation_corpus.py` (from `apps/orchestrator`). Baseline at develop 9c42b58: **5370 passed, 4 skipped, 0 failed** (2792+3 / 1074+1 / 1294 / 210).
- An expectation in `evaluation/ux/conversations.yaml` or `evaluation/real/conversations.yaml` is never edited to make a turn pass.

## Review Focus

- A RESET word inside a real sentence («no, déjalo en restaurantes», «nada más que gasolina») must not wipe the state — only a turn that is *only* the reset words is a RESET.
- A payment order while a payment is already being prepared or confirmed must go to that payment's own gates (confirmation first), not start a second one.
- A reply that fills a slot AND names a new subject («este año pero en gasolina») is ANSWER_PENDING with both slots, not a new question that drops the pending draft.
- The account default must not change balances or payments: «¿cuánto tengo?» lists every account as today; a payment still names its source account.
- A direction flip on a counterparty plan («y lo que me mandó ella») must keep the counterparty and window and change only the direction; on a plan with no counterparty it must not invent one.

---

### Task 1: Real-conversation validation skill, runner and baseline

**Files:**
- Create: `.claude/skills/finguard-real-conversation/SKILL.md`
- Create: `apps/orchestrator/scripts/real_conversation.py`
- Create: `apps/orchestrator/evaluation/real/conversations.yaml`
- Create: `apps/orchestrator/evaluation/real/README.md`
- Create: `apps/orchestrator/tests/test_real_conversation_script.py`
- Create: `.gitattributes` entry (modify): `*.sh text eol=lf`

**Interfaces:**
- Produces: `uv run python scripts/real_conversation.py replay --file evaluation/real/conversations.yaml --out evaluation/real/<date>-<sha>` and `uv run python scripts/real_conversation.py say --state <file.json> "<sentence>"` (one turn of an improvised conversation, state kept in the file); both write per-turn records `{conversation, turn, said, outcome, text, plan_in_words, last_plan, total_minor, movement_count, groups, seconds, sql: {total_minor, movement_count} | null, figure_check: "match" | "mismatch" | "n/a"}`.

- [ ] **Step 1: `.gitattributes` makes compose start on Windows.** Add the line `*.sh text eol=lf` (the reconcile script mounted into the Postgres container failed with CRLF: `set: illegal option -`). Run `git add --renormalize infrastructure/postgres/reconcile-orchestrator.sh` and confirm `git ls-files --eol infrastructure/postgres/reconcile-orchestrator.sh` shows `i/lf w/lf`. Commit: `fix(repo): shell scripts keep LF on a Windows checkout`.

- [ ] **Step 2: The runner script — failing tests first.** `tests/test_real_conversation_script.py`, pure functions only (no network):

```python
from scripts.real_conversation import expected_sql, figure_check, formatted_euros

PLAN = {"direction": "EXPENSE", "start": "2026-07-01", "end": "2026-07-31",
        "categories": ["RESTAURANTS"], "merchants": [], "merchant_terms": [],
        "counterparties": [], "excluded_categories": [], "excluded_merchants": [],
        "across_accounts": True, "only_account_id": None}

def test_the_sql_sums_the_debits_of_the_window_and_category() -> None:
    sql, params = expected_sql(PLAN, account_id="08aba04f-2b30-4639-88a6-eac741153ce0",
                               timezone="Europe/Madrid")
    assert "assistant_read.transactions_v1" in sql
    assert "category = ANY" in sql and params["categories"] == ["RESTAURANTS"]
    assert params["start"] == "2026-07-01" and params["end"] == "2026-07-31"

def test_a_figure_that_differs_is_a_mismatch() -> None:
    assert figure_check(answer={"total_minor": 13357, "movement_count": 6},
                        sql={"total_minor": 13357, "movement_count": 6}) == "match"
    assert figure_check(answer={"total_minor": 13357, "movement_count": 6},
                        sql={"total_minor": 13400, "movement_count": 6}) == "mismatch"
    assert figure_check(answer={"total_minor": None, "movement_count": None}, sql=None) == "n/a"

def test_euros_are_formatted_as_the_answers_write_them() -> None:
    assert formatted_euros(189597) == "1.895,97 €"
```

Run: `uv run pytest -q tests/test_real_conversation_script.py` → FAIL (module missing).

- [ ] **Step 3: Implement `scripts/real_conversation.py`.**
  - HTTP: `POST {base}/v1/questions` with `Authorization: Bearer <token>` (default token `demo-token-laura-pays`, base `http://127.0.0.1:8000`), echoing `conversation` each turn (reuse the shape of `scripts/evaluate_e2e.py:_ask`, lines 513-526).
  - The structured plan of a turn is `response["conversation"]["last_plan"]` (`PlanPayload`, `infrastructure/dto/llm_dto.py:115-162`); comparisons are `last_comparison_earlier` / `_latest`. Resolve relative periods to dates with the orchestrator's own `RelativePeriod` → dates logic rather than re-implementing it (find the function the banking request uses; `answer_writer.window_of` prints them).
  - `expected_sql(plan, account_id, timezone)`: an independent query over `assistant_read.transactions_v1` with the read-only role `finguard_query_reader` (URL `postgresql://finguard_query_reader:finguard_query_reader@127.0.0.1:5442/finguard_banking`), inside a transaction that first runs `SELECT set_config('app.account_id', %(account)s, true)` (row-level security, `apps/banking-api/.../financial_query_repository.py:42`). EXPENSE = debits (`direction = 'DEBIT'` or negative `signed_amount_minor` — check the view's columns: `transaction_id, entry_id, account_id, occurred_at, description, transaction_type, direction, amount_minor, signed_amount_minor, currency, merchant_name, category, category_is_custom, category_source, is_internal`), `NOT is_internal` unless `include_internal`, window `[start, end+1day)` in Europe/Madrid, `category = ANY`, `merchant_name = ANY` / `ILIKE` per `merchant_terms`, `only_account_id`. Only plans of kind SUM/COUNT without grouping are checked mechanically in this slice; others get `"n/a"` and are judged by the rubric.
  - `figure_check(answer, sql)` compares `total_minor` and `movement_count`; the record keeps both.
  - `replay` runs every conversation in the YAML, `say` runs one turn with a state file (`{"conversation": ..., "turns": [...]}`) so the skill's runner can improvise turn by turn.
  - Preflight subcommand `preflight`: `/health` of orchestrator (8000), banking (8001), gateway (8003); `/version` sha; newest `occurred_at` in the ledger (through the view, as above) must be yesterday or today (Europe/Madrid) — otherwise exit 2 with «Carga los datos con `just seed`»; record `nvidia-smi --query-gpu=pstate,clocks.sm --format=csv,noheader` and Ollama `/api/ps`.
  - Output: `<out>.json` (all records + preflight) and `<out>.md` (transcript per conversation, per-turn verdict columns left for the skill's judgement: `intent`, `context`, `pending`, `clarification`, `recovery`, `naturalness 1-5`, `mechanism`, `notes`).

Run the tests → PASS. `uv run ruff check scripts tests && uv run ruff format --check scripts tests`.

- [ ] **Step 4: `evaluation/real/conversations.yaml` — the person's real conversations, as written.** Each conversation: `name`, `goal` (one line), `turns: [say: "...", expect: {route?, mechanism}]`. Include, verbatim from the session of 2026-10-06/07:
  1. `gimnasio-ultimamente`: «Me podrías decir cuánto he gastado en el gimnasio últimamente?» → «en todas mis cuentas» → «Ampliar a tres meses».
  2. `gimnasio-meses`: «Oye perdona cuanto he gastado en gimansio en los ultimos meses ?» → «Todas mis cuentas» → «perdona gimnasios !!» → «Mostrar categorías» → «y de ocio ?» → «Perdona puedes decirme que gimansio tengo vinculado ?» → «perdona el gimansio altafit que clases tiene ?» → «boxeo».
  3. `spotify-llevo`: «¿Cuánto llevo gastado en Spotify?» → «este año» → «Todas mis cuentas».
  4. `transfer-then-compare`: «quiero traspasar dinero a la hucha» → «¿Gasté más en gasolina en junio o en julio?» → «Todas mis cuentas».
  5. `marta-ella-pago`: «cuanto le mande a Marta este mes» → «y lo que me mando ella?» → «ahora quiero mandarle 20 euros a Marta Solis Vega» → «no».
  6. `comparacion-gasolina`: «junio vs julio en restaurantes» → «y en gasolina?» → «en porcentaje» → «ahora solo julio».
  7. `olvidalo`: «cuanto gaste en el super el mes pasao» → «no era eso» → «gasolina» → «olvidalo» → «cuánto tengo?».
  `expect.mechanism` uses the tags of Task 2 (intent, slot_carryover, pending_resolution, interruption, follow_up, comparison, reference, reset, unnecessary_clarification, execution).

- [ ] **Step 5: `SKILL.md`.** Frontmatter `name: finguard-real-conversation`, `description:` "Validate FinGuard with real end-to-end conversations against the running stack: replay the person's conversations, improvise new ones as a holder with a goal, drive two through the real web with Playwright's MCP, check every figure against the ledger in SQL and judge each turn against a rubric. Use before and after any change to conversation handling, and whenever asked to 'probar de verdad', 'validación real' or 'end to end'." Body, in order: (1) preflight (`real_conversation.py preflight`; stop on failure; never load or truncate data); (2) replay (`replay` → JSON/MD); (3) improvise **at least three** conversations: pick a goal from a list in the skill (gym cost then pay Marta 20 €; compare two months then change category; ask about subscriptions, correct, abandon), write each next message from the last answer as a hurried person would (typos, ellipsis, «y …?», corrections, a change of mind), stop at the goal or eight turns, using `say --state`; save each transcript into the run's output and add any failing one to `conversations.yaml` as a replayed conversation in a separate commit; (4) web: start the client on 5173 if needed (`npm --prefix apps/web run dev -- --port 5173`, 3000 may be busy), drive **two** conversations with Playwright's MCP (`browser_navigate`, `browser_type`, `browser_snapshot`), judge what the page shows (answer, «Ver la consulta», evidence, chart); (5) judge every turn: figures mechanically (`figure_check` mismatch = failure, no exceptions), the rest against the rubric with a one-line reason; (6) write `evaluation/real/<YYYY-MM-DD>-<sha>.md` with the per-mechanism table, recovery rate (after a failed turn, is the next one right?), latency per turn, and the comparison with the previous run's file; (7) never edit an expectation to make a turn pass.

- [ ] **Step 6: Commit** `scripts/real_conversation.py`, its test, `evaluation/real/conversations.yaml`, `evaluation/real/README.md`, `.claude/skills/finguard-real-conversation/SKILL.md` as `feat(evaluation): real end-to-end conversations, judged against the ledger`.

- [ ] **Step 7: Baseline (the controller runs it, not the implementer).** With the stack up from this worktree at the base commit, run the skill end to end and commit the report as `evaluation/real/2026-10-07-<sha>.md/.json` (`docs(evaluation): the baseline the slice is measured against`).

### Task 2: The battery reports by mechanism

**Files:**
- Modify: `apps/orchestrator/tests/support/episodes.py` (the `Turn` dataclass ≈:1121)
- Modify: `apps/orchestrator/evaluation/ux/conversations.yaml` (add `mechanism:` to every turn; do not change any other key)
- Modify: `apps/orchestrator/scripts/evaluate_conversation_ux.py`
- Test: `apps/orchestrator/tests/test_ux_battery_report.py`

**Interfaces:**
- Produces: `episodes.Turn.mechanism: str | None = None`; the script's JSON gains `by_mechanism: {tag: {"turns": n, "failed": k}}` and `recovery_rate: float`.

- [ ] **Step 1: Failing test.**

```python
from scripts.evaluate_conversation_ux import summarise

TURNS = [
    {"episode": "a", "turn": 1, "mechanism": "follow_up", "failures": []},
    {"episode": "a", "turn": 2, "mechanism": "follow_up", "failures": ["x"]},
    {"episode": "a", "turn": 3, "mechanism": "reset", "failures": []},
    {"episode": "b", "turn": 1, "mechanism": "intent", "failures": ["y"]},
    {"episode": "b", "turn": 2, "mechanism": "intent", "failures": ["z"]},
]

def test_failures_are_counted_per_mechanism_and_recovery_is_the_next_turn() -> None:
    report = summarise(TURNS)
    assert report["by_mechanism"]["follow_up"] == {"turns": 2, "failed": 1}
    assert report["by_mechanism"]["intent"] == {"turns": 2, "failed": 2}
    # a/2 failed and a/3 passed: recovered; b/1 failed and b/2 failed: not recovered
    assert report["recovery_rate"] == 0.5
```

- [ ] **Step 2:** run → FAIL. **Step 3:** add `mechanism: str | None = None` to `episodes.Turn`; tag every turn of `evaluation/ux/conversations.yaml` (tags as in Task 1 Step 4) — keys added, nothing else changed; implement `summarise(turns)` and call it at the end of `main()`, printing one line per mechanism and the recovery rate, writing both into the JSON. **Step 4:** tests pass; `uv run python scripts/evaluate_conversation_ux.py --stub` runs (routing only). **Step 5:** commit `test(evaluation): the battery says which mechanism failed and whether the next turn recovered`.

### Task 3: Payment vocabulary — the infinitive with a clitic, and a bare amount to a person

**Files:**
- Modify: `apps/orchestrator/src/finguard_orchestrator/domain/services/payment_router.py` (`_PAYING` ≈:143-152, `_EXPLICIT_ORDER` ≈:157-161, `is_payment` ≈:332-368)
- Test: `apps/orchestrator/tests/test_payment_router.py` (lists at ≈:22-46 and ≈:48-73), `apps/orchestrator/tests/test_expert_agent_words.py` (next to R24 ≈:389-404)
- Modify: `docs/architecture/adr-012-a2a-consumer-gateway.md` (≈:195, drop R36 from the limits), `docs/conversational-capabilities.md` (≈:133)

- [ ] **Step 1: Failing tests.** Add to the recognised list: «ahora quiero mandarle 20 euros a Marta», «quiero mandarle 20 euros a Marta», «quiero enviarle 30 € a Javi», «tengo que pasarle 15 euros a Aitor», «quiero hacerle un bizum de 20 a Marta», «pásale 20 a Marta», «mándale 20 a Javi». Add to the not-a-payment list: «¿cuánto le mandé a Marta este mes?», «¿cuánto le pasé a Javi en julio?», «quiero saber cuánto le mandé a Marta», «pásale el informe a Marta». In `test_expert_agent_words.py`, R36:

```python
@pytest.mark.parametrize("sentence", ["pásale 15 a Diego por el pádel de mañana",
                                      "dale 8 a Laura por la clase de yoga de mañana"])
def test_r36_a_payment_named_with_an_activity_is_a_payment_not_a_search(sentence: str) -> None:
    assert payment_router.is_payment(sentence)
    assert not expert_agent_words.asks_an_expert_agent(sentence)
```

- [ ] **Step 2:** run `uv run pytest -q tests/test_payment_router.py tests/test_expert_agent_words.py` → the new cases FAIL. **Step 3:** extend `_PAYING` with the infinitive + enclitic forms (`mandarle|mandarles|enviarle|enviarles|pasarle|pasarles|pagarle|pagarles|hacerle`) and `pasa|pasale|dale` imperatives; allow a bare amount (`\d+(?:[.,]\d{1,2})?`) as money only when it follows one of those verbs and is followed by «a <name>» (keep `_MONEY` unchanged elsewhere); keep the past-tense guards (`describes_a_past_payment`) so «¿cuánto le mandé…?» stays a query. **Step 4:** both files pass; run `tests/test_question_routing.py`, `tests/test_turn_interpreter.py`, `tests/test_conversation_corpus.py` too. **Step 5:** update ADR-012 and conversational-capabilities (R36 closed, with this commit's subject). Commit code+tests (`fix(payments): «quiero mandarle 20 €» and «pásale 20 a Marta» are orders to pay`) and docs separately.

### Task 4: Arbitration — RESET and a new intent come before the pending question

**Files:**
- Create: `apps/orchestrator/src/finguard_orchestrator/domain/services/turn_arbiter.py`
- Modify: `.../domain/services/merchant_words.py` (`_NEVER_A_MERCHANT_WORDS` ≈:104-142: add «olvidalo», «olvídalo», «no importa»)
- Modify: `.../application/queries/financial_turn.py` (`AskFinancialQuestionQuery` ≈:336-372: add `arbitration: TurnType | None = None`)
- Modify: `.../infrastructure/controllers/llm_controller.py` (`ask` ≈:2049-2095 and the streaming twin ≈:2296-2315: compute the arbitration once after `analysis`, use it in `_is_an_order_to_pay`, pass it to the query)
- Modify: `.../application/queries/ask_financial_question.py` (`_steps` ≈:739: RESET handled first, before the retry step ≈:746 and the season ask ≈:830)
- Create: `apps/orchestrator/tests/support/pending.py` (shared helpers for Tasks 4-8)
- Test: `apps/orchestrator/tests/test_turn_arbiter.py` (unit), `apps/orchestrator/tests/test_a_pending_question_is_not_a_cage.py` (HTTP)

**Interfaces:**
- Produces: `class TurnType(StrEnum): RESET, CONFIRMATION, NEW_OPERATION, ANSWER_PENDING, CORRECTION, FOLLOW_UP, NEW_QUESTION`; `def arbitrate(sentence: str, conversation: Conversation, analysis: TurnAnalysis, *, today: date) -> Arbitration` with `@dataclass(frozen=True) class Arbitration: type: TurnType; fills: frozenset[Slot] = frozenset()`. Task 5 fills `fills`; Task 7 fills FOLLOW_UP.

- [ ] **Step 0: Shared helpers** in `tests/support/pending.py` (used by Tasks 4-8):

```python
from tests.support import episodes

SETTLED = {"account_selection_mode": "ALL"}          # episodes.opening(...) uses the same shape

def ask(stack, question: str, conversation: dict | None = None) -> dict:
    response = stack.client.post(
        "/v1/questions", json={"question": question, "conversation": conversation}
    )
    assert response.status_code == 200, response.text
    return response.json()

def asking_for_the_window_stack(*, settled: bool = True):
    """The episodes stack, with a planner that answers a window-less spending question with the
    window clarification and a draft that keeps its subject -- the real planner's behaviour
    (`llm_query_planner.py:464-491`). Build it the way
    `tests/test_a_reply_to_the_account_question.py:198-206` builds its handler; the scripted result
    is a `PlanningResult` carrying the window question and a `FinancialPlanDraft` (use the
    classmethod `domain/models/planning_result.py` defines for a clarification with a draft)."""

def account_question_stack():
    """The episodes stack with a directory and nothing settled, whose planner answers
    «¿Cuánto llevo gastado en Spotify?» with a complete plan, so `_scoped` asks the account
    (AFQ ≈:4421) -- the live path of the 2026-10-07 failure."""
```

Complete the two builders from the cited code; every later task imports from here, so no test file
re-invents them.

- [ ] **Step 1: Unit tests (failing).**

```python
from datetime import date
import pytest
from finguard_orchestrator.domain.models.conversation import IDLE
from finguard_orchestrator.domain.services.turn_arbiter import TurnType, arbitrate
from finguard_orchestrator.domain.services import turn_analyzer  # build an analysis the way tests do

TODAY = date(2026, 8, 5)

@pytest.mark.parametrize("sentence", ["olvídalo", "olvidalo", "déjalo", "no importa", "nada",
                                      "no era eso", "Olvídalo, gracias"])
def test_only_the_reset_words_are_a_reset(sentence: str) -> None:
    assert arbitrate(sentence, IDLE, analysis_of(sentence), today=TODAY).type is TurnType.RESET

@pytest.mark.parametrize("sentence", ["no, déjalo en restaurantes", "nada más que gasolina",
                                      "no era eso, quería decir julio"])
def test_a_reset_word_inside_a_request_is_not_a_reset(sentence: str) -> None:
    assert arbitrate(sentence, IDLE, analysis_of(sentence), today=TODAY).type is not TurnType.RESET

def test_an_order_to_pay_while_a_period_is_pending_is_a_new_operation() -> None:
    waiting = conversation_waiting_for(Slot.WINDOW)   # FinancialClarification(draft, awaiting=WINDOW)
    said = "ahora quiero mandarle 20 euros a Marta"
    assert arbitrate(said, waiting, analysis_of(said, waiting), today=TODAY).type is TurnType.NEW_OPERATION
```

(`analysis_of` / `conversation_waiting_for`: small helpers in the test file that call `turn_analyzer.analyze` with the Deterministic interpreter and build a `FinancialClarification(FinancialPlanDraft(...), awaiting=Slot.WINDOW)` — copy how `tests/test_turn_interpreter.py` and `tests/test_account_scope_workflow.py` build them.)

- [ ] **Step 2: HTTP tests (failing)** in `test_a_pending_question_is_not_a_cage.py`, on a stack whose planner asks for the window (build the handler as `tests/test_a_reply_to_the_account_question.py:198-206` does, with a scripted planner that returns the window clarification draft for «¿cuánto gasté en restaurantes?»):

```python
def test_pending_interrupted_by_new_intent() -> None:
    stack = asking_for_the_window_stack()
    asked = ask(stack, "¿cuánto gasté en restaurantes?")
    assert asked["outcome"] == "NEEDS_CLARIFICATION"
    paid = ask(stack, "mándale 20 € a Marta Solis Vega", asked["conversation"])
    assert paid["outcome"] == "AWAITING_CONFIRMATION"
    assert paid["conversation"]["amount_minor"] == 2000
    assert paid["conversation"]["recipient"].startswith("Marta Solis")

def test_reset_pending() -> None:
    stack = asking_for_the_window_stack()
    asked = ask(stack, "¿cuánto gasté?")
    forgotten = ask(stack, "olvídalo", asked["conversation"])
    assert forgotten["conversation"].get("workflow") in (None, "IDLE")
    assert forgotten["conversation"].get("last_plan") is None
    balance = ask(stack, "¿cuánto tengo?", forgotten["conversation"])
    assert balance["outcome"] == "BALANCE"

def test_an_order_while_a_payment_waits_for_confirmation_goes_to_that_payment() -> None:
    stack = episodes.a_stack()
    started = ask(stack, "hagamos un bizum a Marta Solis Vega")
    preview = ask(stack, "15 euros con concepto de comida", started["conversation"])
    assert preview["conversation"]["workflow"] == "PAYMENT_CONFIRMATION"
    ask(stack, "mándale 20 € a Javi Nuñez", preview["conversation"])
    assert len(stack.bank.prepared) == 1   # the open preview's gates answered; no second payment

def test_olvidalo_after_an_answer_is_not_a_shop() -> None:
    stack = episodes.a_stack()
    first = ask(stack, "cuanto gaste en el super el mes pasado", SETTLED)
    forgotten = ask(stack, "olvidalo", first["conversation"])
    assert forgotten["outcome"] != "PLANNED" and stack.ledger.ran.count == 1   # no second query
```

(Use «Marta Solis Vega»: the episodes contacts hold two Martas, and a bare «Marta» asks which one — `episodes.py:668-676`.)

- [ ] **Step 3:** run both files → FAIL. **Step 4: Implement.** `turn_arbiter.arbitrate` order: RESET (the sentence, folded and stripped of «gracias/vale/por favor» and punctuation, is entirely made of the closed reset lexicon) → CONFIRMATION (a `PaymentConfirmation`/`BookingConfirmation` is active: return CONFIRMATION and let the existing gates act) → NEW_OPERATION (`payment_router.is_payment(sentence)` with `analysis.intent` not LIST_CONTACTS, or the balance rows of `intent_router`) → CORRECTION (`analysis.relation is TurnRelation.CORRECTION`) → otherwise NEW_QUESTION (Task 5 adds ANSWER_PENDING before CORRECTION; Task 7 adds FOLLOW_UP). In the controller: compute once after `analysis`; `_is_an_order_to_pay` returns True when the arbitration is NEW_OPERATION and the reading is a payment (the open-slot exception no longer applies — keep the rail-word-only exception for a bare «bizum» answering «¿de qué categoría?»). In `_steps`: when `query.arbitration is TurnType.RESET`, clear `active`, `last_asked`, `retry_plan`, `pending_ratio`, `last_plan`, `prior_plan`, `last_comparison`, `last_ratio`, `answered_the_balance`, keep `account_selection` and aliases, and answer `GUIDANCE` «Vale, lo dejamos. ¿Qué quieres consultar?» (one line, in the file's voice). Add «olvidalo», «olvídalo», «no importa» to `_NEVER_A_MERCHANT_WORDS`. **Step 5:** tests pass; run the corpus and `tests/test_conversation_recovery.py`, `tests/test_the_reported_conversation.py`; then the full suite. **Step 6:** commit `feat(conversation): a reset or an order to pay comes before the question left open`.

### Task 5: Answering the pending question — every slot the reply fills, in either order

**Files:**
- Modify: `.../domain/services/turn_arbiter.py` (ANSWER_PENDING with `fills`)
- Modify: `.../application/queries/ask_financial_question.py` (`_finish_pending` ≈:2583-2664; `_resume_account_scope` ≈:4115-4183; `_answering_the_scope_question` ≈:4185-4243; ordering: an ANSWER_PENDING turn reaches `_finish_pending` / the account resume before `contextual_follow_up_plan` ≈:1498 and before every reader that acts on the query in view)
- Modify: `.../domain/value_objects/financial_plan.py` (`FinancialPlanDraft`: a `with_accounts(selection)` that sets `across_accounts` / `only_account_id(s)`, mirroring `with_window` ≈:283-312)
- Test: `apps/orchestrator/tests/test_a_reply_fills_what_it_names.py`

**Interfaces:**
- Consumes: `arbitrate` from Task 4. Produces: `Arbitration.fills ⊆ {Slot.WINDOW, Slot.YEAR, Slot.ACCOUNT, Slot.CATEGORY}`.

- [ ] **Step 1: Failing HTTP tests** (stack with a directory and no settled account, and a scripted planner that asks for the window):

```python
def test_the_account_given_to_a_period_question_is_kept_and_the_period_asked_again() -> None:
    stack = asking_for_the_window_stack(settled=False)
    asked = ask(stack, "cuánto he gastado en el gimnasio últimamente")      # «¿De qué periodo?»
    told = ask(stack, "en todas mis cuentas", asked["conversation"])
    assert told["outcome"] == "NEEDS_CLARIFICATION" and told["outcome"] != "BALANCE"
    assert told["conversation"]["account_selection_mode"] == "ALL"
    answered = ask(stack, "este año", told["conversation"])
    assert answered["outcome"] == "PLANNED" and stack.ledger.plans[-1].across_accounts

def test_the_period_given_to_an_account_question_is_kept() -> None:
    stack = account_question_stack()                                         # «¿En qué cuenta?»
    asked = ask(stack, "¿Cuánto llevo gastado en Spotify?")
    told = ask(stack, "este año", asked["conversation"])
    assert told["question"] == "¿En qué cuenta quieres consultarlo?"
    answered = ask(stack, "Todas mis cuentas", told["conversation"])
    assert stack.ledger.plans[-1].period.period is Period.THIS_YEAR          # not today..today

def test_a_reply_to_a_new_question_does_not_complete_the_previous_answer() -> None:
    stack = asking_for_the_window_stack()
    first = ask(stack, "cuanto gaste en el super el mes pasado", SETTLED)
    asked = ask(stack, "¿Cuánto gasté en el gimnasio?", first["conversation"])  # a window question
    assert asked["outcome"] == "NEEDS_CLARIFICATION"
    ask(stack, "este año", asked["conversation"])
    assert stack.ledger.plans[-1].categories != ("GROCERIES",)

def test_a_reply_that_fills_the_slot_and_names_a_subject_keeps_both() -> None:
    stack = asking_for_the_window_stack()
    asked = ask(stack, "¿cuánto gasté en restaurantes?")
    ask(stack, "este año pero en gasolina", asked["conversation"])
    plan = stack.ledger.plans[-1]
    assert plan.period.period is Period.THIS_YEAR and plan.categories == ("TRANSPORT_FUEL",)
```

- [ ] **Step 2:** run → FAIL. **Step 3: Implement.** In `arbitrate`, when `conversation.awaiting or conversation.last_asked` is set, compute what the sentence fills with the existing readers — window (`window_words.window_in(sentence, asked_for=..., today=...)`), account (`_chosen_account_scope` logic: move the pure part into `turn_arbiter` or call through a small domain function; `account_words.asks_for_all_accounts`, `named_in`, `custom_chosen_among`), category (`category_words.precisely_named_in`) — and return ANSWER_PENDING when anything is filled and the sentence is not NEW_OPERATION/RESET. In `_steps`, an ANSWER_PENDING turn goes straight to the open question: a `FinancialClarification` absorbs window and account (account via the new `FinancialPlanDraft.with_accounts`, stored as `account_selection` too) and runs when complete or asks only what is missing (the window question names the subject — Task 8 changes the text); an `AccountScopeClarification` absorbs a window into its pending plan before running. **Step 4:** tests pass; corpus, `tests/test_account_scope_workflow.py`, `tests/test_a_reply_to_the_account_question.py`, `tests/test_clarification_round_trip.py`, then the full suite. **Step 5:** commit `feat(conversation): a reply fills every slot it names, whichever was asked`.

### Task 6: A comparison is a base a follow-up can continue

**Files:**
- Modify: `.../domain/models/planning_context.py` (`can_continue` ≈:108-111)
- Modify: `.../application/queries/ask_financial_question.py` (where the continuation is built ≈:796-801 and passed ≈:1623-1654)
- Test: `apps/orchestrator/tests/test_a_comparison_survives_its_follow_up.py`

- [ ] **Step 1: Failing test** (`comparison_followup` of the spec):

```python
def test_comparison_followup() -> None:
    stack = episodes.a_stack()
    first = ask(stack, "junio vs julio en restaurantes", SETTLED)
    assert len(stack.ledger.ran) == 2
    stack.ledger.ran.clear()
    second = ask(stack, "¿y en gasolina?", first["conversation"])
    assert len(stack.ledger.ran) == 2
    windows = sorted((p.date_range.start.month if p.date_range else None) for p in stack.ledger.ran)
    assert windows == [6, 7]
    assert all(p.categories == ("TRANSPORT_FUEL",) for p in stack.ledger.ran)
```

```python
@pytest.mark.parametrize("sentence", ["cuanto gaste en julio", "¿cuánto tengo?",
                                      "junio vs julio en restaurantes",
                                      "cuanto le mande a Marta este mes",
                                      "¿en qué gasté más en julio?"])
def test_every_answered_turn_leaves_a_base(sentence: str) -> None:
    stack = episodes.a_stack()
    answered = ask(stack, sentence, SETTLED)
    conversation = conversation_mapper.to_domain(answered["conversation"])  # the wire's own mapper
    assert focus_of(conversation) is not None
```

- [ ] **Step 2:** run → FAIL (one query). **Step 3: Implement** in `_steps` before the readers that act on a query in view: when `focus_of(was)` is a `ComparisonFocus` and the sentence is a category/merchant/grouping patch (`continuation_patch.read` APPLIED, or `category_words.precisely_named_in` with an ellipsis marker «y …»), apply the patch to **both** sides (`ComparisonPlan.between(patched(earlier), patched(latest))`) and answer through the existing `_compare_periods` path; otherwise unchanged. `planning_context` accepts a comparison focus for window-free follow-ups only through this path (no planner change). **Step 4:** tests + `tests/test_comparisons*.py` + corpus + full suite. **Step 5:** commit `feat(conversation): «¿y en gasolina?» after a comparison changes both sides`.

### Task 7: Follow-ups that patch the base — direction and grouping

**Files:**
- Modify: `.../domain/services/continuation_patch.py` (`read` ≈:216-278, `_CONTINUATION` ≈:68-71; AMBIGUOUS today for a direction ≈:234-245)
- Modify: `.../domain/value_objects/plan_patch.py` (`PlanPatch` ≈:24-31: add `direction: Direction | None`)
- Modify: `.../domain/services/turn_arbiter.py` (FOLLOW_UP)
- Test: `apps/orchestrator/tests/test_a_follow_up_patches_the_base.py`

- [ ] **Step 1: Failing tests** (`rule_answer_sets_continuation` of the spec and the battery's turns):

```python
def test_rule_answer_sets_continuation() -> None:
    stack = episodes.a_stack()
    first = ask(stack, "¿cuánto le mandé a Marta este año?", SETTLED)
    stack.ledger.plans.clear()
    flipped = ask(stack, "¿y ella a mí?", first["conversation"])
    plan = stack.ledger.plans[-1]
    assert plan.counterparties == ("Marta",) and plan.direction is Direction.INCOME
    assert plan.period.period is Period.THIS_YEAR

@pytest.mark.parametrize("sentence", ["y lo que me mando ella?", "¿y lo que me ha pagado ella?",
                                      "y lo que recibí de ella"])
def test_received_from_the_same_person_flips_only_the_direction(sentence: str) -> None:
    stack = episodes.a_stack()
    first = ask(stack, "cuanto le mande a Marta este mes", SETTLED)
    stack.ledger.plans.clear()
    ask(stack, sentence, first["conversation"])
    plan = stack.ledger.plans[-1]
    assert plan.counterparties == ("Marta",) and plan.direction is Direction.INCOME
    assert plan.period.period is Period.THIS_MONTH

def test_a_flip_without_a_person_in_the_base_invents_none() -> None:
    stack = episodes.a_stack()
    first = ask(stack, "cuanto gaste en julio", SETTLED)
    ask(stack, "¿y lo que me mandó ella?", first["conversation"])
    assert stack.ledger.plans[-1].counterparties == ()

def test_mejor_por_categoria_groups_the_base() -> None:
    stack = episodes.a_stack()
    first = ask(stack, "cuanto gaste este mes", SETTLED)
    ask(stack, "mejor por categoria", first["conversation"])
    assert stack.ledger.plans[-1].group_by is GroupBy.CATEGORY
    assert stack.ledger.plans[-1].period.period is Period.THIS_MONTH
```

- [ ] **Step 2:** run → FAIL. **Step 3: Implement.** `continuation_patch.read` returns APPLIED with `direction=INCOME` for «lo que me mandó/pagó/envió <pronoun|name of the base's counterparty>», «lo que recibí de …», «y ella a mí»; `direction=EXPENSE` for the inverse; only when the base has `counterparties` (otherwise NOT_APPLICABLE). «mejor por categoría / por comercio / por meses» → `group_by` (already reads «por meses»; extend the lexicon with «mejor», «agrupa(do) por»). `arbitrate` returns FOLLOW_UP when a base exists and `continuation_patch.read` is APPLIED. In `_steps`, a FOLLOW_UP with an APPLIED patch answers `patch.apply_to(base)` without the planner (the code at ≈1632-1654 already does this for the planner path — reuse it, earlier). **Step 4:** tests + `tests/test_continuation_patch.py` + corpus + full suite. **Step 5:** commit `feat(conversation): «y lo que me mandó ella» and «mejor por categoría» patch what was asked`.

### Task 8: Defaults, and saying them

**Files:**
- Modify: `.../bootstrap/config.py` + `apps/orchestrator/.env.defaults` (`ORCHESTRATOR_ACCOUNT_SCOPE_DEFAULT=all|ask`, default `all`, documented with why)
- Modify: `.../bootstrap/main.py` (pass it to the handler), `.../application/queries/ask_financial_question.py` (`__init__` ≈:520-534; `_scoped` ≈:4385-4421: with `all` and nothing chosen, `return _across_every_account(plan)` instead of raising; `_scope_note` ≈:6617-6626: say «Consultado en todas tus cuentas.» only when the holder has more than one account **and** the sentence mentioned accounts, or the scope was chosen explicitly this turn)
- Modify: `.../domain/services/window_words.py` («llevo gastado/gastados» with no window → THIS_YEAR in `_NAMED` ≈:45-77; «últimamente» → `RelativePeriod(Period.LAST_N_DAYS, n=30)` as a separate rule, since `_NAMED` rows carry no `n` ≈:940-942)
- Modify: `.../application/queries/ask_financial_question.py` (the window question for a draft that knows its subject: «¿De qué periodo quieres ver tus gastos en restaurantes?» / «…lo que le mandaste a Marta?» — the template used where `WINDOW_QUESTION` reaches `_decline` ≈:5764 and the literal copy ≈:5533-5547; the planner's own text is not touched)
- Modify tests that script the old behaviour, deliberately, each listed in the commit body: «llevo gastado» as a clarification (`tests/test_a_conversation_that_wanders.py:1164,1198`, `tests/test_conversation_recovery.py:346,360`, `tests/test_the_reported_conversation.py:662`), the account question in `evaluation/conversations/31-account-scope.yaml` and others that expect it with no settled scope (run them with `ORCHESTRATOR_ACCOUNT_SCOPE_DEFAULT=ask` semantics where the test is *about* the question: give those tests the `ask` setting rather than rewriting them), `evaluation/planning/dev.yaml:805-809` («últimamente»: expectation note "product decision 2026-10-07").
- Test: `apps/orchestrator/tests/test_the_defaults_say_themselves.py`

- [ ] **Step 1: Failing tests.**

```python
def test_an_unqualified_spending_question_does_not_ask_for_the_account() -> None:
    stack = episodes.a_stack()                       # directory with three accounts, nothing settled
    answered = ask(stack, "¿Cuánto gasté en restaurantes en julio?")
    assert answered["outcome"] == "PLANNED"
    assert stack.ledger.plans[-1].across_accounts
    assert "Consultado en todas tus cuentas" not in answered["text"]

def test_the_old_question_is_one_setting_away() -> None:
    stack = episodes.a_stack(account_scope_default="ask")
    asked = ask(stack, "¿Cuánto gasté en restaurantes en julio?")
    assert asked["question"] == "¿En qué cuenta quieres consultarlo?"

def test_a_balance_still_lists_every_account() -> None:
    answered = ask(episodes.a_stack(), "¿cuánto tengo?")
    assert "Hucha" in answered["text"] and "Cuenta corriente" in answered["text"]

def test_a_payment_still_names_its_source_account() -> None:
    stack = episodes.a_stack()
    started = ask(stack, "hagamos un bizum a Marta Solis Vega")
    previewed = ask(stack, "15 euros con concepto de comida", started["conversation"])
    assert previewed["outcome"] == "AWAITING_CONFIRMATION"
    assert stack.bank.prepared[-1].amount_minor == 1500   # unchanged by the analytics default

def test_llevo_gastado_is_the_year_so_far_and_says_so() -> None:
    stack = episodes.a_stack()
    answered = ask(stack, "¿Cuánto llevo gastado en Spotify?")
    assert stack.ledger.plans[-1].period.period is Period.THIS_YEAR
    assert "en lo que va de año" in answered["text"]

def test_ultimamente_is_the_last_thirty_days_and_says_so() -> None:
    stack = episodes.a_stack()
    answered = ask(stack, "¿Cuánto he gastado últimamente en restaurantes?")
    assert stack.ledger.plans[-1].period == RelativePeriod(Period.LAST_N_DAYS, n=30)
    assert "últimos 30 días" in answered["text"]
```

(`episodes.a_stack(account_scope_default=...)`: add the keyword, defaulting to the settings' value.)

- [ ] **Step 2:** run → FAIL. **Step 3: Implement** as listed. **Step 4:** tests + corpus + full suite; re-run `uv run python scripts/evaluate_conversation_ux.py` (real model) and record the per-mechanism table. **Step 5:** commits, one per default: `feat(accounts): analytics use every account unless the person chose one`, `feat(window): «llevo gastado» is the year so far and «últimamente» the last 30 days`, `feat(answer): the period question names what it is about`.

### Task 9: Validation against the baseline, and the branch review

**Files:**
- Create: `apps/orchestrator/evaluation/real/2026-10-0X-<sha>.md/.json` (the run)
- Modify: `apps/orchestrator/evaluation/ux/latest.json` (the battery's last run, real model)

- [ ] **Step 1 (controller):** rebuild the orchestrator from the branch head; run the skill (`finguard-real-conversation`) end to end: preflight, replay, three improvised conversations, two through the web; compare with the baseline report of Task 1. Gate (spec §2): no figure mismatch; no turn lost to a pending question; recovery within one turn; every mechanism at or above its baseline rate.
- [ ] **Step 2:** the battery with the real model ≥ 56/60, same expectations; the 24-question probe (`scratchpad/step2/probe_bank.py` equivalent: commit it as `scripts/probe_questions.py` if used); the full suites of every service (`just check` with the database up).
- [ ] **Step 3:** whole-branch review (most capable model), one fix wave, re-run Step 1 for any fix that touches conversation handling.
- [ ] **Step 4:** commit the run reports (`docs(evaluation): slice 1 measured on real conversations`).
