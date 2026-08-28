# Conversational drift and wrong figures — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the assistant answering one question with the figure for another, and stop the account-scope requirement crashing the service.

**Architecture:** Two surgical fixes plus a diagnosis pass. The first fix widens one recogniser's
vocabulary so a self-contained question stops being read as an ellipsis — the planner already
answers these correctly, it just never sees them. The second moves one exception's translation from
the two call sites that remember it to the one boundary that cannot forget. Neither widens a
contract; neither touches `PlanPatch`, `TopMovements` or the wire.

**Tech Stack:** Python 3.13 · pytest · pytest-asyncio · ruff · mypy. No new dependencies.

**Spec:** this document. The findings it fixes were produced by manual validation on 2026-08-28
against `39eee4b` with the compose stack up; every probe and its output is reproduced inline in the
task that fixes it. The product decisions it must not violate are in
[docs/architecture/adr-011-derived-charts-and-saved-metrics.md](../../architecture/adr-011-derived-charts-and-saved-metrics.md)
and [docs/conversational-capabilities.md](../../conversational-capabilities.md).

## Global Constraints

- Code, comments, tests and documentation in English. Data — merchant names, categories, corpus
  sentences, user-facing Spanish copy — stays in Spanish.
- No new dependency, and no change to `packages/contracts/` or to any HTTP response shape.
- `PlanPatch`, `TopMovements` and `FinancialPlan` keep their current fields. An earlier reading of
  this bug suggested adding `rank` to `PlanPatch`; Task 1 makes that unnecessary and it is
  explicitly out of scope.
- Every task ends green on: `uv run --directory apps/orchestrator pytest -q -m "not integration"`,
  `ruff check .`, `ruff format --check .`, `mypy src`.
- One commit per task, conventional prefix, no `Co-Authored-By` trailer.
- Scope of this plan: the conversation and the figures. The chart defects (sort order, English
  identifiers, line-over-categories, schema version, 8px overflow) and the UI defects (dead tab bar,
  overdraft preview, credit meter, silent 50-row cut, chips without `aria-pressed`) belong to a
  second plan. Two decisions already taken for that plan, recorded here so they are not re-litigated:
  the category labels are fixed **in the client, gated on `group_by`**, using the table that already
  exists in `apps/web/src/format.ts`; and `derive_chart` gains `sort_field` rather than the presenter
  guessing.

---

## File Structure

| File | Responsibility | Change |
| --- | --- | --- |
| `apps/orchestrator/src/finguard_orchestrator/domain/services/capability_guard.py` | The words that name a movement, in one place | Extract `MOVEMENT_NOUNS` from the existing `_A_SUPERLATIVE_NOUN`, which spells the list twice |
| `apps/orchestrator/src/finguard_orchestrator/application/queries/ask_financial_question.py` | Whether a turn is a new question or an ellipsis; where a scope requirement becomes a question | Widen `_SELF_CONTAINED_FINANCIAL_QUESTION` (line 262); translate `AccountScopeRequiredError` once at the boundary |
| `apps/orchestrator/tests/test_ranking_pipeline.py` | The ranking path, first turn and follow-up | Add the mirror of `test_a_sentence_with_its_own_content_is_not_a_continuation` |
| `apps/orchestrator/tests/test_account_scope_workflow.py` | The account-scope clarification | Add the property that no read path can raise past the boundary |
| `apps/orchestrator/evaluation/conversations/01-ellipsis.yaml` | Corpus: what an ellipsis is and is not | Add the shapes a person uses and the corpus never had |

---

## Task 1: A ranking that names its own window is a new question, not an ellipsis

**The defect.** `_starts_new_financial_scope` decides whether a turn stands on its own. It requires a
window *and* a match on `_SELF_CONTAINED_FINANCIAL_QUESTION`. That pattern lists conjugated verbs
(`gaste`, `gastado`, `pague`…) and plural nouns (`movimientos`, `operaciones`) but **not the singular
nouns a superlative uses** — `gasto`, `movimiento`, `compra`, `pago`, `cargo`. Without a match the
continuation is kept, the closed-vocabulary patch at line 1482 grounds the window and short-circuits
the planner (`planned = PlanningResult.planned(grounded_plan)`), and the superlative is never read.

Measured, three turns, `demo-token-laura-pays`:

```
'cuánto gasté en julio' / 'todas mis cuentas'
   -> 1.895,97 € del 2026-07-01 al 2026-07-31, en 28 movimientos.
'cuál fue mi mayor gasto de julio'
   -> 1.895,97 € del 2026-07-01 al 2026-07-31, en 28 movimientos.   <-- the previous total
'cuál fue mi mayor gasto de junio'
   -> 2.081,40 € del 2026-06-01 al 2026-06-30, en 28 movimientos.   <-- June's total; the window
                                                                        was applied, the rank dropped
```

The discriminator is the noun and nothing else. Same shape, plural noun, same conversation:

```
'cuáles fueron mis movimientos más altos de junio'
   -> Tu mayor gasto en junio de 2026 fue de 2.465,62 €: Innovasur SL, el 28 de junio.   <-- correct
```

**Why widening this pattern is safe.** `_starts_new_financial_scope` returns False unless the
sentence *also* carries a window, and returns False early for `esa|ese|esas|esos|lo mismo`. So
«y qué gasto?» stays an ellipsis (no window) and «ese gasto de julio» stays an ellipsis (demonstrative).
Only a sentence that names both a movement noun and a window changes behaviour — which is the
definition of standing on its own.

**Files:**
- Modify: `apps/orchestrator/src/finguard_orchestrator/domain/services/capability_guard.py:237-244`
- Modify: `apps/orchestrator/src/finguard_orchestrator/application/queries/ask_financial_question.py:262-268`
- Test: `apps/orchestrator/tests/test_ranking_pipeline.py`
- Corpus: `apps/orchestrator/evaluation/conversations/01-ellipsis.yaml`

**Interfaces:**
- Produces: `capability_guard.MOVEMENT_NOUNS: Final[str]` — a regex alternation fragment, no
  capturing groups, no anchors, no `\b`. Consumed by `_A_SUPERLATIVE_NOUN` in the same module and by
  `_SELF_CONTAINED_FINANCIAL_QUESTION` in `ask_financial_question`.
- Consumes: nothing from other tasks.

- [ ] **Step 1: Write the failing test**

Add to `apps/orchestrator/tests/test_ranking_pipeline.py`, directly after
`test_a_sentence_with_its_own_content_is_not_a_continuation`. That test asserts an aggregate question
after a ranking goes to the planner; this is its mirror, and the mirror is what was missing.

```python
async def test_a_superlative_that_names_its_window_is_not_a_continuation() -> None:
    """The mirror of the test above, and the one that was missing.

    "¿Cuál fue mi mayor gasto de junio?" after a total names its own subject and its own window, so
    it is a new question. Read as an ellipsis it became a *window patch* on the previous aggregate —
    the month was applied, the superlative was dropped, and the answer was June's total presented as
    June's largest expense. A real figure about a different question, which is the failure this whole
    path exists to avoid.

    The singular noun is the point. "mis movimientos más altos de junio" already worked, because
    `movimientos` was in the self-contained vocabulary and `gasto` was not.
    """
    aggregate = FinancialPlan(direction=Direction.EXPENSE, period=THIS_YEAR)
    planner = FakePlanner(PlanningResult.planned(aggregate))
    ledger = FakeLedger(CEPSA)

    await ask(
        "¿cuál fue mi mayor gasto de junio?",
        planner,
        ledger,
        conversation=Conversation(last_plan=aggregate),
    )

    assert planner.asked == "¿cuál fue mi mayor gasto de junio?", (
        "it has to reach the model: the patch cannot express a rank, so grounding the window "
        "silently discards the question"
    )


async def test_an_ellipsis_without_a_window_is_still_an_ellipsis() -> None:
    """The guard on the widening. A movement noun alone does not make a turn self-contained —
    `_starts_new_financial_scope` also requires a window — so "¿y el gasto?" stays a follow-up and
    keeps whatever the conversation was about."""
    previous = FinancialPlan(direction=Direction.EXPENSE, period=THIS_YEAR)
    planner = FakePlanner(PlanningResult.planned(previous))
    ledger = FakeLedger(CEPSA)

    await ask("¿y el gasto?", planner, ledger, conversation=Conversation(last_plan=previous))

    assert planner.asked == "¿y el gasto?"
```

- [ ] **Step 2: Run the tests to verify the first fails and the second passes**

```bash
uv run --directory apps/orchestrator pytest \
  tests/test_ranking_pipeline.py::test_a_superlative_that_names_its_window_is_not_a_continuation \
  tests/test_ranking_pipeline.py::test_an_ellipsis_without_a_window_is_still_an_ellipsis -v
```

Expected: the first FAILS with `AssertionError: it has to reach the model` (`planner.asked` is
`None`, because the patch shortcut answered without calling the planner). The second PASSES already —
it is there to fail if the widening goes too far.

- [ ] **Step 3: Extract the noun list in `capability_guard.py`**

`_A_SUPERLATIVE_NOUN` spells the noun list twice today. Name it once, above the pattern, and use it
in both halves:

```python
# The words for a movement, as somebody names one. Exported because two recognisers need the same
# list and a second copy is a second thing to forget: this one decides that a superlative is a
# ranking rather than a comparison, and `_SELF_CONTAINED_FINANCIAL_QUESTION` in the application uses
# it to decide that a sentence naming one of these plus a window is a question and not an ellipsis.
#
# A fragment rather than a compiled pattern: no group, no anchor, no `\b`, so each caller keeps its
# own boundaries and its own surrounding context.
MOVEMENT_NOUNS: Final = (
    "gasto|compra|pago|movimiento|cargo|importe|factura"
    "|bizum|transferencia|ingreso|cobro|recibo"
)

_A_SUPERLATIVE_NOUN: Final = re.compile(
    r"\b(mayor|maxim[oa])\s+"
    r"(gasto|compra|pago|movimiento|cargo|importe|cantidad|bizum|transferencia|ingreso|cobro|recibo)"
    rf"|\b({MOVEMENT_NOUNS})s?\s+"
    r"(mas|de\s+mayor)\s+(alt[oa]|car[oa]|grande|elevad[oa]|importe)",
    re.IGNORECASE,
)
```

Leave the first alternative's list literal: it includes `cantidad` and excludes `factura`, so it is
not the same list, and quietly making it the same list would change what a superlative is.

- [ ] **Step 4: Widen `_SELF_CONTAINED_FINANCIAL_QUESTION`**

In `apps/orchestrator/src/finguard_orchestrator/application/queries/ask_financial_question.py`, add
the import and rewrite the pattern at line 262:

```python
from finguard_orchestrator.domain.services.capability_guard import MOVEMENT_NOUNS
```

```python
# What makes a turn stand on its own, together with a window (see `_starts_new_financial_scope`).
#
# The noun alternation is shared with the superlative recogniser rather than spelled again. It used
# to hold only conjugated verbs and plural nouns, so "¿cuál fue mi mayor gasto de junio?" matched
# nothing here, stayed an ellipsis, and became a *window patch* on the previous aggregate: the month
# was applied and the superlative was dropped. "mis movimientos más altos de junio" worked, because
# `movimientos` happened to be in the list. One word was the whole difference between a ranking and
# somebody else's total.
_SELF_CONTAINED_FINANCIAL_QUESTION: Final = re.compile(
    r"\b(?:cuanto|cuantos|cual|cuales|que)\b.{0,70}"
    r"\b(?:gaste|gastado|pague|pagado|compre|comprado|ingrese|ingresado|cobre|cobrado"
    rf"|movimientos|operaciones|categoria|{MOVEMENT_NOUNS})\b"
    r"|\b(?:muestrame|ensename|quiero\s+ver)\b.{0,55}"
    r"\b(?:movimientos|operaciones|gastos|ingresos)\b"
)
```

- [ ] **Step 5: Run the two tests, then the whole suite**

```bash
uv run --directory apps/orchestrator pytest \
  tests/test_ranking_pipeline.py::test_a_superlative_that_names_its_window_is_not_a_continuation \
  tests/test_ranking_pipeline.py::test_an_ellipsis_without_a_window_is_still_an_ellipsis -v
uv run --directory apps/orchestrator pytest -q -m "not integration"
```

Expected: both PASS, then `2899 passed, 4 skipped` or more.

The widening makes more turns "self-contained", and a self-contained turn **drops the inherited
category**. If a corpus episode regresses, read it before touching the regex: the corpus turn may be
asserting the old behaviour, in which case the episode is the thing to fix and its `why` needs the
reason written down. Do not narrow the noun list to make a red test green without recording why.

- [ ] **Step 6: Check which cascaded symptoms this fix reaches**

Four of the fifteen findings were attributed to this cause without being separately diagnosed: the
conversation getting stuck in `awaiting=WINDOW` for four turns with a perfectly good absolute window
in the plan, `¿y el segundo?` answering «No he entendido qué quieres consultar», a COUNT follow-up
coming back `INVALID_DOMAIN`, and the escape from the stuck state requiring a strong category word
rather than the clarification flow. Rebuild and replay the conversation that produced them:

```bash
docker compose build orchestrator && docker compose up -d orchestrator
```

Then send these turns in order, echoing `conversation` back each time (the probe in Task 3 Step 2 is
the harness — change the list):

```
hola · cuánto dinero tengo · y en la hucha? · cuánto gasté el mes pasado · todas mis cuentas ·
en qué me lo gasté · y en junio? · compáralo con julio · cuánto gasté en supermercado en julio ·
sin contar el alquiler · no, me refería a julio · cuál fue mi mayor gasto de julio · y el segundo? ·
cuántas veces fui al supermercado en julio · qué día suelo gastar más · tengo suscripciones? ·
muéstrame el gasto por categoría de julio en un gráfico · ponlo en barras ·
hazle un bizum de 5 euros a Marta Solis · no
```

Record which of the four still fail. Before the fix, turns 12-15 all returned the same stale figure
or a refusal with the plan unchanged. Whatever still fails after it has a cause of its own and
belongs in Task 3 as a seventh step — do not assume this fix reached it, and do not assume it did
not.

`compáralo con julio` (turn 8) and `qué día suelo gastar más` (turn 15) are expected to still fail:
they are Task 3 Steps 5 and 3.

- [ ] **Step 7: Add the corpus episode**

Append to `apps/orchestrator/evaluation/conversations/01-ellipsis.yaml`. The corpus has superlative
follow-ups — `the_ordinal_walk`, and corpus 14 — but every one of them is a **bare** superlative, the
shape that already worked. This is the shape that did not.

```yaml
  - name: a_superlative_that_names_its_month
    why: >
      Every superlative-as-follow-up in this corpus is bare — "Cual ha sido el gasto mas alto?",
      "¿y el segundo?" — and those go through the deterministic ordinal path and always worked. A
      person naming the month does not: "¿cuál fue mi mayor gasto de junio?" carried a window, so
      the closed-vocabulary patch grounded the window, short-circuited the planner and answered
      with the previous aggregate's total. One noun missing from one regex, and 1.125 turns of
      corpus could not see it.
    metrics: [context_retention, wrong_tool]
    turns:
      - say: ¿cuánto gasté en julio?
        route: QUERY
      - say: ¿cuál fue mi mayor gasto de junio?
        route: QUERY
        ran: {rank: 1, window: {start: 2026-06-01, end: 2026-06-30}, direction: EXPENSE, categories: [], excluded_categories: [], excluded_merchants: []}
      - say: ¿y el segundo?
        route: QUERY
        ran: {rank: 2, window: {start: 2026-06-01, end: 2026-06-30}, direction: EXPENSE, categories: [], excluded_categories: [], excluded_merchants: []}
```

Check the `ran:` window syntax against a neighbouring episode that asserts an absolute range before
committing — `grep -n "ran:.*start" apps/orchestrator/evaluation/conversations/*.yaml`. The corpus
validates its own coherence in the unit suite, so a malformed assertion fails there rather than
silently passing.

- [ ] **Step 8: Run the corpus coherence check and the formatters**

```bash
uv run --directory apps/orchestrator pytest tests/test_conversation_corpus.py -q
uv run --directory apps/orchestrator ruff check . && uv run --directory apps/orchestrator ruff format --check .
uv run --directory apps/orchestrator mypy src
```

- [ ] **Step 9: Commit**

```bash
git add apps/orchestrator/src/finguard_orchestrator/domain/services/capability_guard.py \
        apps/orchestrator/src/finguard_orchestrator/application/queries/ask_financial_question.py \
        apps/orchestrator/tests/test_ranking_pipeline.py \
        apps/orchestrator/evaluation/conversations/01-ellipsis.yaml
git commit -m "fix(orchestrator): read a superlative that names its own window"
```

---

## Task 2: An account scope requirement can never become a 500

**The defect.** `_scoped` raises `AccountScopeRequiredError` when a holder has several accounts and
the sentence named none. Two call sites translate it into the "¿En qué cuenta quieres consultarlo?"
clarification — line 2331 and line 4008. **Nine do not:** lines 2633, 2678, 2872, 2933, 3020, 3021,
3232, 3251, 3259, 3382, 3389 — the comparison pair, the ratio operands, net cash flow and the salary
history. Every one of those is a 500 as a first question for any holder with more than one account.

Measured:

```
POST /v1/questions  {"question": "compara mis gastos de julio con junio"}
   -> HTTP 500

orchestrator | AccountScopeRequiredError: (FinancialPlan(direction=EXPENSE, ...
             |   date_range=AbsoluteRange(2026-06-01, 2026-06-30), ... across_accounts=False ...),
             |   (AccountCandidate('Cuenta corriente'), AccountCandidate('Hucha'),
             |    AccountCandidate('Fondo indexado')))
```

The same sentence works once the scope is settled by a previous turn, which is why no test saw it:

```
'cuánto gasté en julio' / 'todas mis cuentas' / 'compara mis gastos de julio con junio'
   -> En julio de 2026: 1.895,97 €; en junio de 2026: 2.081,40 €. El gasto fue 185,43 € mayor en junio.
```

**The fix is one handler, not nine `try`/`except`.** Nine call sites that must each remember a
translation is nine chances to forget, and the tenth path added next month will forget too. The
translation moves to the boundary the flow already funnels through, and the two existing catches
stay where they are: line 2331 has its own wording for changing an existing scope, and line 4008
sets `AccountScopeClarification` as the active workflow so the answer can be resumed. The boundary
handler is the backstop for everything else, and it produces the same clarification as line 4008
without the resumable state — a path that never registered one cannot resume it.

**Files:**
- Modify: `apps/orchestrator/src/finguard_orchestrator/application/queries/ask_financial_question.py`
- Test: `apps/orchestrator/tests/test_account_scope_workflow.py`

**Interfaces:**
- Consumes: `AccountScopeRequiredError` (line 138) with its `.plan` and `.candidates`, unchanged.
- Produces: nothing other tasks depend on.

- [ ] **Step 1: Find the boundary and read it**

```bash
grep -n "async def stream\|async def handle\|async def __call__" \
  apps/orchestrator/src/finguard_orchestrator/application/queries/ask_financial_question.py
```

The handler's public entry point is a generator that yields `Stage` and finally a result. Read it
before writing the wrapper: the translation has to happen where a result can still be yielded, and
it must not swallow the exception on the two paths that already handle it — those return before it
propagates, so wrapping the outermost body is enough.

- [ ] **Step 2: Write the failing test**

Add to `apps/orchestrator/tests/test_account_scope_workflow.py`. Reuse the file's own fixtures:
`service(ledger)` builds the handler with `Directory()` returning `HOLDINGS`, and `ask(handler,
sentence)` sends one turn from `IDLE`. That directory is already the multi-account precondition — the
existing tests assert `suggestions == ["Cuenta corriente", "Hucha", "Todas mis cuentas"]`.

**The fake `Planner` needs no change, and here is why.** `PlannedQuery` is
`FinancialPlan | TopMovements` — a comparison is *not* something the planner returns. It is built by a
deterministic reader of the sentence, which is why these sentences reach the comparison path even
though the fake planner always answers with the same aggregate. Confirm that at Step 3: if a case
passes trivially instead of raising, the path was not entered and the sentence needs replacing with
one the deterministic reader recognises (`grep -n "frente a\|compara" apps/orchestrator/src/finguard_orchestrator/domain/services/*.py`).

The test asserts the property rather than one sentence, because the defect is a class of paths:

```python
@pytest.mark.parametrize(
    "sentence",
    [
        # The comparison pair: two plans, two `_scoped` calls, no catch (lines 3020-3021).
        "compara mis gastos de julio con junio",
        # A ratio's two operands (lines 3232, 3251, 3259).
        "qué porcentaje de mi nómina se fue en alquiler este año",
        # Net cash flow (lines 2872, 2933).
        "cuánto me ha entrado y cuánto me ha salido este año",
    ],
)
async def test_no_read_path_turns_a_scope_requirement_into_a_crash(sentence: str) -> None:
    """A holder with several accounts and a sentence that names none is a question, never an error.

    `_scoped` raises `AccountScopeRequiredError` and exactly two of its eleven call sites translated
    it. The comparison pair, the ratio operands and net cash flow did not, so each of these was an
    HTTP 500 as a first question -- and invisible to every test, because the same sentence works once
    a previous turn has settled the scope.
    """
    ledger = Ledger()
    handler = service(ledger)

    answer = await ask(handler, sentence)

    assert answer.outcome == PlanningOutcome.NEEDS_CLARIFICATION.value
    assert "cuenta" in answer.text.lower()
    assert "Todas mis cuentas" in answer.suggestions
    assert ledger.plans == [], "no figure is read before the holder has said whose money it is"
```

Decorate it with `@pytest.mark.asyncio` like every other test in the file.

`ask_with_three_accounts` is whatever the file's existing helper is called — reuse it rather than
adding a second harness. If there is no such helper, extract one from the existing test in the same
file and use it in both, in this commit.

- [ ] **Step 3: Run the test to verify it fails**

```bash
uv run --directory apps/orchestrator pytest \
  tests/test_account_scope_workflow.py::test_no_read_path_turns_a_scope_requirement_into_a_crash -v
```

Expected: three FAILs, each raising `AccountScopeRequiredError` out of the handler rather than
returning a result.

- [ ] **Step 4: Translate the exception at the boundary**

In the handler's outermost body, wrap the flow so the exception becomes the clarification. Write it
as a small named method beside `_answer` so the wording lives in one place:

```python
    def _scope_question(self, required: AccountScopeRequiredError) -> AskFinancialQuestionResult:
        """The clarification a scope requirement is, wherever it was raised.

        `_answer` and the scope-change path translate this themselves, because each has something to
        add -- a resumable `AccountScopeClarification`, and its own wording for changing a scope
        already settled. This is the backstop for the other nine call sites, which had nothing and
        therefore produced a 500: a comparison, a ratio or a net-cash-flow question asked first, by
        anybody with more than one account.

        No `AccountScopeClarification` here on purpose. A path that never registered the pending plan
        cannot resume it, and offering to resume something that is not stored is worse than asking
        the whole question again.
        """
        names = [candidate.display_name for candidate in required.candidates]
        return AskFinancialQuestionResult(
            outcome=PlanningOutcome.NEEDS_CLARIFICATION.value,
            text="¿En qué cuenta quieres consultarlo? Puedes elegir "
            + ", ".join(names)
            + " o Todas mis cuentas.",
            question="¿En qué cuenta quieres consultarlo?",
            suggestions=[*names, "Todas mis cuentas"],
        )
```

Then wrap the generator body:

```python
        try:
            ...existing body...
        except AccountScopeRequiredError as required:
            yield self._scope_question(required)
            return
```

Keep the `conversation=` field as the boundary sees it, matching what the surrounding code passes on
its other early returns — check the neighbouring `yield _decline(...)` calls for the convention
rather than inventing one.

- [ ] **Step 5: Run the test, then the whole suite**

```bash
uv run --directory apps/orchestrator pytest \
  tests/test_account_scope_workflow.py::test_no_read_path_turns_a_scope_requirement_into_a_crash -v
uv run --directory apps/orchestrator pytest -q -m "not integration"
```

Expected: three PASS, suite green.

- [ ] **Step 6: Verify against the running service**

The unit test uses a fake directory; the 500 was found over HTTP, so confirm the fix where it was
found. With the compose stack up and rebuilt:

```bash
docker compose build orchestrator && docker compose up -d orchestrator
curl -s -o /dev/null -w "%{http_code}\n" -X POST http://127.0.0.1:8000/v1/questions \
  -H "Authorization: Bearer demo-token-laura-pays" -H "Content-Type: application/json" \
  -d '{"question":"compara mis gastos de julio con junio"}'
```

Expected: `200`, and the body's `outcome` is `NEEDS_CLARIFICATION`. Before the fix this printed `500`.

- [ ] **Step 7: Commit**

```bash
git add apps/orchestrator/src/finguard_orchestrator/application/queries/ask_financial_question.py \
        apps/orchestrator/tests/test_account_scope_workflow.py
git commit -m "fix(orchestrator): answer a scope requirement instead of raising it"
```

---

## Task 3: Find the cause of the six remaining defects

The two tasks above are fixes because their causes are known — a regex and an uncaught exception,
both read in the source. The six below are **symptoms measured over HTTP with no cause yet found.**
Writing TDD steps for them now would mean inventing a fix for a bug nobody has located, which is how
a plan becomes fiction. This task's deliverable is six root causes appended to this document, each
with a file and a line, after which their fix tasks get written the way Tasks 1 and 2 are written.

Work them in this order — it is descending severity, and the first two are the ones that produce a
wrong figure rather than a wrong shape.

- [ ] **Step 1: «en todas mis cuentas» hijacks `group_by`**

```bash
curl -s -X POST http://127.0.0.1:8000/v1/questions \
  -H "Authorization: Bearer demo-token-laura-pays" -H "Content-Type: application/json" \
  -d '{"question":"cuál fue mi mayor gasto de julio en todas mis cuentas"}' | python -m json.tool
```

Observed: `PLANNED`, `group_by=ACCOUNT`, `group_limit=5`, text «La cuenta con mayor importe total …
es:». The superlative became a ranking **of accounts**. Note this happens on the *first* turn, so it
is independent of Task 1. Start at `explicit_facts.read` / `explicit_facts.enforce` (called at
`ask_financial_question.py:1523`) and at the account-scope vocabulary — something reads "todas mis
cuentas" as a grouping instruction rather than a scope one. Record which.

- [ ] **Step 2: `across_accounts` narrows silently while `selection_mode` stays `ALL`**

Save this as `probe.py` outside the repository and run it against the compose stack:

```python
import json, urllib.request
BASE, TOKEN = "http://127.0.0.1:8000", "demo-token-laura-pays"
def ask(q, c=None):
    b = {"question": q} | ({"conversation": c} if c is not None else {})
    r = urllib.request.Request(BASE + "/v1/questions", data=json.dumps(b).encode(), method="POST")
    r.add_header("Authorization", "Bearer " + TOKEN)
    r.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(r) as x:
        return json.loads(x.read().decode())
c = None
for q in ["cuánto gasté en julio", "todas mis cuentas", "y en junio?",
          "cuánto gasté en supermercado en julio", "y en restaurantes?"]:
    a = ask(q, c); c = a["conversation"]; p = c.get("last_plan") or {}
    print(f"{q!r:45} across={p.get('across_accounts')} "
          f"selection_mode={c.get('account_selection_mode')}")
```

Observed:

```
'todas mis cuentas'                      across=True   selection_mode=ALL
'y en junio?'                            across=True   selection_mode=ALL
'cuánto gasté en supermercado en julio'  across=False  selection_mode=ALL   <-- narrows
'y en restaurantes?'                     across=False  selection_mode=ALL
```

Two halves of one fact disagree, and the answer stops saying «Consultado en todas tus cuentas», so
nobody can notice. Today the figures match because that spending is all in the current account; on a
holder with money spread across accounts this under-reports. Find whether the narrowing is in
`_scoped` (line 3686) or in the planner's plan for the new sentence, and record which.

- [ ] **Step 3: «qué día suelo gastar más» answers with an average**

```bash
curl -s -X POST http://127.0.0.1:8000/v1/questions ... \
  -d '{"question":"qué día suelo gastar más"}'      # then answer «en julio», then «todas mis cuentas»
```

Observed: `metric=AVERAGE group_by=DAY`, text «El importe medio por movimiento … fue de 67,71 €».
The grouping is right and the metric is wrong — it should be `Metric.FREQUENCY`. The capability
matrix marks this Active with `test_frequency.py` and corpus 20, so start by running those and
finding what they assert that this sentence does not satisfy.

- [ ] **Step 4: «compáralos» after two answered periods is refused**

```
'cuánto gasté en julio' / 'todas mis cuentas' / 'cuánto gasté en junio' / 'compáralos'
   -> UNSUPPORTED | «no he podido delimitar dos periodos calendario completos. Prueba … o consulta
                     dos periodos por separado y dime "compáralos".»
```

The refusal recommends the exact form that produced it. Both operands are in the conversation
(`last_plan` and `prior_plan` are both populated — check). Find what is supposed to build a
`ComparisonPlan` from those two and why it does not fire, and record it.

- [ ] **Step 5: «compáralo con junio» is refused where «con el mes anterior» works**

```
'compáralo con el mes anterior'  -> ✅ «El mes pasado: 1.895,97 €; en junio de 2026: 2.081,40 € …»
'compáralo con junio'            -> ❌ UNSUPPORTED
```

A relative period word is accepted as the second operand and a month name is not. Find the parser
that reads the second operand and record which vocabulary it is missing.

- [ ] **Step 6: An inert exclusion is presented as applied**

```
'cuánto gasté en supermercado en julio'  -> 449,62 € en supermercado …, en 8 movimientos
'sin contar el alquiler'                 -> 449,62 € en supermercado, sin alquiler …, en 8 movimientos
```

Excluding `HOUSING_RENT` from a query filtered to `GROCERIES` cannot change anything, and the
sentence declares the filter as though it had. `PlanPatch.apply_to` already drops an excluded
category from `categories`; the missing rule is that an exclusion which intersects nothing is not a
change worth claiming. Decide with the reader whether the answer should say so or the patch should
be refused, and record the decision — this one is a product call, not only a bug.

- [ ] **Step 7: Append the six causes to this document and commit**

For each: the file, the line, one sentence of mechanism, and whether the fix is a bug fix or a
product decision. Then write Tasks 4-9 in the shape of Tasks 1 and 2 — failing test first, exact
code, corpus case where a sentence shape is involved.

```bash
git add docs/superpowers/plans/2026-08-28-conversational-drift.md
git commit -m "docs(plan): record the causes of the remaining conversational defects"
```

---

## Out of scope, recorded so it is not lost

**Second plan — the chart contradicting its own answer.** `derive_chart` sets `sort="descending"`
with no `sort_field`, so Vega-Lite sorts the nominal axis alphabetically while the sentence above it
lists by value. The chart and the "Ver los datos" table print `HOUSING_RENT` where the sentence says
«alquiler». «Ponlo en línea» is offered on a category breakdown and drawn. The spec declares
Vega-Lite v5 against a v6.4.3 bundle. Every chart container overflows 8px.

**Second plan — the UI.** With the chat open, `document.elementFromPoint` at the centre of every tab
returns the chat's input: the whole bottom navigation is dead. The transfer preview offers to confirm
an overdraft and shows «Te quedarían -7.776,08 €» (the backend does refuse on confirm, so no money
moves). The credit meter compares a year of charges against a monthly limit and clamps to «100% de
1.500,00 €» where the real figure is 398%. The movement list cuts at 50 rows without saying so. The
filter chips carry no `aria-pressed`.

**Not a defect, worth a decision.** Every spending question costs two turns, because a holder with
three accounts is asked which one before any figure. Inside a conversation the answer is remembered;
between conversations it never is. For a voice assistant that doubles every interaction.

---

## Root causes found on 2026-08-28

Task 3's deliverable. Measured against the running compose stack at `6ba084c`, token
`demo-token-laura-pays`, seeded today 2026-08-28. Every cause below was confirmed twice: once over
HTTP against the live service, and once in-process by calling the recogniser or the patch directly
with the same sentence, so the file and line quoted are the line that decides rather than the line
that looked likely. Paths are relative to
`apps/orchestrator/src/finguard_orchestrator/` unless stated otherwise.

**All six still reproduce.** Two of them turned out to be one cause seen twice: Step 4 is a
downstream consequence of Step 2, proved by construction below.

### Step 1 — «en todas mis cuentas» hijacks `group_by` — BUG

**Reproduces.** `cuál fue mi mayor gasto de julio en todas mis cuentas` →
`PLANNED  group_by=ACCOUNT  group_limit=5  rank=null`, text «La cuenta con mayor importe total del
2026-07-01 al 2026-07-31 es: Cuenta corriente…».

**Cause:** `application/queries/ask_financial_question.py:4383-4385`, `_GROUP_RANKING_DIMENSION`.

The pattern is a bare noun alternation — `categorias?|comercios?|tiendas?|establecimientos?|cuentas?`
— with no requirement that the noun be preceded by a grouping preposition. The scope phrase «en
todas mis **cuentas**» therefore supplies the dimension, `_named_group_dimension` maps it to
`GroupBy.ACCOUNT` (line 4406), `_GROUP_RANKING_SIGNAL` (lines 4411-4415) matches «mayor gasto»
through its `mayor(?:es)?\s+(?:gasto|importe|ticket|media|promedio)` alternative, and
`_group_ranking_plan` (line 5814) returns a ranking of accounts. It runs at line 1335, ahead of every
other reader and ahead of the planner, so nothing downstream ever sees the superlative.

**Ruled out, with the check.** The plan pointed at `explicit_facts`. It is not there.
`explicit_facts.read("cuál fue mi mayor gasto de julio en todas mis cuentas")` returns
`group_by=None, asks_account_breakdown=False`; `_ACCOUNT_BREAKDOWN`
(`domain/services/explicit_facts.py:194-201`) does not match, because every one of its alternatives
requires «por/de cada/separado por cuenta» or «en qué cuenta» — which is exactly the guard
`_GROUP_RANKING_DIMENSION` lacks. The account-scope vocabulary is innocent too:
`account_words.asks_for_all_accounts` correctly returns True and `named_in` correctly returns `()`.

The asymmetry that makes this narrow: «cuánto gasté en julio en todas mis cuentas» has the dimension
word but no ranking signal, so it is unaffected. Only a superlative *plus* the scope phrase changes
meaning.

### Step 2 — `across_accounts` narrows while `selection_mode` stays `ALL` — BUG

**Reproduces exactly as recorded**, including the answer dropping «Consultado en todas tus cuentas».

**Cause:** `application/queries/ask_financial_question.py:4063-4071`, the `remembered` ternary in
`_answer`.

`_answer` deliberately reads with `scoped` and remembers `plan` — the comment at lines 4059-4062 says
so, and the rule is reasonable. But the exception it makes for a widened scope is
`scoped.across_accounts and account_words.asks_for_all_accounts(query.sentence)` (line 4067): the
**current sentence** has to re-say «todas mis cuentas». A scope settled by an earlier turn does not
qualify, so `remembered` falls back to the planner's plan with `across_accounts=False`, and that is
what `_answered` receives at line 4241 and what becomes `conversation.last_plan`. Meanwhile
`_remember_runtime_scope` (lines 7201-7212) correctly records `AccountSelectionMode.ALL` on the
conversation. Two halves of one fact, written by two lines eight apart.

The vanished note is the same line: `_scope_note` (lines 7146-7147) reads `plan.across_accounts` off
`remembered`, so a turn whose figures came from three accounts is presented as if scoped to one.

**One correction to the finding as recorded.** The plan says «on a holder with money spread across
accounts this under-reports». It does not, today: the ledger read at lines 4076-4088 is executed with
`scoped`, and `_scoped`'s persisted-`ALL` branch (lines 3801-3802) widens every subsequent
unqualified turn as well. The figures are right; the recorded plan and the sentence about it are
wrong. That still matters, because the recorded plan is an input elsewhere — see Step 4, which is
this same bug producing a wrong outcome rather than only a wrong label.

**The fix carries one design choice.** Either `remembered` honours a settled
`conversation.account_selection`, or `_scope_note` and `_period_comparison_plans` stop reading scope
off the remembered plan and read the selection instead. The second is the smaller change and puts the
fact in one place; the first keeps `last_plan` self-describing. Decide before writing the task.

### Step 3 — «qué día suelo gastar más» answers with an average — BUG

**Reproduces.** Three turns (`qué día suelo gastar más` / `en julio` / `todas mis cuentas`) →
`metric=AVERAGE group_by=DAY`, «El importe medio por movimiento … fue de 67,71 €».

**The single-turn form is correct**, which is what localises it:
`qué día suelo gastar más en julio en todas mis cuentas` → `metric=FREQUENCY`, «28 veces del
2026-07-01 al 2026-07-31: aproximadamente cada día.» The defect is in the clarification round trip,
not in the reading of the sentence.

**Cause:** `application/queries/ask_financial_question.py:6734-6735` and `:1453-1460`.

`explicit_facts.read` gets this right on its own — it returns `Metric.FREQUENCY`, because `_ASKS_WHEN`
(`domain/services/explicit_facts.py:103-106`) matches «que dia» and `asks_average` is False (the
`_TYPICAL_AMOUNT` half at lines 415-417 also needs a temporal group, which «qué día» is not). Nothing
applies it:

1. The sentence carries no window, so the LLM planner answers `NEEDS_CLARIFICATION` with a draft.
   `_next_state` (lines 6734-6735) stores `planned.draft` verbatim in the `FinancialClarification`.
   `explicit_facts.enforce` is never called on the declined branch — only at line 1523, on the
   answerable one. The stored draft is `{group_by: DAY, metric: AVERAGE}`, the model's reading;
   confirmed by dumping `conversation.draft` after turn 1.
2. The reply «en julio» resumes through `_finish_pending` (line 2138), which adds the window and is
   dispatched at lines 1453-1460 straight into `self._answer`, bypassing line 1523 altogether. The
   facts read on that turn would be «en julio»'s anyway, and its metric is `None`.

**Contributing, and why the corpus never saw it.** The deterministic `_frequency_plan` (line 4682)
declines at lines 4694-4699 when the sentence names no category, which is why this sentence reaches
the model at all. Every asserted frequency sentence names one — `tests/test_frequency.py` uses
«gasolina», «supermercado» and «pagarme», and each states its own window in the same sentence, so
none of them ever takes the clarification round trip. The `suelo gastar` cases in corpus 14 are the
same shape.

### Step 4 — «compáralos» after two answered periods is refused — BUG, and it is Step 2

**Reproduces**, and its cause is Step 2's remembered plan.

**Cause:** `application/queries/ask_financial_question.py:4566` with `:4573`, in
`_period_comparison_plans`.

The recogniser accepts the pronoun (`_PERIOD_COMPARISON_REFERENCE` fullmatches «comparalos») and then
requires the two stored plans to differ *solely* by window: `across_accounts` is a member of the
`scope` tuple at line 4566, and line 4573 returns `None` if any scope field disagrees. In the
recorded conversation the July plan was remembered as `scoped` (`across_accounts=True`, because that
turn's sentence *was* «todas mis cuentas») and the June plan as the unscoped planner plan
(`across_accounts=False`). The pair disagrees on a field neither question mentioned.

**Proved by construction.** The same conversation with the scope named on both turns, so both plans
are remembered scoped:

```
'cuánto gasté en julio en todas mis cuentas'   -> 1.895,97 €   across=True
'cuánto gasté en junio en todas mis cuentas'   -> 2.081,40 €   across=True
'compáralos'  -> PLANNED «En junio de 2026: 2.081,40 €; en julio de 2026: 1.895,97 €.
                 El gasto fue 185,43 € mayor en junio de 2026.»
```

So `_period_comparison_plans` needs no change of its own. Fixing Step 2 fixes this, and Step 4's task
should be a regression test on the three-turn conversation rather than a second edit.

### Step 5 — «compáralo con junio» is refused where «con el mes anterior» works — BUG

**Reproduces.** Verified in-process against a conversation carrying a July `last_plan`, which takes
the LLM out of the reproduction: all three comparison recognisers return `None` for «compáralo con
junio», and `_comparison_with_previous_period` returns a well-formed `ComparisonPlan` for «compáralo
con el mes anterior».

**Cause:** `application/queries/ask_financial_question.py:206-217`, `_COMPARE_WITH_PREVIOUS_PERIOD`.

This is the only recogniser that pairs the plan in view with a window named in the sentence, and its
second operand is a closed list of *relative* calendar-unit nouns —
`fin de semana|trimestre|semestre|quincena|mes|semana|ano|periodo` followed by `anterior|pasad[oa]`
(lines 215-216). A month name is not in it, and neither is anything else `window_words.window_in` can
read. The other two recognisers do not cover the shape either: `_period_comparison_plans` (line 4536)
needs both operands already in the conversation, and `_direct_period_comparison` (line 5087) needs
both windows in the sentence — `window_words.compared_calendar_months("compáralo con junio")` is
`None`, because there is one month and a pronoun. With no recogniser claiming it the sentence falls to
`capability_guard`, whose COMPARISON family pattern `\bcompara`
(`domain/services/capability_guard.py:83`) refuses it, and the refusal text at lines 4286-4289
recommends «compara mis gastos de julio con junio» — which does work, because it names both windows.

The missing vocabulary is therefore not a word but a shape: «pronoun + one named window». The fix is a
recogniser that takes `window_words.window_in(sentence)` as the second operand of a bare comparison
imperative, with `last_plan`'s window as the first.

### Step 6 — an inert exclusion is presented as applied — PRODUCT DECISION

**Reproduces.** Over HTTP, on a route that needs no planner call so it replays deterministically:

```
'top 3 comercios de supermercado en julio en todas mis cuentas'
   -> Carrefour: 218,28 € (3) · Mercadona: 131,21 € (2) · Alcampo: 75,22 € (2)
'sin contar el alquiler'
   -> «Tus 3 comercios con mayor importe total en supermercado, sin alquiler …»
      Carrefour: 218,28 € (3) · Mercadona: 131,21 € (2) · Alcampo: 75,22 € (2)
      plan gains excluded_categories=['HOUSING_RENT']; every figure identical
```

And the whole chain confirmed in-process on the same shape of plan:

```
continuation_patch.apply("sin contar el alquiler", <julio, categories=('GROCERIES',)>)
  -> APPLIED   categories=('GROCERIES',)   excluded_categories=('HOUSING_RENT',)
answer_writer.filter_of(that plan)  ->  ' en supermercado, sin alquiler'
```

**Where it happens:** `domain/value_objects/plan_patch.py:84-92` and
`domain/services/answer_writer.py:395-405`.

`PlanPatch.apply_to` (`plan_patch.py:84-92`) unions the exclusion into `excluded_categories` and
removes it from `categories`, which removes nothing — `HOUSING_RENT` was never inside a `GROCERIES`
filter. Nothing tests whether the exclusion intersects what is being counted.
`answer_writer.filter_of` (`answer_writer.py:395-405`) then states every exclusion on the plan
unconditionally, so the sentence claims a filter that cannot have changed the figure. The total and
the movement count are identical before and after, which is the only way a reader can tell.

**This is the product call the plan flagged, and it is genuinely open.** Three defensible answers:
(a) apply the patch and say it changed nothing — «El alquiler no estaba en supermercado, así que la
cifra no cambia»; (b) refuse the patch as not applicable and leave the previous answer standing;
(c) accept it silently as future-proofing, on the grounds that the exclusion is a real instruction
that will matter if the category filter is later widened. Today's behaviour is (c) *presented as*
(a) — and that part is indefensible whichever way the decision goes. Recommended: (a). It is the only
one of the three that tells the reader the truth about the figure in front of them.

### Not found

Nothing: all six have a file and a line, and two of them share one.

### Note for whoever writes Tasks 4-9

Steps 2 and 4 are one task, not two. Step 3's fix touches the clarification lifecycle rather than a
vocabulary, so it needs a test at the boundary («a draft stored by a declined turn is enforced against
the sentence that produced it») rather than a corpus sentence alone. Steps 1 and 5 are both regex
edits with obvious corpus cases. Step 6 needs the decision above taken before any code is written.
