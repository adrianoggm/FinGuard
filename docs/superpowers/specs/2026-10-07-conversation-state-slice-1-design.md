# Conversation state, slice 1 — arbitration first, a base after every answer

**Status:** proposed, 2026-10-07. **Branch:** `task/conversation-step2` (from `develop` 68b7fd9).
**Scope:** the first slice of "Conversation State v2 + Turn Arbitration", sized for the days left
before the panel. Everything listed under *Later* is out of this slice on purpose.

## 1. Why

A person trying FinGuard with ordinary, slightly unconventional conversation found it brittle and
robotic. The measurements say where the brittleness is — and where it is not:

- A battery of 10 long conversations (60 turns, real planner, simulated bank;
  `apps/orchestrator/scripts/evaluate_conversation_ux.py`, `evaluation/ux/`) fails **8 of 60
  turns** with `qwen2.5-coder:7b` at 4096 tokens of context.
- **Neither model size nor context fixes them:** 8192 tokens → 8; `qwen3.5:9b` → 9;
  `mistral-nemo:12b` → 9; `ministral-3:8b` → 11; turn interpreter router / hybrid / model →
  8 / 8 / 9. A prototype that shows the planner the last 4–6 turns and lets it write
  counterparties, exclusions and a top-N (`experiment/planner-context`) → still 8, also with the
  9B. Only a 2048-token context hurts (18–20 failures): the planner prompt is 2.7–3.0k tokens and
  is truncated.
- **The same turns fail with every model, because the decision is made before the model or
  overwritten after it.** Of the 8, two never reach a model (a rule decides first: «olvidalo» is
  read as a shop name, «y gasolina?»), and six reach it and are lost to the pipeline: the base of
  «¿cuánto le mandé a Marta?» does reach the planner, but no deterministic reader flips the
  direction and the model answers «¿De qué periodo?» («y lo que me mando ella?»); a comparison's
  base is dropped before the planner (`planning_context` passes a continuation only for a single
  query in focus), so «y en gasolina?» is planned from scratch;
  a payment order is not recognised as one («ahora quiero mandarle 20 euros a Marta» →
  `payment_router.is_payment` is False for the infinitive with a clitic, and for «pásale 20 a
  Marta»).
- Live, three more of the same family: a reply that fills a different slot than the one asked is
  not absorbed («en todas mis cuentas» to «¿De qué periodo?» → the BALANCE; «este año» to «¿En qué
  cuenta?» → asked again, period lost); after an answered question, «este año» to a new question's
  «¿De qué periodo?» completed the PREVIOUS plan (`contextual_follow_up_plan` claims it before
  `_finish_pending`).
- 21 of 24 ordinary banking questions first asked «¿En qué cuenta quieres consultarlo?».

The state itself mostly exists. `Conversation` (`domain/models/conversation.py`) already carries
`active` (the open workflow and its slot), `last_asked`, `last_plan`, `prior_plan`,
`last_comparison`, `last_ratio`/`pending_ratio`, `answered_the_balance`, `retry_plan`,
`result_reference`, `account_selection`, aliases, `recent_payment_rail`, a bounded `turns` log and
`visual_contexts`; `focus_of` (`domain/models/planning_context.py:83`) picks the strongest referent
in a fixed order; `TurnRelation` already names NEW_REQUEST / ANSWER_TO_SLOT / FOLLOW_UP /
CORRECTION / COMPOUND (`domain/models/turn_analysis.py:14`); `continuation_patch` already patches
the previous plan without a model. What is missing is **who decides first** and **who writes the
state, always**: today about 60 steps in `AskFinancialQuestionHandler._steps` each decide whether
to claim a turn and which fields to touch (the `turns` field's own comment: "the fields above it
kept getting wrong").

## 2. Goal of this slice

1. A turn is **arbitrated once, at the top**, into one of: RESET, CONFIRMATION, NEW_INTENT,
   ANSWER_PENDING, FOLLOW_UP (continue / modify), CORRECTION — before any reader can claim it. A
   pending question is one possibility, never a cage.
2. **Every answered turn commits a continuation base**, whichever path answered it, so a follow-up
   is a patch on what was understood instead of a fresh guess.
3. Analytics questions **do not ask for the account**: all accounts unless the person chose one.
4. Measured with the offline harness from the start, without editing its expectations to fit.

Success is decided by **real end-to-end conversations** (§4.1), not by the tests alone: the slice
passes when the real-conversation validation shows no wrong figure, no turn lost to a pending
question, recovery within one turn after a misunderstanding, and per-mechanism rates better than
the baseline measured on `develop` before any change. Below that gate: the 60-turn battery at
≥ 56/60 with the same expectations; the four diagnostic tests in §4.2 green; the three live
failures above answered correctly; every suite green; every model-facing prompt byte-identical
(the existing pins) — this slice changes no prompt.

## 3. Design

### 3.1 Turn arbitration (`domain/services/turn_arbiter.py`, new; called first in `_steps`)

One pure function over `(sentence, conversation, analysis, today)` returning a `TurnType` and, for
ANSWER_PENDING / FOLLOW_UP, the slots the sentence fills. Deterministic, reusing the readers that
exist; the turn interpreter's model reading (`analysis`) is evidence, not a verdict. Order, first
match wins:

| Order | Type | When | Reads with |
|---|---|---|---|
| 1 | RESET | «olvídalo», «déjalo», «no importa», «nada», «no era eso» with nothing after it | a closed lexicon (`confirmation.py` and `merchant_words` already hold several) |
| 2 | CONFIRMATION | a payment/booking confirmation is open and the sentence answers it | unchanged: the controller's existing confirmation gates stay first |
| 3 | NEW_INTENT (operation) | an order to move money (`payment_router.is_payment`, with the vocabulary fix in §3.6) or a balance question (`intent_router` balance rows) | existing routers |
| 4 | ANSWER_PENDING | something is open (`awaiting` or `last_asked`) **and** the sentence fills at least one slot the open question can hold | `window_words.window_in`, `account_words` / `_chosen_account_scope`, `category_words`, the merchant readers |
| 5 | CORRECTION | the existing correction readers («perdón junio», «no, quería decir julio») | unchanged |
| 6 | FOLLOW_UP | a continuation base exists and the sentence is elliptical (starts with «y», names only a slot, or a pronoun referent) | `continuation_patch`, the follow-up reader |
| 7 | NEW_INTENT (question) | everything else | the existing pipeline and planner |

Consequences, each a stated behaviour:
- A clear new intent **leaves the pending question** (it is dropped, not kept: keeping an abandoned
  draft is *Later*). «¿De qué periodo?» → «ahora quiero mandarle 20 euros a Marta» starts the
  payment preview.
  One exception, kept on purpose: the **account question survives** an aside and a balance
  (`31-account-scope.yaml` `the_account_question_survives_an_aside_and_a_balance`). Its reply is a
  chip naming one of the person's accounts and can mean nothing else, while a window reply
  («en julio») reads as a follow-up to whatever was answered last -- so only the window question
  is dropped.
- An answer is **absorbed whole**: every slot it fills goes into the open draft, and FinGuard asks
  only for what is still missing. «¿De qué periodo?» → «en todas mis cuentas» sets the account and
  asks the period again (naming the subject, §3.5); «¿En qué cuenta?» → «este año» keeps the window
  and asks the account again.
- A reply to an open question is never claimed by `contextual_follow_up_plan` or any reader that
  acts on the query in view: order 4 runs before them.
- RESET clears `active`, `last_asked`, `retry_plan` and the continuation base, and answers with a
  short acknowledgement. «no era eso, quería decir julio» is CORRECTION, not RESET.

The turn interpreter's prompt is not changed in this slice.

### 3.2 The open question holds several slots

The draft a financial question leaves open (`FinancialClarification(draft: FinancialPlanDraft,
awaiting)`) gains the **account** as a slot it can hold (the draft already has `only_account_id` /
`only_account_ids` / `across_accounts`; `financial_plan.py:214-216`), so window and account can be
filled in either order and in one reply. `AccountScopeClarification` (a complete plan waiting for
an account) keeps its role; an ANSWER_PENDING reply to it may also carry a window, which patches
the pending plan before it runs. No new wire field: the draft and the clarification already
travel, through the mapper, with their round-trip tests.

### 3.3 Post-turn commit — a base after every answer

The code map (2026-10-07) shows the base is already committed through shared helpers on every
answered path (`Conversation.answered`, `answered_comparison`, `answered_ratio`,
`answering_the_balance`, called from `_answered` and the comparison, ratio and balance paths).
This slice therefore does not rewrite them into one function; it **pins** the property with a
test -- every answered turn leaves a base `focus_of` can find -- and closes the one gap: a
comparison's base is dropped before any follow-up can use it (`planning_context.py:108-111`
accepts only a `QueryFocus`), so a comparison follow-up is handled on the comparison itself
(§3.4).

Required by name: the person-payment reader's answer («¿cuánto le mandé a Marta este mes?»)
already commits a plan with the counterparty, direction and window (AFQ `_person_payment_plan`);
what is missing is the FOLLOW_UP patch that flips its direction (§3.4). A comparison's answer
commits `last_comparison`, and `planning_context` must accept it as a continuation (today it only
accepts a `QueryFocus`), so «¿y en gasolina?» patches the shared category and keeps both windows.

### 3.4 Follow-ups are patches

`continuation_patch` (`domain/services/continuation_patch.py`) gains the patches this slice needs,
each applied to the committed base without the planner: direction flip («lo que me mandó / recibí /
me ha pagado ella»), grouping («mejor por categoría», «por comercio»), and the shared filter of a
comparison («y en gasolina?» on `last_comparison`). A patch the reader cannot build goes to the
planner with the base as its continuation (§3.3).

### 3.5 Defaults, and saying them

- **Account:** analytics questions use all of the holder's accounts unless the person chose one in
  the conversation (`account_selection`). A setting, `ORCHESTRATOR_ACCOUNT_SCOPE_DEFAULT=all|ask`
  (default `all`), keeps the old behaviour one variable away. Payments are unchanged (they name
  their own source account). «Consultado en todas tus cuentas.» is said only when the person has
  more than one account **and** asked nothing about accounts — not on every answer.
- **Windows:** «llevo gastado» / «llevo gastados» with no window → year to date; «últimamente» →
  the last 30 days. Both as `window_words` readings, and the answer states the window it used
  (it already does: «en lo que va de año», «en los últimos 30 días»). `evaluation/planning/dev.yaml`
  expects «últimamente» to ask; that expectation changes with a note that it is a product decision.
- **Questions name their subject:** «¿De qué periodo quieres ver tus gastos en restaurantes?»
  instead of «¿De qué periodo? Por ejemplo…», where the draft knows its subject. (Template change
  only; the model-written clarification is untouched.)

### 3.6 Payment vocabulary

`payment_router.is_payment` recognises the infinitive with a clitic after a desire or obligation
verb («quiero mandarle / enviarle / pasarle / hacerle un bizum de 20 €», «tengo que pagarle») and a
bare amount after an imperative with a clitic and a person («pásale 20 a Marta»). This also
closes the A2A residual R36 (ADR-012: «pásale 15 a Diego por el pádel de mañana» was claimed by the
agent path — a gym search instead of a payment preview): `expert_agent_words` already yields any
turn `is_payment` accepts. R36 has no test today; this slice adds it, and ADR-012's limit list
drops it.

## 4. Evaluation — real conversations decide, the offline judge runs from day one

### 4.1 Real end-to-end validation (the gate)

A FinGuard skill of our own, `finguard-real-conversation` (versioned in the repo under
`.claude/skills/`), runs whole conversations against the **running system as the demo runs it** —
compose `core` + `a2a`, the orchestrator with its configured model and mode, the banking database
loaded with the committed dataset up to yesterday, the real development token — and judges them:

- **Preflight:** every service healthy; `/version` names the commit under test; the dataset's
  newest movement is yesterday (if it is not, the skill stops and asks the person to run
  `just seed`; it never loads or truncates data itself); the GPU state and the model in memory are
  recorded, because latency depends on them.
- **Conversations, several per run:**
  - *Replayed* — the person's own conversations from 2026-10-06/07 (the gym and «últimamente», the
    subscriptions, Spotify across the account question, the long ones of `evaluation/ux/`), sent
    turn by turn as written, typos included.
  - *Improvised* — the skill's runner plays the holder with a goal («find out what my gym costs and
    then pay Marta 20 €»), writes the next message from the last answer, as a person would (short,
    hurried, changing their mind, correcting, abandoning), and stops when the goal is met or after
    eight turns. Each run keeps its transcript, so an improvised conversation that finds a failure
    becomes a replayed one.
  - At least two per run go through the **real web** (driven with Playwright's MCP), so what the
    person sees — the answer, «Ver la consulta», the evidence, the chart — is judged too; the rest
    through `POST /v1/questions`.
- **Judgement, per turn:**
  - *Figures, mechanically:* every amount, count and date in the answer is checked against a SQL
    query on the real ledger for the plan that ran (the read-only query role). A figure that
    differs is a failure, whatever else is right.
  - *Conversation, against a rubric:* right intent; context carried (window, subject, account,
    comparison); a pending question resolved or correctly abandoned; a clarification only when it
    was needed, and useful when asked; recovery within one turn; naturalness of the wording (1–5,
    with the reason).
  - Mechanism tags as in §4.3, so a failure names the mechanism that broke.
- **Output:** a dated report per run (`evaluation/real/<date>-<sha>.md` + `.json`): transcripts,
  per-turn verdicts with evidence, the per-mechanism table, latency per turn, and the comparison
  with the previous run. The baseline is the run on `develop` before this slice starts.

### 4.2 Diagnostic tests

- **Diagnostic tests** (red first, one mechanism each), over HTTP on the episodes stack:
  1. `pending_interrupted_by_new_intent`: «¿cuánto gasté en restaurantes?» → «¿De qué periodo…?» →
     «mándale 20 € a Marta» ⇒ payment preview, amount 20, recipient Marta.
  2. `comparison_followup`: «junio vs julio en restaurantes» → «¿y en gasolina?» ⇒ two queries,
     June and July, category fuel.
  3. `rule_answer_sets_continuation`: «¿cuánto le mandé a Marta este año?» → «¿y ella a mí?» ⇒
     counterparty Marta, this year, direction income.
  4. `reset_pending`: «¿cuánto gasté?» → «¿De qué periodo?» → «olvídalo» → «¿cuánto tengo?» ⇒
     balance, no inherited window.
  Plus the three live failures of §1 and «ahora quiero mandarle 20 euros a Marta» after a query.
### 4.3 The battery and the corpus

- **The battery, broken down by mechanism:** each turn in `evaluation/ux/conversations.yaml`
  gains a `mechanism:` tag (intent, slot carry-over, pending resolution, interruption, follow-up,
  comparison persistence, reference, reset, unnecessary clarification, execution); the script
  reports pass rates per tag and a *recovery rate* (after a failed turn, is the next correct?). The
  expectations themselves are not edited to make a turn pass.
- **Every change** reruns: the battery with the real model, the conversation corpus, the 24-question
  probe and the full suites; **every merged piece** reruns the real-conversation validation (§4.1). The prototype switches (`ORCHESTRATOR_PLANNER_HISTORY_TURNS`,
  `ORCHESTRATOR_PLANNER_RICH_PLAN`) are re-measured once §3.3 lands: with a base committed, history
  may start to help.

## 5. Risks

- **Account default.** The corpus (`31-account-scope.yaml` and others) and unit tests expect the
  account question; they change with the default, deliberately, and the old behaviour stays one
  setting away. Balance answers are unaffected (they already list every account).
- **Ordering change in `_steps`.** Moving arbitration to the top changes which path answers some
  turns that work today. Mitigation: arbitration only claims a turn for types 1, 3, 4 and its
  order-6 follow-ups when it is sure; otherwise it returns NEW_INTENT and the pipeline runs as
  today. The full corpus (210 episodes) is the regression net.
- **The validation needs the system up as the demo runs it.** The compose stack, the model in GPU
  memory and the dataset loaded up to yesterday are preconditions the skill checks and does not
  create: loading the dataset truncates the demo's bank data and stays the person's step. The
  skill and its baseline run come first, before any change, so there is a "before" to compare.
- **An improvised conversation is not reproducible by itself.** Its transcript is; a failure found
  that way is replayed in every later run, and the verdicts that depend on judgement (the rubric)
  are reported apart from the mechanical ones (figures), which are the gate.
- **Time.** If the slice does not fit, the order of delivery is: §3.6 (payment vocabulary) → §3.1
  RESET + NEW_INTENT breaking the pending question → §3.3 commit → §3.2 multi-slot → §3.4 patches →
  §3.5 defaults. Each is mergeable on its own.

## 6. Later (not in this slice)

Rules as evidence with confidence everywhere (only the arbitration subset now); keeping an
abandoned draft to return to; a typo-normalisation pass before the readers («semna», «pasao»);
natural templates (dates in words, no coda) and the narrator in shadow mode with a fact verifier;
the runtime judge; the prototype's history and rich plan in production.
