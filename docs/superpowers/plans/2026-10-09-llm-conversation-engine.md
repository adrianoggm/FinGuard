# LLM Conversation Engine (phase 1): Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a sibling conversation engine behind `ORCHESTRATOR_CONVERSATION_ENGINE=model`. An LLM turns each analytics turn into typed operations; pure code applies them to a canonical, versioned state, validates and authorises them, and executes them through the existing banking contracts. Then measure it against the rules engine on a frozen regression set and a frozen generalization set.

**Architecture:** message → `TurnInterpreterPort` (one LLM call, closed JSON schema) → `Operation`s → pure grounding against catalogues → pure transitions on `DialogueState` (invariants) → structural validation → executor (FinancialQueryPort / AccountSummaryPort) → presenter (existing writers) → state saved with compare-and-set. The engine is a new package that never imports the rules engine's interpreters (enforced by a test). Money flows are delegated to the existing route.

**Tech Stack:** Python 3.12, FastAPI, pydantic v2, dataclasses, pytest, uv. Ollama `qwen2.5-coder:7b` (4096 ctx) locally; Groq `openai/gpt-oss-120b` when quota allows.

**Spec:** `docs/superpowers/specs/2026-10-09-llm-conversation-engine-design.md`

## Global Constraints

- **Language:** code, comments, tests and commits are in English; the person-facing text is in Spanish. Match the repo's style: comments explain *why*.
- **Commits:** atomic, `git commit -m "<subject>" -- <exact paths>`, `git add` only by path. No `Co-Authored-By` trailer, no push, no amend.
- **Default `rules`:** with the flag at its default, production behaviour is byte-identical. The full existing suite stays green: the baseline at a478f219 is 6377 passed, 4 skipped (3310+3 / 1148+1 / 1708 / 211).
- **Engine import boundary:** the engine packages (`domain/engine`, `application/engine`, `infrastructure/engine`) must not import:
  - `application/queries/readers/*`
  - `ask_financial_question`
  - `turn_arbiter`
  - `window_words`
  - `account_words`
  - `payment_words`
  - `merchant_words`
  - `explicit_facts`
  - `continuation_patch`
  - `plan_verifier`
  - `capability_guard`
  - `intent_lexicon`
  - `query_recovery`
- **Who reads the sentence:** the sentence reaches exactly one component, the interpreter adapter. `category_words` may be used only to name categories and to build the glossary.
- **No figure from a model:** every figure comes from the bank and every answer text from templates.
- **Pinned text:** the rules engine's model-facing text is pinned and unchanged.
- **Prompt budget:** the engine prompt with state must fit the 4096 context. That means at most 2,800 prompt tokens, approximated as at most 9,000 characters including state, and at most 600 output tokens.
- **Generalization set:** implementers NEVER open `apps/orchestrator/evaluation/engine/generalization/`, and nothing is tuned on it.
- **Off limits:** never touch the primary tree `C:\Users\adriano.garcia\Desktop\Developer\FinGuard`, never write the demo DB, never restart the stack (only the controller does, in Task 7), and never read `apps/orchestrator/.env`.
- **Test file naming:** new tests are top-level files named `tests/test_engine_*.py`, so the suite halves pick them up.

## Review Focus

1. **Two operations in one turn** («no, mejor junio vs julio en ocio y en porcentaje»). They are applied in order and give one answer with its figures. → Task 2 test `test_two_operations_apply_in_order`.
2. **A stale version from a double submit.** The second turn gets 409, and the stored state is unchanged. → Task 4 test `test_a_stale_version_is_refused_and_nothing_changes`.
3. **The model returns malformed or inapplicable operations** (a patch with no focus, an unknown field). The result is a clarification that names what is missing, never a crash or a guess. → Task 3 `test_malformed_output_becomes_clarify` and Task 2 `test_patch_without_focus_is_clarify`.
4. **A name that is not grounded** (an unknown contact, account or shop). The engine asks, naming the name; it never silently drops the filter. → Task 2 `test_unknown_person_is_clarify_naming_it`.
5. **A payment in the middle of an engine conversation.** It is delegated to the rules route, and afterwards the engine's focus is intact. → Task 5 `test_payment_is_delegated_and_focus_survives`.

---

### Task 1: Freeze the regression set and generate the generalization set (before any engine code)

**Files:**
- Create: `apps/orchestrator/evaluation/engine/regression/conversations.yaml`: a byte copy of `apps/orchestrator/evaluation/real/conversations.yaml` at a478f219.
- Create: `apps/orchestrator/evaluation/engine/regression/ux.yaml`: a byte copy of `apps/orchestrator/evaluation/ux/conversations.yaml`.
- Create: `apps/orchestrator/evaluation/engine/generalization/conversations.yaml`: generated by a separate agent.
- Create: `apps/orchestrator/evaluation/engine/FROZEN.md`: the sha256 of each file, the date, the generator's instructions, and the rule that nothing is tuned on the generalization set.

**Interfaces:**
- Produces: the three YAML files, in the exact schema of `evaluation/real/conversations.yaml`: `conversations: [{name, goal, turns: [{say, expect: {mechanism, route?, want}}]}]`. `scripts/real_conversation.py replay --file <path>` must load them unchanged.

- [ ] **Step 1 (controller):** copy the two regression files byte for byte and compute their sha256.
- [ ] **Step 2 (a separate generator agent):** it may read only these:
  - the regression YAMLs;
  - `.claude/skills/finguard-real-conversation/SKILL.md`, for the rubric and the mechanism tags;
  - `evaluation/real/README.md`;
  - the seeded data summary: Laura, accounts «Cuenta corriente», «Hucha», «Fondo indexado»; contacts Marta Ruiz, Marta Solis Vega, Javi Nuñez, Aitor, Carmen; categories from `config/categories` or `category_words` keys; data up to yesterday.

  It never reads `src/`. It writes:
  - (a) **one paraphrased variant per regression conversation.** Same goal, same number of turns, same `expect` (copied). Wording, order of slots, typos and courtesy change. Name: `<name>-v`.
  - (b) **at least 20 new combination conversations of 5–9 turns.** Each composes at least 3 mechanisms. They cover:
    - comparison → subject change → percentage → euros → one operand → reset;
    - a pending period plus a correction plus a person reference;
    - a balance aside in the middle of a comparison;
    - account scope changes;
    - grouping then filter;
    - a flip of direction;
    - year inheritance;
    - an abandoned question;
    - a payment intent stopped at preview (stop at preview, never «sí»).

    Each turn has `expect.want` in Spanish describing the intended behaviour, not any engine's.
- [ ] **Step 3:** validate the files load:

  `uv run python -c "import yaml,sys; [yaml.safe_load(open(p,encoding='utf-8')) for p in sys.argv[1:]]" evaluation/engine/regression/conversations.yaml evaluation/engine/regression/ux.yaml evaluation/engine/generalization/conversations.yaml`

  Every mechanism must be in `real_conversation.MECHANISMS`.
- [ ] **Step 4:** write FROZEN.md with the hashes and commit:

  `git commit -m "test(evaluation): freeze the regression set and a generalization set for the engine" -- apps/orchestrator/evaluation/engine/`

### Task 2: Canonical state, operations, grounding and transitions (pure domain)

**Files:**
- Create: `apps/orchestrator/src/finguard_orchestrator/domain/engine/__init__.py`
- Create: `.../domain/engine/state.py`: `DialogueState`, foci, `Pending`, `Scope`, `DialogueTurn`, `Measure`.
- Create: `.../domain/engine/operations.py`: the operation dataclasses and `PlanFields`.
- Create: `.../domain/engine/grounding.py`: pure resolution of names against `Grounds`.
- Create: `.../domain/engine/transitions.py`: `apply(state, ops, *, today) -> Transition`.
- Test: `apps/orchestrator/tests/test_engine_transitions.py`, `tests/test_engine_grounding.py`

**Interfaces:**
- Consumes these existing types:
  - `FinancialPlan`, `TopMovements` (`domain/value_objects/financial_plan.py`);
  - `ComparisonPlan.between(earlier, latest) -> ComparisonPlan | None`;
  - `RatioPlan(part, whole, kind: RatioKind)`;
  - the enums in `domain/enums.py` (`Direction`, `Period`, `Metric`, `GroupBy`, `RatioKind`);
  - `RelativePeriod`, `AbsoluteRange`;
  - `Holding`, `Contact`.
- Produces:

```python
# state.py
class Measure(StrEnum): AMOUNT="AMOUNT"; COUNT="COUNT"; AVERAGE="AVERAGE"; PERCENT_CHANGE="PERCENT_CHANGE"
class Missing(StrEnum): WINDOW="WINDOW"; SUBJECT="SUBJECT"; ACCOUNT="ACCOUNT"; PERSON="PERSON"; OPERAND="OPERAND"
@dataclass(frozen=True) class QueryFocus: plan: FinancialPlan | TopMovements
@dataclass(frozen=True) class ComparisonFocus: comparison: ComparisonPlan; measure: Measure = Measure.AMOUNT
@dataclass(frozen=True) class RatioFocus: ratio: RatioPlan
@dataclass(frozen=True) class BalanceFocus: account_ids: tuple[UUID, ...] = ()
Focus = QueryFocus | ComparisonFocus | RatioFocus | BalanceFocus
@dataclass(frozen=True) class Pending: missing: Missing; draft: "PlanFields"
@dataclass(frozen=True) class Scope: account_ids: tuple[UUID, ...] = ()        # () = every account
@dataclass(frozen=True) class DialogueTurn: said: str; answered: str            # answered = plan in words, never figures
@dataclass(frozen=True) class DialogueState:
    conversation_id: UUID; version: int = 0
    focus: Focus | None = None; pending: Pending | None = None
    scope: Scope = Scope(); turns: tuple[DialogueTurn, ...] = ()
    MAX_TURNS: ClassVar[int] = 8
    def remembered(self, turn: DialogueTurn) -> "DialogueState": ...           # keeps the last MAX_TURNS

# operations.py
@dataclass(frozen=True) class PlanFields:   # every field optional; None = "not said"
    direction: Direction | None = None; window: RelativePeriod | AbsoluteRange | None = None
    categories: tuple[str, ...] | None = None; merchants: tuple[str, ...] | None = None
    people: tuple[str, ...] | None = None; accounts: tuple[str, ...] | None = None   # names as the model wrote them
    excluded_categories: tuple[str, ...] | None = None; excluded_merchants: tuple[str, ...] | None = None
    metric: Metric | None = None; group_by: GroupBy | None = None; limit: int | None = None
@dataclass(frozen=True) class Ask: fields: PlanFields
@dataclass(frozen=True) class Patch: target: Literal["focus","earlier","latest","pending"]; changes: PlanFields
@dataclass(frozen=True) class Compare: shared: PlanFields; earlier: PlanFields; latest: PlanFields
@dataclass(frozen=True) class ChangeMeasure: measure: Measure
@dataclass(frozen=True) class SelectOperand: which: Literal["earlier","latest"]
@dataclass(frozen=True) class Reset: pass
@dataclass(frozen=True) class Balance: accounts: tuple[str, ...] = ()
@dataclass(frozen=True) class Clarify: missing: Missing; about: str | None = None   # `about` = a name to quote back, never prose
@dataclass(frozen=True) class Smalltalk: kind: Literal["GREETING","THANKS","IDENTITY","HELP"]
@dataclass(frozen=True) class Delegate: to: Literal["PAYMENT","TRANSFER","BOOKING","CHART","UNSUPPORTED"]
Operation = Ask | Patch | Compare | ChangeMeasure | SelectOperand | Reset | Balance | Clarify | Smalltalk | Delegate

# grounding.py
@dataclass(frozen=True) class Grounds: categories: frozenset[str]; accounts: tuple[Holding, ...]; contacts: tuple[Contact, ...]
@dataclass(frozen=True) class Unknown: kind: Missing; name: str
def ground(fields: PlanFields, grounds: Grounds) -> PlanFields | Unknown   # folds case/accents; exact or unique-prefix match; ambiguous or none -> Unknown

# transitions.py
@dataclass(frozen=True) class Work: kind: Literal["QUERY","COMPARE","RATIO","BALANCE"]; plan: FinancialPlan | TopMovements | ComparisonPlan | RatioPlan | None; account_ids: tuple[UUID, ...] = ()
@dataclass(frozen=True) class Transition: state: DialogueState; work: Work | None; reply: Clarify | Smalltalk | Delegate | None
def apply(state: DialogueState, ops: Sequence[Operation], *, today: date, grounds: Grounds) -> Transition
```

**Transition rules** (each is a test; see Step 1):
- `Ask` builds a `FinancialPlan` from grounded fields; a missing window becomes `Pending(WINDOW, fields)` plus `Clarify(WINDOW)`. `Ask` with `limit` and no `group_by` becomes `TopMovements`. The new focus is set by the caller after execution: `state.focus` becomes the executed work.
- `Patch(focus)` on `QueryFocus` replaces only the fields given. On `ComparisonFocus` it applies the same changes to both sides (`target` `earlier`/`latest` applies to one). A window change on a comparison moves both sides by the same shift, measured from the latest side, so the calendar order is kept. On `RatioFocus` it patches part and whole together.
- `Patch(pending)` merges into `Pending.draft`. Once complete, it runs as `Ask`.
- `Compare` builds `ComparisonPlan.between(earlier, latest)` in calendar order; `None` becomes `Clarify(OPERAND)`.
- `ChangeMeasure(PERCENT_CHANGE)` on a comparison becomes `RatioPlan(PERCENT_CHANGE)` over the pair, and the focus measure becomes PERCENT_CHANGE. `AMOUNT` or `COUNT` sets the comparison metric. On a `QueryFocus` it sets the metric.
- `SelectOperand` on a comparison gives a `QueryFocus` on that side; with no comparison it becomes `Clarify(OPERAND)`.
- `Reset` clears focus and pending and keeps scope.
- `Balance` gives `Work(BALANCE)` and does not touch focus.
- An op that cannot apply (a patch with no focus, an op on the wrong focus kind) becomes `Clarify` with the most specific `missing`. It never raises.
- **Invariant check after every op:** for a `Patch`, every plan field not named in `changes` equals the base's (asserted in code, so a violation raises `InvariantViolation`, which the handler turns into `Clarify` and logs `engine.invariant_violation`).
- Scope: `accounts` in fields becomes `Scope(account_ids)` for that work only. `Scope` changes only when an `Ask`/`Patch` names accounts.

- [ ] **Step 1: Write failing tests.** One per rule above plus Review Focus 1, 3 and 4. Examples (write all of them in this style):

```python
TODAY = date(2026, 10, 8)
JUNE = AbsoluteRange(date(2026,6,1), date(2026,6,30)); JULY = AbsoluteRange(date(2026,7,1), date(2026,7,31))
G = Grounds(categories=frozenset({"RESTAURANTS","LEISURE","TRANSPORT_FUEL"}), accounts=(), contacts=(Contact(UUID(int=7),"Marta Ruiz","600"),))

def test_patch_on_comparison_changes_subject_on_both_sides_and_keeps_months():
    s = state_with(ComparisonFocus(ComparisonPlan.between(plan(JUNE,["RESTAURANTS"]), plan(JULY,["RESTAURANTS"]))))
    t = apply(s, [Patch("focus", PlanFields(categories=("LEISURE",)))], today=TODAY, grounds=G)
    assert t.work.kind == "COMPARE"
    assert [p.categories for p in (t.work.plan.earlier, t.work.plan.latest)] == [("LEISURE",), ("LEISURE",)]
    assert (t.work.plan.earlier.date_range, t.work.plan.latest.date_range) == (JUNE, JULY)

def test_two_operations_apply_in_order():
    s = state_with(QueryFocus(plan(JULY,["RESTAURANTS"])))
    t = apply(s, [Compare(PlanFields(categories=("LEISURE",)), PlanFields(window=JUNE), PlanFields(window=JULY)),
                  ChangeMeasure(Measure.PERCENT_CHANGE)], today=TODAY, grounds=G)
    assert t.work.kind == "RATIO" and t.work.plan.kind is RatioKind.PERCENT_CHANGE

def test_patch_without_focus_is_clarify():
    t = apply(DialogueState(UUID(int=1)), [Patch("focus", PlanFields(categories=("LEISURE",)))], today=TODAY, grounds=G)
    assert t.work is None and isinstance(t.reply, Clarify) and t.reply.missing is Missing.SUBJECT

def test_unknown_person_is_clarify_naming_it():
    t = apply(DialogueState(UUID(int=1)), [Ask(PlanFields(people=("Pepe",), window=JULY))], today=TODAY, grounds=G)
    assert isinstance(t.reply, Clarify) and t.reply.missing is Missing.PERSON and t.reply.about == "Pepe"
```

- [ ] **Step 2:** `uv run pytest -q tests/test_engine_transitions.py tests/test_engine_grounding.py` → FAIL (import errors).
- [ ] **Step 3:** Implement `state.py`, `operations.py`, `grounding.py` and `transitions.py` to the interfaces above. They contain no I/O and must not import anything outside `domain/` (and nothing from the boundary list).
- [ ] **Step 4:** The tests pass; `uv run ruff check`, `ruff format --check` and `mypy src` are clean.
- [ ] **Step 5:** Commit `feat(engine): a canonical dialogue state and pure transitions over typed operations`.

### Task 3: The LLM interpreter adapter (prompt, closed schema, parsing)

**Files:**
- Create: `.../domain/ports/turn_interpreter.py`. It defines `TurnInterpreterPort` with `async def interpret(self, sentence: str, state: DialogueState, *, today: date) -> tuple[Operation, ...]`, and `InterpretationFailedError(DomainError)`.
- Create: `.../infrastructure/engine/__init__.py`, `operations_schema.py`, `operations_prompt.py`, `llm_turn_operations.py`.
- Test: `tests/test_engine_interpreter.py`.

**Interfaces:**
- Consumes `LlmProviderPort.complete(prompt, *, model, temperature, max_output_tokens, schema) -> str`, plus Task 2's `Operation` types and `DialogueState`.
- Produces `LlmTurnOperations(provider, *, model: str, temperature: float, max_output_tokens: int, categories: Mapping[str, str])`, which implements `TurnInterpreterPort`.

**Schema:** a JSON object `{"operations": [ ... 1..4 items ... ]}`.
- Each item is a `oneOf` keyed by `"op"`. Every object has `additionalProperties: false`, enums for directions, periods, metrics, groupings, measures and slots, and categories restricted to the catalogue keys.
- Windows are `{"period": <Period>, "n": int?}` or `{"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}`.
- Arrays are bounded by the domain MAX_* values.

**Prompt (Spanish/English mix is fine; it is model-facing):**
- the role;
- today's date;
- the category glossary (key: Spanish name);
- the operation vocabulary, with one example per op;
- the current state rendered compactly as DATA, fenced the same way `infrastructure/interpreting/turn_prompt.py` fences data: the focus in words with explicit dates, pending, scope by account names, and the last turns as `said → answered`;
- the sentence;
- the rules:
  - output only operations;
  - never compute figures;
  - unchanged fields are omitted from a patch;
  - money movements (paying, transfers, bookings) are `delegate`.

Size: at most 9,000 characters with an 8-turn state.

**Parsing:** validate against the schema with `jsonschema`, which is already a dependency (check `pyproject.toml`; if it is absent, validate with the pydantic models generated from the same definitions). Then map to the domain dataclasses. Malformed output gets one repair retry that sends the schema error back. If it fails again, return `(Clarify(Missing.SUBJECT),)`. Provider failure raises `InterpretationFailedError`.

- [ ] **Step 1: Failing tests** with a fake provider that returns canned JSON:
  - `test_each_op_parses_to_its_dataclass` (one canned JSON per op);
  - `test_malformed_output_becomes_clarify` (invalid JSON twice gives `(Clarify(...),)` and 2 provider calls);
  - `test_one_repair_then_success`;
  - `test_prompt_fits_the_budget` (a state with 8 turns, a comparison focus and pending: `len(prompt) <= 9000`);
  - `test_state_is_fenced_as_data` (the state block sits between the fences, so a sentence inside a turn cannot break out);
  - `test_the_sentence_appears_once`.
- [ ] **Step 2:** Run the tests and confirm they FAIL.
- [ ] **Step 3:** Implement the schema, prompt and adapter.
- [ ] **Step 4:** Run the tests and confirm they pass; ruff and mypy are clean.
- [ ] **Step 5:** Commit `feat(engine): an LLM interpreter that answers with typed operations under a closed schema`.

### Task 4: Executor, presenter, versioned store and the AnswerTurn handler (the vertical slice in process)

**Files:**
- Create: `.../domain/ports/dialogue_store.py`:
  - `DialogueStorePort` with `async load(conversation_id) -> DialogueState | None` and `async save(state: DialogueState, *, expected_version: int) -> DialogueState`. `save` returns the state with version+1 and raises `StaleDialogueError(current: DialogueState)` on a mismatch.
- Create: `.../infrastructure/engine/memory_dialogue_store.py`: an `asyncio.Lock` per conversation; compare-and-set.
- Create: `.../application/engine/__init__.py`, `executor.py`, `presenter.py`, `answer_turn.py`.
- Test: `tests/test_engine_answer_turn.py`, `tests/test_engine_store.py`.

**Interfaces:**
- Consumes:
  - `FinancialQueryPort.run(plan, *, account_id, today, reporting_timezone, movements=None) -> QueryAnswer` and `.ranked(...)`;
  - `AccountSummaryPort.of(...)`, `AccountDirectoryPort.all_of(account_id)`, `ContactDirectoryPort.all_of(account_id)`;
  - the writers: `answer_writer.write(answer, plan)`, `ranked`/`ranked_many`, `balance(summary, named=(), selected=None)`, `grouped_period_comparison(...)`, `comparison_value`, `money`, `plan_in_words`, `ratio_writer.write(part, whole, ratio)`;
  - Tasks 2 and 3.
- Produces:
  - `AnswerTurnHandler(interpreter: TurnInterpreterPort, store: DialogueStorePort, queries: FinancialQueryPort, summary: AccountSummaryPort, accounts: AccountDirectoryPort, contacts: ContactDirectoryPort, categories: frozenset[str])`;
  - `async def answer(self, sentence: str, *, conversation_id: UUID | None, version: int | None, account_id: UUID, today: date, reporting_timezone: str) -> EngineAnswer`;
  - `EngineAnswer(result: AskFinancialQuestionResult | None, state: DialogueState, delegate_to: str | None, model_calls: int, stages_ms: dict[str, int])`. `result` is `None` exactly when `delegate_to` is set;
  - `StaleDialogueError` is propagated to the caller.

**Executor** (`executor.py`): `async def execute(work: Work, ...) -> Executed`.
- Single: `run`. Ranking: `ranked`/`ranked_many`.
- Comparison: run both sides with `movements=5` and compute `difference_minor = value(latest) - value(earlier)` using `answer_writer.comparison_value`; refuse mixed currencies.
- Ratio: run part and whole, then `ratio_writer.write`.
- Balance: `summary.of`.
- Scope: `Scope.account_ids` empty means across all accounts; otherwise the plan is narrowed with `only_account_ids`. Ids come from grounding against `AccountDirectoryPort`, never from the sentence.

**Presenter** (`presenter.py`):
- builds `AskFinancialQuestionResult` (text, plan_in_words, totals, groups, movements, suggestions=());
- appends the scope note («Consultado en todas tus cuentas.» / «Consultado en <names>.»);
- clarify templates per `Missing` that name the subject from the draft, using `plan_in_words` fragments (e.g. «¿De qué periodo quieres ver tus gastos en restaurantes?»), and «No encuentro a <about> entre tus contactos.» for `PERSON`;
- smalltalk templates;
- the invariant: a result whose text has no figure when the work ran is a bug. For example, a percentage that is undefined because the base is 0 says both amounts and why: «No hay variación porcentual: en junio no gastaste nada en ocio; en julio, 23,06 €.»

**Handler flow:**
1. Load the state (new id if none).
2. Check the version: if `version` is not None and differs from the stored one, raise `StaleDialogueError`.
3. Interpret.
4. Load the grounds (categories, accounts and contacts, fetched once per turn).
5. `apply`.
6. A delegate returns `EngineAnswer(result=None, delegate_to=...)` and leaves the state unchanged.
7. Execute.
8. Set the focus to the executed work.
9. Present.
10. Remember the turn (`answered = plan_in_words`).
11. Save with `expected_version`.
12. Return.

`model_calls` comes from the interpreter's own count.

- [ ] **Step 1: Failing tests.** Use a scripted interpreter (it returns given ops), the episodes stack's fake bank ports (`tests/support/episodes.py` builds `FakeLedger`/directories; reuse them) and the memory store:
  - `test_a_query_runs_and_becomes_the_focus`;
  - `test_a_follow_up_patch_keeps_the_window` (two turns: ask restaurants July, then patch categories LEISURE; the ledger saw LEISURE July);
  - `test_comparison_then_percent_then_select_operand` (three turns; the ledger plans are asserted);
  - `test_a_pending_window_is_asked_naming_the_subject_and_completed_by_the_reply`;
  - `test_reset_clears_focus`;
  - `test_balance_does_not_touch_the_focus`;
  - `test_a_stale_version_is_refused_and_nothing_changes` (Review Focus 2);
  - `test_an_undefined_percentage_still_says_both_amounts`;
  - `test_delegate_returns_no_result_and_keeps_state`;
  - `test_two_concurrent_turns_on_one_version_one_wins` (asyncio.gather of two `answer` calls with the same version: exactly one `StaleDialogueError`).
- [ ] **Step 2:** Run the tests and confirm they FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run the tests and confirm they pass; ruff and mypy are clean.
- [ ] **Step 5:** Commit `feat(engine): the vertical slice: interpret, transition, execute, present, save with a version`.

### Task 5: Wiring behind the flag (routes, delegation, version on the wire, model-call counter, import boundary)

**Files:**
- Modify: `.../bootstrap/config.py` and `apps/orchestrator/.env.defaults`. Add the settings and document why they default to `rules`:
  - `conversation_engine: Literal["rules","model"] = "rules"`;
  - `engine_model: str | None = None` (None means `planner_model`);
  - `engine_max_output_tokens: int = 600`;
  - `engine_temperature: float = 0.0`.
- Modify: `.../bootstrap/main.py`:
  - when `conversation_engine == "model"`, build `AnswerTurnHandler` with `LlmTurnOperations(planner_provider, ...)`, `MemoryDialogueStore()` and the same banking adapters, and set `app.state.engine`; otherwise set `app.state.engine = None`;
  - wrap the providers with `CountingProvider`.
- Create: `.../infrastructure/llm/counting_provider.py`. It wraps any `LlmProviderPort` and increments a request-scoped `contextvars.ContextVar[int]`.
- Create: `.../infrastructure/controllers/engine_dispatch.py`. It exposes `async def try_engine(request, question_request, who, today) -> QuestionResponse | None`. It returns `None` when the engine is off, when the echoed conversation has a money/booking workflow active (`workflow` in {payment, transfer, booking, confirmation} on the wire), or when the engine delegates. Otherwise it maps through `llm_mapper.question_to_dto` and sets `conversation.conversation_id` and `version`.
- Modify: `.../infrastructure/controllers/llm_controller.py`. Add ONE early call at the top of `ask` and of `ask_streaming`. The streaming route emits `{"type":"stage","name":"interpreting"}` and then one `answer` frame. No engine logic goes into this file.

```python
if (answered := await engine_dispatch.try_engine(request, body, who, today)) is not None:
    return answered
```

- Modify: `.../infrastructure/dto/llm_dto.py`: add `ConversationPayload.version: int | None = None`.
- Modify: `.../infrastructure/mappers/conversation_mapper.py`: carry `version` through unchanged. The rules engine ignores it.
- Create: middleware or route code that sets the response headers `X-FinGuard-Engine: rules|model` and `X-FinGuard-Model-Calls: <n>` on both routes.
- A stale version returns 409 with JSON `{"code":"STALE_CONVERSATION","conversation": <current payload>}`.
- Test:
  - `tests/test_engine_routes.py`: an HTTP test with the episodes stack, the engine on, and a scripted interpreter;
  - `tests/test_engine_boundary.py`: parses the imports of every module under the three engine packages with `ast`, fails on any boundary module, and checks that `AnswerTurnHandler` passes `sentence` to nothing but `self._interpreter.interpret`.

- [ ] **Step 1: Failing tests:**
  - `test_flag_off_keeps_rules_byte_identical` (the same response as without the engine for 3 sentences);
  - `test_engine_answers_a_query_over_http`;
  - `test_payment_is_delegated_and_focus_survives` (Review Focus 5): ask restaurants July, then «hazle un bizum de 20 a Marta» is delegated (preview from the rules route), then «no», then «¿y en ocio?» is the engine's patch over July;
  - `test_stale_version_is_409_with_current_state`;
  - `test_headers_report_engine_and_model_calls`;
  - `test_engine_imports_nothing_linguistic`.
- [ ] **Step 2:** Run the tests and confirm they FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run the tests, then the full suite: four foreground halves, `timeout 600000`, against the baseline 6377 passed, 4 skipped. ruff and mypy are clean.
- [ ] **Step 5:** Commit in two commits:
  - `feat(engine): a feature flag routes analytics turns to the engine, money turns stay on the rules route`;
  - `test(engine): the engine imports no linguistic reader`.

### Task 6: Measurement tooling

**Files:**
- Modify: `apps/orchestrator/scripts/real_conversation.py`:
  - record the `X-FinGuard-Engine` and `X-FinGuard-Model-Calls` headers per turn;
  - `summarise` reports: the conversation success rate (every turn passes; `goal_met` judged per conversation when present), model calls per turn (mean, p95), latency per engine, and figure checks.
- Create: `apps/orchestrator/scripts/interpretation_footprint.py`. It counts non-blank, non-comment lines in two fixed lists:
  - rules interpretation: `application/queries/readers/*`, `turn_arbiter.py`, `window_words.py`, `category_words.py`, `account_words.py`, `payment_words.py`, `merchant_words.py`, `explicit_facts.py`, `continuation_patch.py`, `plan_verifier.py`, `capability_guard.py`, `intent_lexicon.py`, `query_recovery.py`, `infrastructure/interpreting/*`, `infrastructure/planning/{prompt,plan_schema,llm_query_planner}.py`, and the share of `ask_financial_question.py` that is turn interpretation (count the whole file and say so);
  - engine interpretation: `infrastructure/engine/operations_*.py` and `llm_turn_operations.py`. Transitions and executor are reported separately as non-linguistic.
- Test: `tests/test_real_conversation_script.py` (extend it) and `tests/test_interpretation_footprint.py`.

- [ ] **Step 1:** Failing tests for the header capture (a fake response with headers lands in the record) and for conversation success (2 conversations, one with a failing turn, gives 50 %).
- [ ] **Step 2:** Run the tests and confirm they FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run the tests and confirm they pass.
- [ ] **Step 5:** Commit `feat(evaluation): the runner reports conversation success, model calls and the interpretation footprint`.

### Task 7: Measure (controller-led): rules vs engine, regression vs generalization, local and hosted

- [ ] **Step 1:** Rebuild the orchestrator from the branch head with `ORCHESTRATOR_CONVERSATION_ENGINE=rules`, and replay both sets:
  - `real_conversation.py replay --file evaluation/engine/regression/conversations.yaml --ux evaluation/engine/regression/ux.yaml --out evaluation/engine/runs/<date>-rules-regression`;
  - the same with `--file evaluation/engine/generalization/conversations.yaml` (no `--ux`), giving `…-rules-generalization`.
- [ ] **Step 2:** Recreate the orchestrator only, with `ORCHESTRATOR_CONVERSATION_ENGINE=model` (environment override, no rebuild). Replay both sets, giving `…-model-regression` and `…-model-generalization`.
- [ ] **Step 3:** Blind judging. One judging agent per set gets the two runs labelled A and B, with no engine name, and judges every turn with the skill's rubric plus `goal_met` per conversation. Then `summarise` each.
- [ ] **Step 4:** If the hosted quota is available, run the model engine on the generalization set with Groq `openai/gpt-oss-120b`, using `--provider-retries` as needed.
- [ ] **Step 5:** Run `scripts/interpretation_footprint.py` and record the counts.
- [ ] **Step 6:** Write `docs/evaluation/2026-10-09-llm-conversation-engine.md`. It contains:
  - the metrics table: semantic accuracy, context conservation, plan coherence (judge plus invariant violations), bank results, recovery, conversation success, latency per engine, model calls per turn, and footprint;
  - regression and generalization reported separately;
  - every failure and regression;
  - the decision against the spec's success criteria.

  Commit the runs and the report.

### Task 8: LangGraph capability-addition experiment (read-only)

**Question:** Does a small LangGraph StateGraph make a new multi-step analytic capability cheaper to add and safer to recover, or does it merely move FinGuard's orchestration rules into graph nodes?

This is an isolated experiment after Task 7 has been recorded. Keep the current engine as the control. Put the graph behind a separate opt-in flag; the default remains the measured engine. Reuse its canonical operations, grounding, transition validation, banking ports, and presenter. LangGraph owns sequencing and the bounded loop only. It does not own financial authorization, version checks, idempotency, or the canonical conversation store.

**Graph shape:** `load_and_reserve → plan → validate_and_apply → execute_read_only → assess_result → (plan | present_and_commit)`.
The planner makes a Structured Outputs request. FinGuard executes the typed operation through existing ports. If the result needs investigation, the graph makes a separate Structured Outputs request with a bounded, value-minimized result summary; it never combines Structured Outputs and tool calling in one Groq request. The assessment can ask for another allow-listed read-only operation or stop. Cap the experiment at three banking executions and three model calls per user turn; exceeding either limit returns a grounded partial answer or a clarification. Delegated money flows leave this graph and keep their existing preview, confirmation, authorization, and idempotency path.

**State and recovery rules:**
- Treat `DialogueState` plus its existing version as canonical. A checkpoint is an execution-resume record, not a competing conversation store.
- Give each submitted turn a stable request/idempotency key. Atomically reserve `(account_id, conversation_id, expected_version, request_id)` before model or bank work. A duplicate request returns/resumes that turn; a different request against the same version gets the existing stale-version response. Keep the final compare-and-set.
- Checkpoint only after deterministic boundaries. Persist the graph/protocol version, state schema version, turn id, account scope, validated operations, and execution status. Store the minimum result needed for assessment; do not checkpoint prompts, credentials, or unrestricted banking payloads.
- Make read-only execution resumable without silently advancing conversation state twice. Do not route a payment, transfer, booking, or other mutation through the graph in this task.
- Use a durable PostgreSQL checkpointer only if its transaction/retention behavior can be verified in this experiment. Otherwise mark durability as unproven and do not claim crash recovery; do not let an in-memory checkpointer stand in for the production persistence requirement.
- Keep an explicit iteration budget and allow-list in deterministic code. The LLM may request the next semantic operation; it cannot bypass grounding, state transitions, scope, or authorization.

- [ ] **Step 1:** Pick one read-only question that requires result-dependent investigation, such as «¿en qué comercios gasto más que antes y se debe a más compras o a importes mayores?». Verify the banking contracts provide the needed aggregate and movement evidence. Write down answerable claims, the allowed follow-up operations, the iteration budget, and what must remain unknown when the bank data cannot establish a cause.
- [ ] **Step 2:** Record the baseline implementation and per-file code size for the same capability using the existing orchestration. Do not change Tasks 6/7 runs or their report results.
- [ ] **Step 3:** Add the opt-in LangGraph StateGraph in the orchestrator. Reuse existing domain/application components through thin adapters; avoid a second domain model and avoid copying existing validation into nodes. Use Groq-compatible separate structured planning calls. Add tests for loop termination, malformed/unauthorized operations, no unsupported numeric claims, provider/bank failure recovery, duplicate request, stale concurrent turn, checkpoint resume, and graph/schema version mismatch.
- [ ] **Step 4:** Add at least 3 capability conversations to `evaluation/engine/capability/conversations.yaml`; run the same cases through the current engine and graph, blind-judge them, and record correct answers, continuity, unsupported claims, model calls/tokens, latency, retries/recovery, and failures.
- [ ] **Step 5:** Compare total code added by responsibility (graph orchestration, adapters, validation, persistence/concurrency, tests) and identify which existing orchestration logic was removed. Report whether LangGraph reduced specialized coordination code, the recovery behavior actually demonstrated, and production gaps including durable checkpointing, idempotency, retention, and migration. Keep Task 7's phase-1 success decision separate.
- [ ] **Step 6:** Append the experiment and its limitations to the report, then commit the experiment and report.
