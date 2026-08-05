# ADR-009 — Holdings, internal movements, and layered categorisation

**Status:** accepted, unimplemented
**Date:** 2026-08-04
**Numbering:** 008 is the language packs. 001–007 are in vision.md.

## Context

Two review comments arrived together and turn out to be one problem.

The first asks for crypto and stocks as categories, and for categorisation to become layered — some
of it immutable, some assigned by the holder, plus a taxonomy the holder builds and an
Obsidian-shaped graph over it. The stated payoff is real: holders labelling their own movements
would show us the categories we did not think of.

The second asks for a holder with several accounts — a current account, a piggy bank that earns
interest — with cross-account summaries and a breakdown of what might be deductible.

They meet at a defect that does not exist yet and that either of them creates.

## Decision

### 1. An asset is an account, not a category

Putting 500 € into bitcoin is not spending 500 €. As `Category.CRYPTO` under `Direction.EXPENSE`
it is, and then "¿cuánto gasté este mes?" is wrong by the size of the holder's investments — a real
figure answering a different question, which is the failure mode this product exists to prevent and
the one the last four commits were about.

The ledger is double-entry. An investment purchase is a movement from the current account to an
asset account, which is what double entry is *for*. What is missing is not vocabulary; it is
account kinds:

```
CURRENT   money to spend
SAVINGS   money set aside, possibly earning interest
ASSET     a holding whose value is not its cost — crypto, funds, shares
```

`ASSET` accounts carry a second problem this ADR does not solve and must not pretend to: a holding
is worth what the market says, not what was paid. Valuation needs a price source with a date, and
until there is one an asset account reports its **cost basis** and says so. A balance that looks
like a valuation and is not is worse than no balance.

### 2. A movement between the holder's own accounts is not an expense

The same defect, and the one multi-account creates on its own. Moving 200 € to the piggy bank is a
debit on one account and a credit on another; counted naively it is an expense of 200 and an income
of 200, and the monthly summary reports twice the movement that happened.

So the ledger distinguishes a movement that **leaves the household** from one that stays inside it.
Marked at write time, by the writer that created both legs, rather than derived at read time from
whether the counterparty happens to be another of the holder's accounts — the derivation needs the
account graph on every query, and the writer already knows.

Consequences, both of which are the point:

- `Direction.EXPENSE` excludes internal movements by default. "¿Cuánto gasté?" means money that
  left.
- Asking about them is a different question with a different answer shape: "¿cuánto he movido a la
  hucha este año?" is a real question and it is not a question about spending.

### 3. Category, tag and hierarchy are three structures

The single "permutable field" is the part of the proposal to refuse, because merging them breaks
something that works today.

| | answers | do totals sum? |
|---|---|---|
| **Category** | which one bucket is this in? Exactly one | yes — a breakdown reconciles with the total |
| **Tag** | which labels apply? Zero or many | **no**, and cannot |
| **Hierarchy** | which category contains which? | yes — a tree still partitions, so rollups work |
| **Graph** | what is related to what? | not a question about money |

`evaluate_e2e` already asserts `GROUPS_DO_NOT_SUM_TO_TOTAL`, because a breakdown whose parts do not
add up to the whole is worse than no breakdown: it looks like proof. Route tags through the same
grouping mechanism and that check starts failing on correct answers, and a check that cries wolf is
a check somebody switches off.

Grouping by tags is allowed and answers differently — the response says it is not exhaustive, and
the prose says so too: *"estos son los movimientos etiquetados; el resto no lleva ninguna"*.

**On the Obsidian shape.** Obsidian's graph is for browsing notes. Money needs arithmetic, and a
free graph gives no sums. What actually delivers "watch my expense distribution grow" is the
holder's own **tree** — `Ocio > Cine`, `Ocio > Videojuegos` — because a tree partitions and totals
roll up. Co-occurrence between tags is a visualisation on top of that, not the model underneath it.

### 4. Four layers, and only one of them is immutable

```
bank_category     what the bank said.       Immutable.
system_category   what FinGuard infers.     Recomputable — a better classifier rewrites this
                                            column and touches nothing else.
user_category     the holder's override.    Wins over both above.
user_tags         free labels.              Zero or many, outside the grouping mechanism.
user_taxonomy     the holder's own tree.    What rollups are computed over.
```

`bank_category` is immutable for one reason and it is the product's foundation: it is the only
layer that can be audited against a statement. The moment it is editable, "cada cifra viene de tu
banco" stops being true.

The effective category of a movement is `user_category ?? system_category ?? bank_category`, and
the answer can say which layer it used — the explainer already answers "¿en qué categoría has
clasificado esto?" and this gives it a better answer.

### 5. Several accounts per holder

The schema already allows it: `accounts.customer_id` exists and a second account is an `INSERT`.
What is single-account is the security boundary and the identity:

- **Twelve RLS policies** keyed on `account_id = current_setting('app.account_id')`. They become
  "the accounts this holder owns", and this is the change where a mistake shows somebody else's
  money. It gets its own migration and its own cross-holder test, and no other change rides with it.
- **`Caller.account_id`**, singular with a comment that already anticipated this.

And a conversational consequence that is not in either comment: **"¿cuánto tengo?" stops having one
answer.** The default is the total with a per-account breakdown under it, which is what a bank app
does; naming an account narrows it. That is a new slot, not a new sentence.

#### Correction, after reading the database rather than the migration titles

The paragraph above was written believing the twelve policies were what enforces isolation. They
are not, and the difference changes the order of this work.

`relforcerowsecurity` is **false** on every protected table, and deliberately: migration 0007 took
it off and explains why at length. Row-level security applies inside trigger functions, and every
integrity check in this ledger reads the table it protects — `assert_transaction_is_balanced` sums
a transaction's entries, saw none under RLS, and **zero balances**, so an unbalanced transaction
would have been accepted in silence. The owner is exempt so those triggers can do their job.

So the policies are correct, in place, and dormant. What actually enforces isolation today is a
pair the tests already name:

    the privilege barrier   `finguard_query_reader` cannot reach the base tables at all
    the views' WHERE        `assistant_read.*` filters on the declared account

That makes the multi-account change **smaller and differently shaped** than "rewrite twelve
policies". The load-bearing edit is the views' filter; the policies are updated in the same commit
to stay correct for the day a migration grants table access, and not because they are guarding
anything today. Claiming otherwise in a commit message would be describing a second line of defence
that does not exist.

Two consequences for how it is done:

- **The declared identity becomes the holder.** `app.account_id` narrows; `app.customer_id` scopes.
  A policy that resolved the holder from the account would have to read `accounts` from inside a
  policy on `accounts`, which recurses, so the holder is declared by the caller rather than derived
  in SQL — and that means banking's read path learns who is asking instead of inferring it.
- **FORCE stays off**, and any attempt to turn it back on has to answer 0007 first. The silent
  failure it describes is the exact shape a rushed RLS change reintroduces.

### 6. What may be learned from holders' labels

The idea is good and one detail sinks it if it is seen late. A free-text tag on a bank movement is
written by a person about their own life: *"abogado divorcio"*, *"tratamiento"*, *"regalo Ana"*.
That is not anonymised by stripping identifiers, and some of it is special-category data.

So the export is the **mapping between closed vocabularies** and nothing else: "the system said
`GROCERIES`, the holder moved it to `HOGAR`", plus counts. No free text, ever, at any aggregation.
That still answers the question the idea was for — which categories we are missing and how holders
actually group their spending — and it exports nobody's life.

On top of that, unchanged from the earlier review: explicit consent, a declared purpose, bounded
retention, opt-out, and operational data kept apart from any training set.

### 7. A deduction breakdown is a fact; a deduction suggestion is advice

`capability_guard` refuses advice by name — *"No doy consejos sobre tu dinero"* — and that refusal
is regulatory rather than stylistic. The line runs through the middle of this request:

- *"En 2026 llevas 1.240 € en la categoría SALUD"* — a fact, labelled as one. Fine.
- *"Puedes deducirte estos gastos"* — tax advice. Refused, by the family that already exists.

Choosing that line on purpose is the decision; letting a prompt choose it is how the refusal stops
being a control.

## Order of work

1. **Internal movements.** Correctness, and it is small *before* the seed grows and expensive after.
2. **The multi-account seed.** Data first — a current account, a savings account with interest, an
   asset account, transfers between them — so the questions that become ambiguous are visible
   before anybody decides how to answer them. No RLS change yet.
3. **The categorisation layers.** Migration, effective-category resolution, and a tag breakdown that
   says when it does not reconcile.
4. **RLS and identity.** Its own migration, its own cross-holder test, nothing else in the commit.
5. **The holder's taxonomy and its rollups.**
6. **The label export**, behind consent, closed vocabularies only.

Asset valuation, the co-occurrence visualisation and anything about tax are not scheduled here.

## Consequences

**Good.** "How much did I spend" keeps meaning what it says once there is more than one account.
Categories keep reconciling. A better classifier becomes a column rewrite rather than a migration
of everybody's corrections. The learning idea gets a shape that can be shipped.

**Costs.** Four migrations and a security-boundary change. Account kinds and internal movements
touch the read view every query goes through. And a rule to hold: any new classification has to
declare which of the four structures it is before it gets a column.

**Not decided here.** Where asset prices come from, whether a taxonomy is per holder or shareable,
and whether internal movements are hidden from the movement list or merely excluded from totals —
that last one is a product question and worth answering with a screen in front of somebody.
