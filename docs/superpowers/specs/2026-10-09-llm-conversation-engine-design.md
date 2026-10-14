# LLM conversation engine (prototype, phase 1): design

Status: accepted by the person (2026-10-08/09), who asked for no further approval round: "documenta brevemente las decisiones, implementa el prototipo, ejecuta las pruebas y presenta los resultados".

Branch: `task/conversation-engine`, from develop `4597d5fd`.

## Why

The rules engine reached 89 % of turns on its real corpus (`evaluation/real/2026-10-08-54f392d3.md`), but it got there by recognising phrasings:
- 19 linguistic readers and 5 precedence blocks;
- a turn arbiter;
- a 449 KB handler that grew by 30 % in slice 1.

An external audit concluded, and we agree, that it scales by accumulating knowledge of sentences instead of composing semantic operations. The phase-1 question is whether a model-led interpreter of typed operations reaches better results with **less interpretation-specific code**, and whether new capabilities can be added without multiplying exceptions.

## Decisions (the person's, binding)

1. **The model interprets.** It handles intent, corrections, references, comparisons, filter changes and continuity. No regex cascade or specialised reader decides first.
2. **The model emits typed, validated operations.** Deterministic code only does four things: it applies state transitions, checks invariants, authorises capabilities, and executes valid plans. It is not another linguistic interpreter.
3. **Existing contracts are reused.** FinancialPlan, ComparisonPlan, RatioPlan and the banking contracts are reused. The engine is not shaped by, and does not sit on top of, the existing handler.
4. **Money paths are untouched.** Payments, transfers, bookings and confirmations are not migrated in phase 1: the engine delegates them to the existing flow.
5. **The engine is behind a feature flag.** Production behaviour does not change: `ORCHESTRATOR_CONVERSATION_ENGINE=rules|model`, default `rules`.
6. **State is canonical.** The conversation state has one canonical representation, explicit transitions and a version (optimistic concurrency).

## Hard boundaries (enforced by a test)

The engine package must not import any of:
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

The person's sentence is passed to exactly one component, the interpreter port. Nothing deterministic reads it. `category_words` may be imported only to name categories in answers and to build the model's category glossary, never to read the sentence.

## Architecture

```
message + canonical state (server-side, versioned)
  → TurnInterpreter (LLM, one call per turn, closed JSON schema) → list[Operation]
  → Grounding (pure lookups: category catalogue, the holder's accounts and contacts by name; no sentence)
  → Transitions (pure: apply ops to the state; check invariants)
  → Authorisation + structural validation (domain __post_init__, MAX_* bounds, capability table, token scope)
  → Execution (FinancialQueryPort, AccountSummaryPort; comparison/ratio executed by the engine's own small executor)
  → Presentation (answer_writer / ratio_writer templates; no figure from a model; no answer without its figures)
  → new state (version + 1) saved with compare-and-set → response (same QuestionResponse wire shape)
```

### Canonical state: `DialogueState`
A domain dataclass, stored server-side and keyed by `conversation_id`. It holds:
- `version: int`.
- `focus`: one of `QueryFocus(plan)`, `ComparisonFocus(comparison, measure)`, `RatioFocus(ratio)`, `BalanceFocus(accounts)` or `None`. This is what is on screen.
- `pending`: an open question `(missing: Slot, draft: partial plan)`, or `None`.
- `scope`: the account selection, defaulting to all accounts.
- `turns`: the last N turns as `(said, what was answered, in words)`. These carry no figures.

The wire `ConversationPayload` gains two optional fields, `version` and `engine`. The client echoes `conversation_id` and `version`. A stale version gets **409** with the current state. The prototype store is in memory behind a `DialogueStorePort`, which is single-instance only and documented as such; a Postgres adapter in the orchestrator's `assistant` schema is the next step.

### Operations (closed schema; one turn may carry several)
| Operation | Fields |
|---|---|
| `ask` | a full plan, from the planner's field vocabulary plus counterparties, exclusions, accounts and top-N |
| `patch` | `target`: `focus`, `earlier`, `latest` or `pending`; `changes`: any subset of {subject, window, direction, measure, grouping, accounts, person}. Every field not named is kept. |
| `compare` | `shared` fields and two `sides` |
| `measure` | the focus's measure: amount, count, average, percent change or share |
| `select_operand` | `earlier` or `latest` |
| `answer_pending` | the slots the reply fills |
| `reset` | |
| `balance` | accounts named |
| `clarify` | `missing`, a closed slot enum; the question text is a template that names the subject |
| `smalltalk` | greeting, thanks, identity or help |
| `delegate` | payment, transfer, booking, chart or unsupported |

### Transitions and invariants (pure, unit-tested)
- A `patch` changes only the fields it names. A comparison patch applies to both sides, keeps the pair, keeps the calendar order and moves both sides by the same shift.
- `measure` keeps the pair or plan.
- `select_operand` turns a comparison into a single query on that side.
- `reset` clears focus and pending but keeps scope.
- An operation that cannot apply becomes a `clarify`. It never becomes a guess.

## Evaluation (frozen before the engine is written)

### Sets
- **Regression set**: a frozen copy of `evaluation/real/conversations.yaml` (every replay found so far) plus the UX battery. Both engines have "seen" these conversations, because the rules were tuned on them.
- **Generalization set**: generated once, before implementation, by a separate agent with no access to the engine code. It contains:
  - one paraphrased variant of every conversation, with the same goal and the same `expect.want`;
  - at least 20 new **combination** conversations that compose mechanisms: comparison → subject change → percentage → euros → one operand → reset; pending + correction + reference; and so on.

  It is committed before any engine code. Implementers are forbidden to read it, and nothing is tuned on it.

### Runs
The same seeded data, bank and judging rubric are used throughout.
1. rules engine, local model (qwen2.5-coder:7b, 4096 context): both sets;
2. model engine, local model: both sets;
3. model engine, hosted `openai/gpt-oss-120b`, when quota allows. The Groq free tier gives about one run a day.

Judging is blind wherever practical: runs are labelled A and B for the judge.

### Metrics
| Metric | How it is measured |
|---|---|
| Semantic accuracy | intent column |
| Context conservation | context column |
| Plan coherence | judge plus the engine's invariant violations |
| Bank results | figure_check match / mismatch / error |
| Recovery | after a failure, the next turn is right |
| Conversation success rate | every turn passes and the goal is met |
| Latency | per path / stage |
| Model calls per turn | counted by one engine-agnostic provider wrapper, reported in a response header |

Two more measurements:
- **Interpretation footprint**: lines of interpretation-specific code in each engine (script).
- **Capability addition**: add one new analytic capability to the model engine and record the diff it needed.

### Success criteria (stated before any run)
The model engine must, on the **generalization set**, with the same model:
- be at or above the rules engine on turn and conversation success;
- produce no figure mismatch;
- take no unsafe action;
- use a fraction of the interpretation-specific code.

The regression set is reported but is not the decision.

## Out of scope in phase 1
- Payments, transfers, bookings and confirmations (delegated).
- Chart planning and visual follow-ups (delegated or reported as a gap).
- Saved metrics.
- A2A gyms (delegated).
- Voice.
- A Postgres-backed dialogue store.

## Risks
- **The local 7B model may be too weak** for a typed-operations schema. That is a result, not a reason to add rules; it is measured with the hosted model as well.
- **Prompt size within 4096 tokens.** The glossary and state must be compact.
- **Free-tier quota limits hosted runs.**
- **Delegation seams.** A payment in the middle of an engine conversation must keep the engine's focus.
