# Phase 4 — `task/phase4` → `develop`

Written for somebody deciding whether to merge, not for somebody reading 282 files. It says plainly
what is finished, what is deliberately not, and what is known to be wrong.

## What this is, honestly

```
282 files · 152 commits
production  +20 984      tests  +14 476      docs  +1 302      generated  +7 496
```

**It is too big, and splitting it now would cost more than it buys.** The dependencies run deep —
the client needs the orchestrator's endpoints, the orchestrator needs banking's, the seventeen
migrations are ordered — so a split means rebasing 152 commits and revalidating each piece against
a database. What makes it reviewable instead is the ordering: every commit is atomic and was
verified on its own, so `git log -p` reads as a sequence of decisions rather than a snapshot.

The lesson is taken for the next branch, not retrofitted onto this one.

## How to review it in an hour

Read these five, in this order. They are where the decisions are; the rest is their consequences.

| Read | Why |
|---|---|
| `docs/architecture/adr-009-holdings-and-categorisation.md` | Every multi-account decision, including the ones refused |
| `migrations/versions/0016_holder_scope.py` | The security boundary, and why the RLS policies are dormant |
| `domain/services/explicit_facts.py` | What the model is not allowed to contradict |
| `domain/services/answer_writer.py::balance` | The shape of every "which accounts" answer |
| `tests/test_holder_isolation.py` | The only file where a mistake shows somebody else's money |

Skip `seeds/0001_demo.sql` (+5 300, generated), `package-lock.json` and the evaluation `RESULTS.md`.

## What it does

**A conversation that keeps its context and does not invent it.** The state is one tagged union the
client round-trips without interpreting; a payload whose tag disagrees with its fields is a 422
rather than a silent idle. What a sentence states outright — its window, its shape — is enforced
over the model's opinion, so "los últimos 3 meses" is never answered over five.

**A holder has several accounts, and every figure says which.** Isolation moved from "the account
you declared" to "the accounts you hold", written once in `my_accounts_v1` and filtered through by
all five read views. "¿Cuánto tengo?" answers with the total and a breakdown; naming an account
narrows it; a question naming none now covers all of them.

**Money moves between your own accounts**, over a third rail with its own destination shape, its own
transaction type and no clearing account — because it never leaves the household. From a screen and
from a sentence.

**Transfers to an account number**, with the IBAN as a separate field the model never reads and
never writes, validated by mod-97 at the edge.

**Four layers of classification.** `bank_category` immutable, `system_category` recomputable,
`user_category` the holder's, tags outside the grouping mechanism entirely — and `category_source`
carries which one decided, all the way to the row on screen.

**A client that shows all of it**: three accounts, what a payment is about to do, a paused payment,
where a category came from, and money that never left the household.

## The rule this branch is built around

A wrong figure is not the worst outcome. **A real figure answering a different question** is, because
nothing contradicts it — and most of what is here exists to make one impossible:

- An investment balance is a cost basis, so it is never inside a figure labelled "tu dinero", and
  the client and the assistant compute that line the same way, to the cent.
- A transfer to the piggy bank is not spending, so "¿cuánto gasté?" does not move when you save.
- A total across two currencies does not exist, so none is printed.
- A question about interest is answered about interest, or refused — never with income.
- `capability_guard` owns refusals; a model claiming a limit the guard does not have is overruled,
  because "no puedo" about something the product does is a wrong answer nobody can check.

## Database

Seventeen migrations, `0003` through `0017`. Three matter to a reviewer:

- **`0007`** removed `FORCE ROW LEVEL SECURITY`, and everything about isolation follows from it. RLS
  applies inside trigger functions, so `assert_transaction_is_balanced` summed zero entries, found
  zero, and would have accepted an unbalanced transaction in silence. The policies are therefore
  **correct, in place, and dormant**; what enforces isolation is the privilege barrier plus the
  views' WHERE. Any commit claiming otherwise would be describing a defence that does not exist.
- **`0016`** put the scope rule in one place. `assistant_read.holder_of` is `SECURITY DEFINER` with a
  pinned `search_path`, which is the narrowest hole that lets a role with no table privileges ask
  who owns an account.
- **`0017`** lets an intent point at another account of the same holder. The foreign key says the
  account exists; it does not say it is yours, and the command checks that against the view.

All round-trip down and up.

## Deliberately not done

Named so a reviewer does not have to guess whether they were forgotten.

- **The holder's own taxonomy and the graph.** ADR-009 decides the shape — a tree partitions and
  totals roll up, a free graph answers no question about money — and none of it is built.
- **The label export.** Consent, closed vocabularies only, no free text ever. Decided, unwritten.
- **A deduction breakdown.** ADR-009 §7 draws the line between a fact and advice; neither side is
  implemented.
- **Language packs.** ADR-008 exists; the Spanish semantics come first, and moving text while it is
  still changing means not knowing which change broke what.
- **No FX.** Multi-currency is handled by refusing to add across currencies, not by converting.
- **`GroupBy.COUNTERPARTY`.** "¿A qué personas les he hecho Bizum?" groups on `MERCHANT`, which is
  the column the names are in. Right answer, wrong vocabulary, marked with a `ponytail:` comment.
- **Structural cleanups.** `llm_controller.py` is 906 lines, `banking_dto.py` holds every DTO,
  `application/__init__.py` is a reexport list. All three grew on this branch. They change no
  behaviour, and splitting them while features are still landing guarantees conflicts — so they are
  one refactor commit after the demo, with the suites as the net.

## Known wrong

- **The card routes bin in the deployment's zone, not the holder's**, and there are two defects
  behind it. `http_cards` recomputes `days` from `date.today()`, which undoes the holder's day the
  orchestrator had already resolved; and banking's card queries default `reporting_timezone` to the
  deployment's. A holder in the Canaries sees the chat on their day and their card charges on
  Madrid's. The conversation and movement paths are correct.
- **`card_charges_v1` does not read `movement_classifications`**, so a reclassification reaches every
  figure except the card screen's.
- **Nothing writes `system_category` in production.** The table exists and only the dataset generator
  fills it, so the SYSTEM layer is empty outside the demo.
- **A draft-less clarification can still loop.** Loop detection reads the slot off the active
  workflow, and a clarification with nothing half-built behind it leaves no state to compare.
- **`prepare` idempotency is generated server-side**, so a browser retrying a request it never saw
  the answer to can create a second intent. No money moves; orphan previews are possible.
- **A bare "hoy" outranks an unrecognised month name.** "Hoy quiero saber cuánto gasté en junio" is
  read as today, because the extractor does not know `en junio` without a year.

## Security guarantees

- The account is never in a request body. It comes from the credential, so there is nowhere to say
  whose money you mean.
- `payments:prepare` and `payments:confirm` are separate scopes: a credential that could do both
  would make the human confirmation step a formality.
- SQL from the model is never executed. The shape comes from enums and every outside value is a
  parameter.
- Logs carry no question text, no Bizum concept, no contact name or phone, no movement description
  and no raw account id. A test asserts it.
- An account that is not the holder's is *absent*, never refused — the same answer as one that does
  not exist, which is all a caller probing should learn.

## Evidence

```
banking-api    325 tests
orchestrator  1 417 tests
web              38 tests
query engine     15/15 (100 %) execution accuracy, phase 2
migrations     0003 → 0017 up and down
ruff + mypy    clean in both services
```

Driven end to end against the running stack, not only against fakes: 120,50 € moved between two
accounts with spending unchanged at 677,11 €, another holder's account refused, a Bizum previewed
and confirmed, and the multi-account balance read out in the chat.

**CI does not run on this branch.** It was narrowed to `develop` → `main` because ten jobs per push,
four of them on Windows runners at double rate, was spending a real slice of the monthly allowance
on a question `just check` answers locally. Run it here with **workflow_dispatch** before merging;
that is the automated evidence this description cannot be.

## Merge checklist

- [ ] `workflow_dispatch` run of `ci` against this branch, green
- [ ] `just db && just check` locally, green
- [ ] `just seed && just evaluate` still 15/15
- [ ] The six "known wrong" items opened as issues, so merging is a decision and not an oversight
- [ ] `docs/next.md` updated with what the next branch starts from
