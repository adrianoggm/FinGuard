# Scaling the language surface

**Date:** 2026-08-30 · **Scope:** `apps/orchestrator` · **Status:** approved design, being built

## The problem this exists to fix

The only answer this architecture has to "the model got it wrong" is "add a deterministic rule",
and that does not converge. Four fixes on 2026-08-29 were all the same shape — a pattern the reader
did not know:

| fix | what it actually was |
| --- | --- |
| `window_words` learns «desde el lunes» | two new patterns |
| a follow-up inherits its window | one new rule |
| `CUMULATIVE` with no buckets | one pair settled |
| «me he dejado» is spending | one new pattern |

All four are correct. All four improved the measured set. **None of them says anything about the
sentences nobody has written down yet.**

The evidence that this is structural, not anecdotal:

- **`metamorphic` scores 73.7 %, the worst of the five sets** — and that set exists for exactly one
  purpose: to check that saying the same thing differently produces the same plan.
  `metamorphic_exclusion` is 6/10.
- **`drift` is the only genuinely blind set** — the harness says so in its own help text: *"the only
  one nobody here wrote: ten questions from dev, paraphrased by a model that had never seen the
  prompt"*. It still fails `drift_last_month_6` and `drift_income_6`: «el mes pasado» resolved as
  the month in progress. The commonest phrasing of the commonest question, paraphrased, after a day
  spent fixing that exact family.
- **19 hand-written readers, consulted in source order, with no memoisation and readers calling
  readers** — `_direct_period_comparison` is asked five times in one turn, four of them on an
  ordinary spending question. The standard remedy for a defect is to add the twentieth.

## The principle, which this codebase already contains in one place

The direction fix surfaced it. `explicit_facts` deliberately does **not** impose direction, because
`plan_verifier` owns it: a reader that forced `EXPENSE` would have repaired the disagreement before
the verifier saw it, silently neutralising a live prompt-injection control.

> **A reader that *authors* a plan is a rule that must be maintained forever and is never complete.
> A reader that *verifies* one is a test that is cheap to add and can only refuse.**

The 19 recognisers author. `plan_verifier` verifies. The verifier does not need to know every way of
saying something — only how to detect a contradiction. The author needs all of them, which is why it
does not converge.

**The direction of travel is to move weight from the author column to the verifier column.** Not to
zero: authors buy latency and the 21.7 % of turns that never reach the model. But today that split
is *emergent* — nobody chose it — and that is the part that is wrong.

## Section A — the cascade becomes a registry

Today: 19 `@_recogniser`-decorated functions tried in the order they appear in a 7,975-line file.
Nobody can say why `_ratio_request` precedes `_gambling_plan`, and adding the twentieth means
choosing a position in a list where position is load-bearing.

The decorator's own docstring has to **argue in prose** about which nested readers can double-claim.
That should be a checkable property.

**Each reader declares a precondition** — what must be true of the sentence for it to be worth
asking — and the registry asks only those whose precondition holds. What that buys:

- order **derived** from specificity rather than from source order
- a test that two readers cannot claim the same sentence, which today is discovered when a corpus
  turn goes wrong
- memoisation is trivial, and five consultations become one
- the 7,975-line file loses its largest tenant

**Two steps, and the first must land alone.** Step A1 introduces the registry and registers the 19
readers *where they are*, proving the chosen reader is identical to today's for every sentence in
the datasets and the 1,167-turn corpus. Step A2 moves them into modules — pure code movement over a
proven-equivalent base. A refactor and a behaviour change in one diff is how a regression becomes
unattributable.

## Section B — the lexicon becomes data

The Spanish knowledge lives as regexes scattered across nine modules, and **every defect fixed
yesterday was one module knowing a phrase another did not**: `_EXPENSE` and `_INCOME` matched the
same words and nobody knew; `_CUMULATIVE` knew «acumulado» and not «acumulo»; `window_in` knew
«entre el 10 de mayo y el 20 de mayo» and not «entre el 10 y el 20 de mayo».

Measured, so the scope is not a matter of taste:

| module | lines | regexes | capture groups | |
| --- | ---: | ---: | ---: | --- |
| `explicit_facts.py` | 774 | 29 | 1 | almost pure vocabulary → **lexicon** |
| `payment_words.py` | 356 | 14 | 1 | lexicon |
| `account_words.py` | 370 | 10 | 4 | lexicon |
| `category_words.py` | 283 | 2 | 0 | lexicon |
| `continuation_patch.py` | 292 | 3 | 0 | lexicon |
| `intent_router.py` | 519 | 23 | 61, **none read** | lexicon — **the table above missed it**, and it is the largest single win: 168 phrasings, 519 → 298 lines |
| **`window_words.py`** | 914 | 33 | **29** | a date grammar → **stays code** |

**The lexicon is for words. Structure stays in code.** `window_words` is 29 capture groups reading
days, months and years; turning that into a table would trade readable code for a DSL, and the
second thing anyone would have to build is a parser for it.

**A correction to the measurement above, from doing it.** The capture-group column is the wrong
count on its own. `intent_router` holds **61** groups, not zero as this table first said — they are
`(` written where `(?:` was meant, inert and never inspected. What decides whether a pattern can
become data is not whether it *has* a group but whether one is **read**: `explicit_facts` reads one,
`intent_router` reads none (`.group(`, `.groups(`, `.groupdict(` all absent, now asserted by a
test). On that count the router is the purer case, which is what the first reading claimed for the
wrong reason.

**And a criterion that turned out to be narrower than it looked.** B1 kept two patterns as code
because an alternation's order was load-bearing through backtracking. That test finds nothing in the
router: with no group read anywhere, no alternative's win is observable. A pattern consulted only for
truth can always be a row.

What a table buys that nine regex files cannot:

- **what the system knows becomes enumerable.** Today it takes reading nine files.
- **adding a phrasing is reviewable by a Spanish speaker**, not only by someone who reads regex.
- **the paraphrase tests in Section C can be generated from it**, rather than written by hand.

That last one is the point. You cannot measure the coverage of a pile of regexes. You can measure
the coverage of a lexicon.

## Section C — paraphrase, generated and gated

`drift` is **ten questions** paraphrased by a model. Exactly the right idea at one per cent of the
right scale.

Every dataset case gets N paraphrases, generated **offline** by a model that never saw the prompt
and committed as data — CI cannot depend on a GPU, and a set regenerated per run is a flaky gate.
The metamorphic property then runs over all of them: a local rewording produces only the declared
plan delta.

This is the only one of the four that **converges**, because it attacks the space rather than
enumerating it.

**It earns a gate row of its own.** `metamorphic` at 73.7 % is the figure that predicts behaviour
with real users; dev at 5.8 % measures the sentences already known. A robustness number belongs
beside `Unsafe execution` in gate 1, not inside a per-group table.

**And the gate needs a companion.** `Unsafe execution ≤ 10 %` falls when the system refuses:
adversarial is 8.3 % unsafe at 77.8 % accuracy, metamorphic 0.0 % at 73.7 %. The two hardest sets
have the best safety numbers *because they fail by saying no*. A system that grew more timid would
improve gate 1. That incentive needs bounding.

## Section D — the split becomes a contract

Only possible since the observability work landed. It already measures the split: 21.7 % of turns
never reach the model, `enforce` overrides 21.4 % on traffic that stresses direction, `intent`
8.6 %. **Nobody chose those numbers; they happened.**

Each guard declares the band its override rate is expected to sit in, and leaving the band is a
signal. Out of band means either the model changed under us or the reader went stale — the two
things that have bitten this month.

## Order, and why

| wave | sections | why |
| --- | --- | --- |
| 1 | **A1** registry · **C** paraphrase | independent; A1 unblocks everything |
| 2 | **A2** extraction · **B** lexicon | both need A1's registry to plug into |
| 3 | **D** bands | needs a baseline over traffic |

**A before B**, even though B is the larger lever: A1 is a refactor with no behaviour change, and it
creates the place the lexicon plugs into. Doing B first would move regexes around inside a file that
has to be split anyway.

**If only one of the four ever gets built, build C.** The other three improve the code. C is the only
one that tells you when you are finished.

## What this design refuses

- **No probabilistic guards.** This codebase avoids threshold-based rules deliberately, and a guard
  that sometimes fires is a guard nobody can reason about. The improvement is moving work to
  verification, not attaching confidence to authorship.
- **No rule engine.** Section A's registry is a table and a derived order, not a DSL. The moment
  somebody must learn a language to add a reader, a coverage problem has been traded for a tooling
  problem.
- **No prompt changes for any of this.** `RESULTS.md` documents that path failing to generalise
  twice.
- **No shared package across services.** This lives in the orchestrator, which is the only service
  that has a language surface.
