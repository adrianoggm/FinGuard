# What the test suite does and does not prove

Audit by mutation testing, 2026-08-28. Base commit `8300929` on `task/visualization`.

**Baseline, reproduced before any mutation and restored after every one:**

```
uv run --directory apps/orchestrator pytest -q -m "not integration"
2925 passed, 3 skipped, 2 deselected in 258.77s (0:04:18)
```

```
uv run --directory apps/banking-api pytest -q -m "not integration"
240 passed, 149 deselected in 2.28s
```

Method: break the code deliberately, run the tests that claim to cover it, record whether
anything failed. 31 mutations across 12 files. Every mutation was reverted with
`git checkout --` and the tree confirmed clean (`git status --porcelain` empty) between
targets. Nothing in this report is an opinion about test style; every line is a command and
its exit state.

A note on reading the table: a mutation that "survived" a selection which did not contain
its tests is not a finding, so each row names the selection, and every survivor that
mattered was re-run against the **full** suite. Two of the three headline survivors are
full-suite survivors.

---

## 1. The table

`AFQ` = `apps/orchestrator/src/finguard_orchestrator/application/queries/ask_financial_question.py`.
Line numbers are as of `8300929`. All commands run from the repository root.

The recurring selection strings are abbreviated in the table and spelled out under it.

| # | Target | Site | Mutation | Selection | Result |
|---|--------|------|----------|-----------|--------|
| 1a | `answer_writer.money` grouping | `domain/services/answer_writer.py:83` | delete `.replace(",", ".")` so `1.895,97` becomes `1,895,97` | `S-money` | **caught** (28 failed) |
| 1b | `money` cents padding | `answer_writer.py:95` | `{cents:02d}` → `{cents:d}` | `S-money` | **caught** (17 failed) |
| 1c | `money` sign | `answer_writer.py:81` | `sign = "-" if amount_minor < 0 else ""` → `sign = ""` | `S-money`, then **full suite** | **SURVIVED** (127 passed; full suite `2925 passed`) |
| 2a | `Caller.may_read` | `domain/value_objects/caller.py:83` | `return account_id == self.account_id` → `return True` | **full suite** | caught, but by **only 2 of 2925** |
| 2b | the one real authorisation call site | `application/metrics_executor.py:22` | `if plan.only_account_id is not None:` → `if False:` | `S-metrics` | **SURVIVED** (71 passed) |
| 3a | `_scoped`, learned-alias branch | `AFQ:3815-3822` | replace the `return replace(plan, …only_account_id=wanted_ids[0]…)` block with `return _across_every_account(plan)` | `-k "alias or scope or account"` | **caught** (2 failed) |
| 3b | `_scoped`, persisted-scope revalidation | `AFQ:3867` | `if not set(persisted.account_ids).issubset(live_ids):` → `if False:` | `-k "alias or scope or account or selection"` | **caught** (1 failed) |
| 4a | `MetricTokenCodec.verify` signature | `infrastructure/security/metric_tokens.py:53-56` | drop the `hmac.compare_digest` term, keeping only `if key is None:` | `S-metrics` | **caught** (1 failed) |
| 4b | `verify` holder binding | `metric_tokens.py:57` | `if raw.get("h") != holder_reference:` → `if False:` | `S-metrics` | **caught** (2 failed) |
| 5a | `plan_verifier.disagreement` | `domain/services/plan_verifier.py:194` | `for name, broken in _RULES:` → `for name, broken in ():` (always approve) | `S-verifier` | **caught** (12 failed) |
| 5b | `capability_guard` refuses for the **wrong reason** | `domain/services/capability_guard.py:59,66` | swap the `AVERAGE` and `RATIO` compiled patterns | `tests/test_capability_guard.py` | **SURVIVED there** (1 failed, and not one of the three shape tests). Caught only by `tests/test_accents_do_not_hide_a_rule.py` (4 failed) |
| 5c | one guard family never fires | `capability_guard.py:308` | `if not policy.allows(name):` → `if name == "CATEGORY_EXCLUSION" or not policy.allows(name):` | `-k "guard or exclusion or capability or accent"`, then **full suite** | **SURVIVED** (449 passed; full suite `2925 passed`) |
| 6a | `PlanPatch.apply_to` window | `domain/value_objects/plan_patch.py:66` | `if self.window is not None:` → `if False:` | `-k "patch or window or follow or refin or merge"` | **caught** (1 failed) |
| 6b | `apply_to` exclusion | `plan_patch.py:84` | `if self.excluded_categories:` → `if False:` | `S-inert` | **caught** (3 failed) |
| 7a | `inert_exclusions` returns everything | `answer_writer.py:440-444` | replace the whole body with `return tuple(plan.excluded_categories)` | `S-inert` | **caught**, but by an `IndexError` at collection, not an assertion |
| 7b | `inert_exclusions` drops its membership test only | `answer_writer.py:442-444` | `return tuple(plan.excluded_categories)`, keeping the `if not plan.categories: return ()` guard | `S-inert` | **SURVIVED** (47 passed) — see §2.4, this one is a deliberate second lock |
| 7c | `inert_exclusions` returns nothing | `answer_writer.py:440` | `if not plan.categories:` → `if True:` | `S-inert` | **caught** (8 failed) |
| 8a | `derive_chart` limit | `domain/models/chart.py:318` | `limit=None if temporal or plan_limited or …` → `limit=None` | `S-chart` | **caught** (2 failed) |
| 8b | `derive_chart` sort | `chart.py:312` | `sort="descending" if not temporal else None` → `sort=None` | `S-chart` | **SURVIVED** (44 passed) |
| 8c | `derive_chart` mark | `chart.py:292` | `("line" if temporal else "bar")` → `("bar" if temporal else "line")` | `S-chart` | **caught** (1 failed) |
| 9a | banking balance sign | `banking-api/…/mappers/row_mapper.py:18` | `Money(int(row["booked_minor"]), …)` → `Money(-int(row["booked_minor"]), …)` | `S-banking` | **SURVIVED** (240 passed) |
| 9b | `Money.__sub__` | `banking-api/…/value_objects/money.py:47` | `self.amount_minor - other.amount_minor` → `+` | `S-banking` | **caught** (1 failed) |
| E1 | `_across_every_account` | `AFQ:364` | `replace(plan, across_accounts=True, only_account_id=None, only_account_ids=())` → `replace(plan, across_accounts=True)` | `-k "account or scope or across or widen"` | **caught** (1 failed) |
| E2 | `result_reference_of` source binding | `domain/models/result_reference.py:114` | `if conversation.last_plan is not source_query:` → `if False:` | `-k "reference or result or focus or context or conversation"`, then **full suite** | **SURVIVED** (606 passed; full suite green — see note) |
| E3 | `focused_on_result` identity check | `result_reference.py:86` | `if conversation.last_plan is not reference.source_query:` → `if False:` | `-k "reference or result or focus"` | **SURVIVED** (168 passed) |
| E4 | `ResultReference` grounding invariant | `result_reference.py:67-75` | the `category is not grounded by its source query` condition → `if False:` | `-k "reference or result or focus or round_trip or conversation"` | **caught** (2 failed) |
| E5 | `capability_guard.ordinal_in` narrowness | `capability_guard.py:280` | `_ONLY_A_CONTINUATION.fullmatch(…)` → `.search(…)` | `-k "ordinal or continuation or ranking or superlative or top"` | **SURVIVED** (203 passed) |
| E6 | `_starts_new_financial_scope` demonstratives | `AFQ:375` | `if re.search(r"\b(?:esa\|ese\|esas\|esos)\b\|\blo\s+mismo\b", folded):` → `if False:` | `-k "scope or follow or continuation or wander or drift or corpus"` | **SURVIVED** (433 passed) |
| B1 | *harmless* control: tooltip encoding order | `chart.py:309-310` | swap the two `ChartEncoding("tooltip", …)` entries | `S-chart` | passed — **not brittle** |
| B2 | *harmless* control: base encoding order | `chart.py:301-302` | swap the `y`/dimension and `x`/value entries | `S-chart` | **1 failed — brittle**, see §4 |

Selection strings, copy-pasteable. Prefix each with
`uv run --directory apps/orchestrator pytest -q -m "not integration"`:

- `S-money` = `tests/test_answer_headline.py tests/test_balance_answer.py tests/test_balance_across_accounts.py tests/test_percentages.py tests/test_the_reported_conversation.py tests/test_top_movements.py`
- `S-metrics` = `tests/test_saved_metrics.py tests/test_saved_metric_codec.py tests/test_visualization_stage1.py tests/test_visualization_stage2_core.py tests/test_identity.py tests/test_scopes.py`
- `S-verifier` = `tests/test_plan_verifier.py tests/test_category_misread.py tests/test_a_verified_disagreement_is_repaired.py tests/test_guard_policy.py tests/test_the_guard_owns_refusals.py`
- `S-inert` = `-k "inert or exclusion or exclud or headline"`
- `S-chart` = `-k "chart or visual or vega"`
- `S-banking` = `uv run --directory apps/banking-api pytest -q -m "not integration"`

Note on E2's full-suite run: I ran it with `-p no:logging` and got
`2924 passed, 3 skipped, 2 deselected, 1 error`. The single error was
`tests/test_turn_interpreter.py::test_the_log_line_carries_the_decisions_and_not_the_sentence`,
which needs the `caplog` fixture that flag disables. Re-running that file with logging on
under the same mutation gives `60 passed`. The mutation is a clean full-suite survivor; do
not use `-p no:logging` when reproducing.

---

## 2. The survivors, in order of what they cost

### 2.1 `money()` can lose the minus sign and nothing anywhere notices

`answer_writer.py:81`, mutation 1c. With `sign = ""`:

```
uv run --directory apps/orchestrator python -c "from finguard_orchestrator.domain.services import answer_writer as a; print(repr(a.money(-189597,'EUR')), repr(a.money(189597,'EUR')))"
'1.895,97 €' '1.895,97 €'
```

A refund reads as a charge; an overdraft reads as savings. Full suite: `2925 passed`.

Why the obvious candidate misses it: `tests/test_percentages.py:230`
`test_negative_net_cash_flow_is_explained_without_hiding_either_side` asserts
`result.total_minor == -50_000` — the raw integer, where the sign does survive — and then
asserts the *text* contains `"gastaste 500,00 € más de lo que ingresaste"`, which words the
negative positively — so the assertion straddles the minus without ever touching it.

> **Correction, 2026-08-28, after the fix.** The two sentences that stood here claimed the
> rendered string never carries a minus, and that the sign branch is therefore unobserved. Both
> are wrong, and the error mattered: it downgraded a live-path hole to a formatting nicety, and
> the session manager repeated the downgrade to the product owner. `ratio_writer._net_cash_flow`
> (`domain/services/ratio_writer.py:78`) renders `money(net, currency)` where
> `net = income.total_minor - expenses.total_minor`, inside a function that branches on
> `elif net < 0`. The sentence this very test exercises reads *"Tu flujo neto en lo que va de mes
> fue de **-500,00 €**"*. So the sign is rendered, on a path a user reads, and dropping it turns
> an overspend into a surplus. A second, weaker path: `answer_writer._one_account:178,181` passes
> `AccountSummary.available_minor` through unguarded, and nothing constrains that to be positive.
> The other derived-figure call sites *are* guarded — `ask_financial_question.py:3023` orders its
> operands, `:3510` and `ratio_writer:70` wrap in `abs()` — which is what made the wrong reading
> plausible. Closed by `05a7429`, which added the seventeen direct assertions this section asked
> for; mutation 1c now fails eight of them.
>
> The methodological lesson is the one this audit exists to teach: checking that a function is
> correct in isolation says nothing about whether a caller can reach its broken branch. Trace the
> callers, or say you did not.

Confirmed structurally: `grep -rn "money(-" apps/orchestrator/tests` and
`grep -rn '"-[0-9]' apps/orchestrator/tests` both return nothing. **No test in the suite
passes a negative amount to `money()` or asserts a money string containing a minus.**

Should have caught it: a unit test on `money` itself, e.g.
`assert money(-189597, "EUR") == "-1.895,97 €"`. There is no `tests/test_answer_writer.py`
covering `money` directly at all; every assertion on it is indirect, through a sentence.

### 2.2 Cross-account isolation is asserted by two unit tests, and the guard on the main path is a tautology

This is the most important finding in the audit, and it has two halves.

**Half one, by mutation.** `caller.py:83` → `return True`, full suite:

```
FAILED tests/test_identity.py::test_a_caller_may_read_only_its_own_account
FAILED tests/test_identity.py::test_authorisation_is_a_check_and_not_a_comment
2 failed, 2923 passed, 3 skipped, 2 deselected
```

Exactly **2 of 2925**. Both are direct unit tests of the value object
(`tests/test_identity.py:100-103` and `:147-150`). Nothing in the pipeline, the controller,
the corpus or the metrics path observes authorisation at all. The suite proves `may_read`
computes the right boolean. It does not prove anything reads it.

**Half two, by code reading — no mutation needed.** `may_read` / `assert_may_read` has
exactly two production call sites:

```
apps/orchestrator/src/finguard_orchestrator/infrastructure/controllers/llm_controller.py:1318:    who.assert_may_read(who.account_id)
apps/orchestrator/src/finguard_orchestrator/application/metrics_executor.py:23:            caller.assert_may_read(plan.only_account_id)
```

The first — the one on the question-answering path, the path every conversation takes —
passes the caller's own account id to the caller's own check. It reduces to
`who.account_id == who.account_id`. **It cannot fail for any input, under any
implementation of `may_read`.** The docstring on `may_read` anticipates precisely this
failure mode — "review rightly flagged that an unused authorisation check is worse than
none: it reads like a control while enforcing nothing" — and then the call site that was
added to answer that review is inert.

**And the one call site that is not a tautology has no test.** Mutation 2b deletes the
`metrics_executor` guard entirely; 71 tests pass. `financial_metric_executor` is exercised
only by `tests/test_saved_metrics.py:177` and `:207`, both of which construct a caller whose
account matches and a plan whose `only_account_id` is unset, so the guard is never reached
even with a correct implementation.

Should have caught it: a test that pins a saved metric to account A and replays it as
holder B, expecting `NotAuthorisedError`. And a controller-level test that reaches
`llm_controller.ask` with an account id that is not the credential's — which today is
unreachable by construction, and that is the argument for the tautology, but a guarantee by
construction is worth a test that fails if the construction changes.

### 2.3 A whole capability-guard family can be switched off silently

Mutation 5c, `capability_guard.py:308`, full suite `2925 passed`.

The family is **live, not dead code** — it fires on real sentences:

```
CATEGORY_EXCLUSION  <- cuanto gaste en alquiler excepto lo que pague con la tarjeta de credito el mes pasado
CATEGORY_EXCLUSION  <- cuanto gaste en ocio quitando
CATEGORY_EXCLUSION  <- ni incluyas lo de siempre, cuanto gaste en ocio
```

so this is untested live behaviour, not a `if False` waiting to be deleted. No test in the
suite feeds `unsupported_reason` a sentence on which `CATEGORY_EXCLUSION` fires. Every test
touching the family asserts it does **not** fire
(`tests/test_accents_do_not_hide_a_rule.py:83`, `:98`, `:102`).

**Two further defects surfaced while establishing reachability**, and they are worse than the
missing test. `explicit_facts.excluded_merchants_in` accepts arbitrary trailing free text as
a merchant name, which exempts the guard through the door at `capability_guard.py:325`:

```
named=('TRANSPORT_FUEL',)  exmerch=('algunas cosas',)      <- cuanto gaste en gasolina sin contar algunas cosas
named=('TRANSPORT_FUEL',)  exmerch=('contar',)             <- cuanto gaste en gasolina sin contar
named=('TRANSPORT_FUEL',)  exmerch=('lo que ya sabes',)    <- cuanto gaste en gasolina si no contamos lo que ya sabes
```

All three return `unsupported_reason() == None` and proceed to a plan carrying an
`excluded_merchants` filter that matches nothing. The comment at `capability_guard.py:320-326`
states the intended behaviour — "a vague `excepto algunas cosas` still belongs to this
guard" — and the code does the opposite. `"contar"`, the verb from the exclusion phrase
itself, becomes a merchant to exclude. Nothing refuses these, and no test covers them.

Should have caught it: a parametrised test asserting
`unsupported_reason(s) == "CATEGORY_EXCLUSION"` for a vague exclusion beside a named
category — the mirror of the existing `test_every_family_is_caught_with_or_without_accents`,
which pins the other six families and omits this one.

### 2.4 Two survivors that are *not* holes, and the distinction matters

Being objective cuts both ways. Mutations 7b, E2 and E3 survived because the invariant they
check is **also enforced upstream**, not because nobody thought about it:

- **7b** (`inert_exclusions` membership test). The docstring says `check_filters` already
  refuses a category that is both included and excluded, "and the membership test is still
  written out, as a second lock on that door". The first lock catches it, so the second is
  unmutatable by construction.
- **E3** (`focused_on_result` identity check) and **E2** (`result_reference_of` source
  binding). `Conversation.__post_init__:428-432` raises
  `"a result reference needs its exact source query"` whenever
  `result_reference.source_query is not last_plan`. Because `focused_on_result` reaches the
  state through `replace(...)`, the dataclass invariant fires first. The test named for this
  behaviour, `tests/test_result_reference.py:239`, does exercise the rejection at line 259 —
  it just gets its exception from `Conversation`, not from the function under test.
  Separately, `focus_of` (`planning_context.py:93-94`) always builds
  `QueryFocus(conversation.last_plan, …)`, so E2's guard is tautological on every call site
  that derives its focus from the same conversation — the same shape as the
  `llm_controller` tautology in §2.2, but benign here.

A defence-in-depth guard is a legitimate thing to have and an impossible thing to
mutation-test. I flag these as survivors for completeness and explicitly **do not** count
them as gaps. The nuance is worth keeping: a reviewer who treats every survivor as a bug
will delete real second locks, and a reviewer who treats every survivor as deliberate will
miss §2.1 through §2.3.

### 2.5 Chart sort, ordinal narrowness, follow-up demonstratives

Three genuine, cheap gaps on the conversational path:

- **8b** — `derive_chart` can stop sorting a categorical ranking descending; 44 chart tests
  pass. Every `descending` assertion in the suite is on a spec a fixture built explicitly
  (`test_visualization_stage2_core.py:120`, `:367`, `test_saved_metrics.py:52`) or on a
  stage-2 reorder patch. **None is on the sort `derive_chart` chose.** "Where do I spend
  most" comes back as an unsorted bar chart and the suite is silent.
- **E5** — `ordinal_in`'s `fullmatch` can become `search`; 203 tests pass. Proven not a
  no-op: `"y el segundo, cuanto me costo en total la reforma entera"` returns `None`
  unmutated and `OrdinalReference(mode='ABSOLUTE', rank=2)` mutated. The docstring's
  "**Narrow on purpose** … A sentence carrying its own question is a new question, and
  reading '¿cuánto gasté?' as a continuation would answer it with the second largest
  expense" is asserted by nothing.
- **E6** — `_starts_new_financial_scope` can stop reading demonstratives; 433 tests pass,
  including the conversation corpus. Proven not a no-op:
  `"cuanto gaste en esa cuenta este mes"` flips `False` → `True`, i.e. a follow-up that says
  "esa" is re-read as a new question and silently drops the inherited category — the exact
  defect the docstring cites ("Inheriting an old category there is how 'cuánto gasté en la
  cuenta principal este mes' remained filtered by restaurants").

### 2.6 Banking: what I could not test, stated as such

**Banking's balance arithmetic is not Python and I could not test it.** The derivation is a
Postgres view, `apps/banking-api/migrations/versions/0015_classification_layers.py:116-125`:

```sql
COALESCE(SUM(t.signed_amount_minor), 0) AS booked_minor
FROM accounts a LEFT JOIN assistant_read.transactions_v1 t ON t.account_id = a.id
```

`PostgresLedgerRepository.balance_of` just runs `_BALANCE_SQL` against it. Perturbing that
arithmetic requires a database, and `tests/test_balance_repository.py` — whose docstring is
literally *"The balance comes from the entries, not from a column someone wrote"* — is
`pytestmark = pytest.mark.integration`. **I did not run it and I am not reporting a pass or
a fail on it.** 149 of banking's 389 tests (38%) are deselected without a database, so the
banking figure in this audit covers 240 tests, not the suite.

What I could reach on the Python side did produce a survivor. Mutation 9a negates every
balance the API reports and all 240 unit tests pass. `row_mapper.to_balance` is referenced
only by `ledger_repository.py:45` (integration-only) and by no test at all; `to_group` is
likewise untested, while its sibling `to_movement` does have
`tests/test_movement_semantics.py`. So the mapper that turns a row into money has no unit
test, and its only coverage is behind the database I do not have.

`Money` arithmetic itself is properly covered — 9b was caught immediately.

---

## 3. Tests that assert shape where the business fact is the value

72 of 1478 test functions (4.9%) have **no assertion other than** a `is None` / `is not None`
/ `isinstance` check. Many are legitimate: for a function returning `str | None`, `is None`
*is* the outcome. The `is not None` direction is where the value is the fact. The worst five:

1. **`tests/test_banking_client.py:230-234`**, `test_amounts_stay_in_minor_units`.
   `assert isinstance(answer.total_minor, int)`, under the comment "No float anywhere on the
   path. That is how cents go missing." The input is the literal `142580`, so
   `isinstance(…, int)` is trivially true; a client that did `// 100` would still return an
   `int` and still pass. The business fact is `answer.total_minor == 142580`.

2. **`tests/test_capability_guard.py:36`, `:40`, `:49`** —
   `assert unsupported_reason(…) is not None`. **Proven weak by mutation 5b:** swapping the
   `AVERAGE` and `RATIO` patterns makes the guard refuse for the wrong reason, and all three
   stay green. The family name is the fact, and
   `tests/test_accents_do_not_hide_a_rule.py:79` already asserts it properly for six
   families. These three are strictly weaker duplicates of a test that exists.

3. **`tests/test_payment_words.py:92-95`**,
   `test_a_period_beside_an_amount_does_not_swallow_it`.
   `assert payment_words.amount_in(sentence) is not None` for `"le paso 100 al mes"`. The
   docstring is entirely about reading the *right* number rather than the period's digits —
   so the fact is `== 10_000`, and any non-`None` wrong amount passes. The sibling at
   `:100`, `test_the_first_amount_wins`, gets this right (`== 1_500`).

4. **`tests/test_payment_client.py:164-167`**, `test_a_timestamp_never_arrives_naive`.
   `assert preview_from_body(PREVIEW).expires_at.tzinfo is not None`, with a docstring
   saying "Banking writes UTC with a Z … would print an hour that is not the one the ledger
   recorded." Any timezone satisfies `is not None`, including a wrong one, which is the
   defect described. The fact is `.tzinfo is UTC` — as the sibling at `:159` already does
   with `datetime(2026, 8, 3, 10, 1, tzinfo=UTC)`.

5. **`tests/test_plan_verifier.py:83-87`**,
   `test_asking_how_many_may_not_be_answered_with_how_much`.
   `assert disagreement(…) is not None`. The name states the rule; the assertion accepts any
   of the four. The fact is `== "COUNT_ANSWERED_AS_AMOUNT"`, which matters because
   `repaired()` dispatches on exactly that string.

---

## 4. The inverse failure: over-specification

One of my two harmless controls broke tests, so brittleness is real but narrow.

**B2 — `chart.py:301-302`**, swapping the order of the `y`/dimension and `x`/value entries
in `derive_chart`'s `base` tuple. Vega-Lite's `encoding` is an object keyed by channel, so
the order two encodings appear in a Python tuple has no effect on any rendered chart. It
breaks `tests/test_visualization_stage1.py:104`:

```python
assert categorical.encodings[0].channel == "y"
```

The test pins list *position*, not the fact it means — that the dimension sits on the y axis
of a categorical bar chart. The outcome-preserving form is
`next(e for e in categorical.encodings if e.field == "dimension").channel == "y"`.

**B1** — the same swap applied to the two `tooltip` encodings (`chart.py:309-310`) passed.
So the over-specification is one assertion, not a pattern, and the honest summary is that
this suite is much more prone to *under*-specification than to brittleness. I found one
brittle assertion and nine surviving mutations.

---

## 5. Near-duplicate tests, quantified

The "useless repetition" complaint is **not borne out** at the level the phrase suggests, and
I would rather say so than confirm a premise.

Normalising every string and numeric literal to a placeholder and grouping tests whose whole
body then matches exactly:

- **12 clusters**, containing **28 test functions**, of which **16 are redundant**
  (cluster size minus one, summed). That is **1.1% of 1478**.

Grouping more aggressively — by the normalised set of `assert` lines alone, ignoring setup,
which catches tests that pin the same claim through different fixtures:

- **36 clusters** of more than two tests, containing **147 functions**, **111** of which
  would disappear if each cluster collapsed to one parametrised test. That is **7.5%**.
  One caveat, stated because it inflates the number: the largest apparent cluster (20 tests
  sharing the assertion `assert (`) is an artefact of my line-based normaliser meeting
  multi-line assertions, and should be discounted.

The three biggest genuine clusters:

1. **`tests/test_intent_router.py`** — 5 tests whose entire body is
   `assert route(<sentence>) is Intent.FINANCIAL_QUERY` (lines 91, 360, 364, 368, 372); four
   of them are consecutive. 28 of that file's 31 tests are a single `assert route(...)` call.
2. **`tests/test_payment_router.py`** — 5 tests whose entire body is
   `assert payment_router.is_payment(sentence) is False` (lines 185, 254, 262, 287, 333),
   each already parametrised, so the collapse is five decorators into one.
3. **`tests/test_capability_guard.py`** — the 3 tests from §3 item 2, all
   `assert unsupported_reason(<sentence>) is not None`. This cluster is the interesting one:
   it is both duplicated *and* weaker than the single test that supersedes it.

Also worth a number, since it is where the bulk actually is: 1478 test **functions** collect
to 2930 test **cases**, so parametrisation roughly doubles the suite. That breadth is cheap
and mostly legitimate — many of those cases are distinct Spanish phrasings, which is real
coverage of a real input space.

**The honest conclusion is that repetition is not this suite's problem.** 1.1% exact
duplication is low for 1478 tests. The problem is the nine mutations above that nothing
caught: the suite is large and repetitive-looking because it is broad across *phrasings*,
while being thin across *outcomes* — it asserts the same kind of fact many times and several
important kinds of fact never.

---

## 6. What an objective quality measurement would look like

**What exists genuinely covers plan construction.** The planning corpus is 310 cases across
four files (`dev.yaml` 120, `validation.yaml` 79, `drift.yaml` 75, `adversarial.yaml` 36)
with a held-out split, and it measures the one thing hardest to measure by unit test: whether
a model turns Spanish into the right typed plan. `metamorphic.yaml` adds 19 invariance cases —
paraphrase in, same plan out — which is the right shape of assertion for a stochastic
component, because it needs no golden answer. The conversation corpus is 169 episodes over
~20 named groups (refusals, ellipsis, typos, corrections, topic-changes, suspended,
cancellations…) and covers multi-turn state, which no single-turn benchmark can. `ratchets.py`
is the best-designed thing in the test tree: a number that may only go down, carrying its own
unit, born from a ratchet that "lied in green" by counting turns where the assertion counted
sentences. And `evaluate_e2e.py` asserts *properties over the whole HTTP path* rather than
figures — "a COUNT plan is answered with a count" — which is exactly the class of check that
caught a defect passing 235 corpus cases and 163 unit tests.

**What they structurally cannot measure is anything requiring a second actor or a real
number.** Every one of those five instruments runs one holder against one seeded dataset, so
none can express containment; `evaluate_e2e.py` says so itself in its own docstring: "What it
cannot test is what a single seeded holder cannot express: cross-account containment needs a
second holder in the data." My §2.2 result closes the pincer — the unit suite cannot express
it either, since only 2 of 2925 tests observe authorisation and the guard on the live path is
`who.assert_may_read(who.account_id)`, a comparison of a value with itself. The second
structural gap is the *rendered value*: the corpora stop at the plan, and the unit tests
assert sentences that happen never to contain a negative amount, so `money()`'s sign, the
chart's sort order and the balance mapper's sign are all unobserved (§2.1, §2.5, §2.6). The
third is that 38% of banking's tests need a database, which means the invariant "the balance
comes from the entries" is asserted only in a tier that does not run by default — and the
arithmetic itself lives in a SQL view, where no Python test can reach it at all.

**The cheapest addition that closes the largest gap is a second holder in the seed, and it is
not close.** One extra account holder in the banking fixture plus roughly a dozen adversarial
e2e cases — holder B replaying holder A's metric token, holder B naming holder A's account
alias, holder B's credential against holder A's saved-metric id, an account id from A's
directory in a follow-up from B — converts the whole isolation question from unmeasured to
measured, and would have failed on mutation 2b today. It also makes the `llm_controller`
tautology visible, because a real second account id gives `assert_may_read` something to
compare against. Second, and cheaper still: a `tests/test_answer_writer.py` with direct
value assertions on `money()` across negative, zero, sub-euro and thousands-boundary inputs —
perhaps fifteen lines, and it kills the §2.1 survivor outright. Both are additions of *kind*,
not volume: against "we find out in production", the argument is that the suite's 2925 green
tests are not evidence about cross-account isolation or about the sign of a rendered figure,
because nine deliberate breakages of exactly those things left it green — and a number that
stays green when the code is wrong is not a measurement, it is a habit. The commissioner's
standard is met by adding the two smallest things the current design cannot express, not by
adding more of what it already does well.

---

## 7. Effort and coverage

All 9 numbered targets were reached, plus 6 self-chosen targets from the conversational path
(favouring the last 20 commits: `result_reference.py` and `_across_every_account` are from
commits landed today) and 2 harmless controls. 31 mutations total, 5 full-suite runs.

**Not reached, and named rather than glossed:**

- Banking's actual balance arithmetic (the SQL view) — no database available; see §2.6. Also
  unreached for the same reason: the 149 integration tests, `test_account_isolation.py`,
  `test_holder_isolation.py` and `test_query_reader_privileges.py`, which are precisely the
  files that *would* speak to §2.2 on the banking side. My §2.2 finding is scoped to the
  orchestrator's unit suite and should be read that way.
- `apps/voice-gateway` and `apps/web` — not in scope, not mutated, not measured.
- The provenance of the `focus` argument at `AFQ:5743` — I established that `focus_of` makes
  E2's guard tautological on every call site I traced, but did not exhaustively trace that
  one, so E2 is reported as "survived, probably a redundant second lock" rather than
  definitively either.
- `evaluate_planning.py` / `judge_plans.py` were read for §6 but not run: they call a model,
  and a stochastic run is not evidence in an audit this shape.

Every mutation in the table was reverted; `git status --porcelain` was confirmed empty before
this report was written. The only file this audit commits is the report.
