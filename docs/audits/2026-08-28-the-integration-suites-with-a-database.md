# What the integration suites prove when the database is actually there

Run 2026-08-28 at `0bf2db5` on `task/visualization`. Compose Postgres `18-alpine` up and healthy
on `127.0.0.1:5442`, containers at `dd1d2d0`, nothing built, started or restarted.

These are the suites excluded from `just test-unit` and therefore from the default gate. They are
the half of the test suite that nobody had run in this session, and one of them had never run in
any automated run at all.

Every number below is a command and its output. Where a claim in the brief that commissioned this
run turned out to be wrong about the repository, the repository is recorded and the claim is named.

---

## 1. The runs

```
uv run --directory apps/banking-api pytest -q -m integration
149 passed, 240 deselected in 10.86s
```

```
ORCHESTRATOR_TEST_DATABASE_URL=postgresql://finguard_orchestrator:finguard_orchestrator@127.0.0.1:5442/finguard_orchestrator \
  uv run --directory apps/orchestrator pytest -q -m integration
2 passed, 3004 deselected in 41.69s
```

Banking needed no environment at all: `bootstrap/config.py` already defaults `database_url` and
`query_database_url` to `127.0.0.1:5442`. `just migrate` was **not** needed — `alembic current`
reported `0018_card_charge_layers (head)` before the run, and the orchestrator schema was already
at `0001_saved_metrics`.

### The orchestrator has two integration tests, not one

The brief said one. `--collect-only -m integration` reports two:

| test | dependency | outcome |
| --- | --- | --- |
| `test_ollama_provider.py::test_a_real_daemon_honours_the_schema` | a local Ollama daemon | **passed** — the daemon was up on `127.0.0.1:11434` |
| `test_saved_metrics.py::test_postgres_repository_enforces_holder_rls_and_jsonb_roundtrip` | `ORCHESTRATOR_TEST_DATABASE_URL` | **passed**, for the first time ever |

Both ran. Neither skipped: the run reports `2 passed`, not `1 passed, 1 skipped`.

---

## 2. Cross-account containment: 19 tests, 19 pass

```
uv run --directory apps/banking-api pytest -q -m integration \
  tests/test_account_isolation.py tests/test_holder_isolation.py
19 passed in 1.55s
```

Six in `test_account_isolation.py`, thirteen in `test_holder_isolation.py`. The count of **19** in
the brief is correct and the `docs/next.md` figure of "four", carried since 2026-08-03, was not.
All nineteen are integration-marked, which is why the default gate never saw them.

### What a green isolation suite proves, and what it does not

This matters more than the pass, because migration `0016_holder_scope.py` argues against the
obvious reading in its own prose, and it is right:

> **What actually enforces isolation here, and it is not the policies.** `relforcerowsecurity` is
> false on every protected table and deliberately: 0007 took it off because row-level security
> applies inside trigger functions, and `assert_transaction_is_balanced` summed a transaction's
> entries, saw none, and would have accepted an unbalanced transaction in silence.

Verified against the live catalogue rather than taken on trust:

```
select relname, relrowsecurity, relforcerowsecurity from pg_class
 where relname in ('accounts','ledger_entries','contacts','payment_intents','cards');
```

`relforcerowsecurity` is **false** on all of them. `finguard_banking` owns those tables, and an
owner is exempt from every policy unless FORCE is on. So:

**What the 19 tests prove.** That the **privilege barrier** holds — `finguard_query_reader` has no
privilege on any base table, confirmed independently by
`test_account_isolation.py::test_the_reader_cannot_reach_the_base_tables_at_all` and by the three
tests in `test_query_reader_privileges.py` — and that the **read views' `WHERE` clause** is correct,
including in the fail-safe direction: a session that declares nothing sees nothing
(`test_a_session_that_declares_nothing_sees_nothing`), and a session that names a stranger's
account gets an empty answer rather than an error or their money
(`test_narrowing_to_a_stranger_finds_nothing_rather_than_their_money`,
`test_declaring_an_account_that_does_not_exist_reveals_nothing`).

**What they do not prove.** That row-level security protects the rows. The policies on
`accounts` and the eight tables in `0016`'s `SCOPED` tuple are **dormant** — 0016 says they exist
"for the day a migration grants table access, and not because they are guarding anything today".
No test in the suite exercises them, and none can while FORCE is off and the only role subject to
them cannot reach the tables. A green suite here means *the reader's one door is locked and the
lock is the right shape*, not *the room has two locks*.

**And what neither proves.** 0003 states it plainly and it survives this run unchanged: banking
does not authenticate its callers. `app.account_id` is set from what the request says. This stops a
bug in our own SQL compiler or HTTP adapter. It stops nothing on banking's network.

The one place FORCE **is** on is the orchestrator's own table — see §4.

---

## 3. The ledger arithmetic is verified, and this is the run that proves it

This was the most valuable thing in the run and the answer is good news.

The 2026-08-28 mutation audit's row 9a recorded that negating
`row_mapper.to_balance` — `Money(int(row["booked_minor"]))` → `Money(-int(...))`, which inverts
every reported balance — **survived all 240 banking unit tests**, because `to_balance` has no unit
test. The open question was whether anything catches it.

Reproduced both halves here without editing a single repository file, by injecting the mutation
from an out-of-tree pytest plugin on `PYTHONPATH` that rebinds the module attribute
(`row_mapper.to_balance = _negated`):

```
PYTHONPATH=<scratch> pytest -q -p negate_to_balance -m "not integration"
240 passed, 149 deselected in 0.59s          # survives, as recorded
```

```
PYTHONPATH=<scratch> pytest -q -p negate_to_balance -m integration
9 failed, 140 passed, 240 deselected in 10.55s
```

**The mutation is caught, by nine integration tests.** `balance_of` in
`ledger_repository.py:45` is the only caller of `to_balance`, and it is on the path of:

```
tests/test_balance_repository.py::test_a_debit_subtracts_and_a_credit_adds
tests/test_balance_repository.py::test_the_balance_accumulates_several_transactions
tests/test_balance_repository.py::test_what_is_not_booked_does_not_count
tests/test_internal_movements.py::test_the_balance_still_counts_it
tests/test_payments.py::test_preparing_moves_no_money
tests/test_payments.py::test_confirming_moves_it_once
tests/test_payments.py::test_confirming_twice_pays_once
tests/test_payments.py::test_a_payment_beyond_the_balance_is_refused
tests/test_payments.py::test_a_transfer_to_a_typed_account_needs_no_contact
```

### What the test actually asserts

`tests/test_balance_repository.py`, docstring *"The balance comes from the entries, not from a
column someone wrote"*, integration-marked at module level. Five tests, and they assert the
arithmetic rather than the plumbing:

- an account with no movements is `Money(0, "EUR")`, booked **and** available;
- **a debit subtracts and a credit adds** — one movement of 2500 minor units between two
  accounts, asserted as `-2500` on the sender and `+2500` on the receiver. This is the assertion
  that kills the sign mutation: it pins both signs from one movement, so inverting them swaps a
  matched pair and cannot pass;
- two movements accumulate to `Money(12_549, "EUR")`;
- a `PENDING` movement of 99_999 contributes **nothing** beside a `BOOKED` 5_000 — the view's
  `WHERE lt.status = 'BOOKED'` under test;
- an account that does not exist raises `AccountNotFoundError`, which is a different thing from an
  account at zero.

So the derivation is verified end to end: the Postgres view's
`COALESCE(SUM(t.signed_amount_minor), 0)`, the `CASE WHEN entry_type = 'CREDIT'` that gives
`signed_amount_minor` its sign, the `BOOKED` filter, and the Python mapper on top of it.

One correction to the brief: that arithmetic is **not** from migration 0015. `pg_get_viewdef` on
the live `assistant_read.account_balances_v1` matches the body `0016_holder_scope.py` installs
(`FROM assistant_read.my_accounts_v1`); 0015's version selects `FROM accounts` and was replaced.
The expression appears in six migrations, 0002 through 0016.

**The gap the unit suite has is real and is still open.** Nothing in `-m "not integration"` covers
`to_balance`. The default gate excludes the nine tests above, so on a machine without a database
the sign of every balance FinGuard reports is unverified.

---

## 4. `assistant.saved_metric`'s RLS policy ran for the first time, and passed

`ORCHESTRATOR_TEST_DATABASE_URL` is read in exactly one place —
`tests/test_saved_metrics.py:308` — and set in **no** file in the repository: not in
`compose.yaml`, not in the justfile, not in a conftest, not in `.env.defaults`, not in CI. Without
it the test calls `pytest.skip`. It has therefore skipped in every run since it was written, which
means `FORCE ROW LEVEL SECURITY` on `assistant.saved_metric` had **never** been executed by an
automated test.

The URL is derivable from `compose.yaml`'s `ORCHESTRATOR_DATABASE_URL` by substituting the
published host port for the compose-network service name:

```
postgresql://finguard_orchestrator:finguard_orchestrator@127.0.0.1:5442/finguard_orchestrator
```

**It passed.** And unlike banking, the assertion it makes is load-bearing:

```
async with pool.acquire() as connection:
    assert await connection.fetchval("SELECT count(*) FROM assistant.saved_metric") == 0
```

Three rows exist at that moment — two for `holder_a`, one for `holder_b` — and the connection
holding them sees zero, because it has not set `app.holder_id`. `finguard_orchestrator` **owns**
`assistant.saved_metric`, so that assertion is true only because FORCE is on. Confirmed:

```
 relname      | relrowsecurity | relforcerowsecurity
--------------+----------------+---------------------
 saved_metric | t              | t

 polname                       | polcmd | qual
-------------------------------+--------+------------------------------------------------------------------
 saved_metric_holder_isolation | *      | (holder_id = NULLIF(current_setting('app.holder_id'::text, true), ''::text))
```

So this is the one place in FinGuard where a green test genuinely means *the database will not
serve the rows*, rather than *the reader has no door to the rows*. The test also pins the positive
direction — `repository.get(holder_a, saved_a.id)` returns the row, `repository.get(holder_a,
saved_b.id)` returns `None`, and the two `list()` calls return disjoint id sets — so it would not
pass against a policy that refused everything. It cleaned up after itself: `assistant.saved_metric`
holds 0 rows now.

**This does not close the finding the brief describes.** The application-level guard that refuses a
single account the rest of the system serves, while `across_accounts=True` passes unchecked, is in
the orchestrator's Python and is untouched by an RLS test on a repository. Row 2b of the mutation
audit is the same surface and also still open.

---

## 5. A new failing test: a confirmation can redirect a payment

`migrations/sql/0006_assert_intent_transition.sql` says of the columns it compares:

> The amount, the recipient and the account are what the user agreed to. Confirming has no
> business changing any of them, and a bug that did would move a different payment than the
> one that was previewed.

It compares `account_id`, `contact_id`, `amount_minor`, `currency`. Those were every column a
destination had in 0006, when the only rail was Bizum. `0012_transfers.py` added `rail`,
`recipient_iban` and `recipient_name`; `0017_internal_transfers.py` added `to_account_id`. Neither
redefined the function. "The recipient" is no longer among the columns the guard checks.

Added `tests/test_a_confirmation_cannot_redirect_a_payment.py` — four tests, all integration-marked,
all asserting the behaviour the comment promises. **All four fail**, and are left failing:

```
FAILED test_confirming_cannot_change_the_iban_the_holder_approved
FAILED test_a_pending_intent_cannot_have_its_iban_rewritten
FAILED test_confirming_cannot_change_the_name_printed_beside_the_account
FAILED test_confirming_cannot_erase_the_contact_the_payment_came_from
4 failed in 0.73s
```

Each fails as `Failed: DID NOT RAISE PostgresError`. The trigger is silent.

Demonstrated in raw SQL as well, inside a rolled-back transaction, because a test that fails by
not raising deserves a second witness. The trigger is attached and does fire on this shape:

```
payment_intents_lifecycle BEFORE UPDATE ON public.payment_intents
  FOR EACH ROW EXECUTE FUNCTION assert_intent_transition()
```

```
== what the holder approved
        status         | recipient_name |      recipient_iban      | amount_minor
-----------------------+----------------+--------------------------+--------------
 AWAITING_CONFIRMATION | Elena Marin    | ES9121000418450200051332 |         3000

== one UPDATE confirms it and rewrites the destination
UPDATE 1

== what got paid
  status   | recipient_name |      recipient_iban      | amount_minor
-----------+----------------+--------------------------+--------------
 CONFIRMED | Somebody Else  | ES6000491500051234567892 |         3000
```

€30 approved to `ES91…`, paid to `ES60…`, to a different name, in the single statement that
confirms it. The status transition is legal, so the guard does not take its early return — it runs,
inspects its four columns, finds them unchanged, and returns `NEW`. Every check constraint still
holds: `payment_intents_destination` requires a `TRANSFER` to *have* an IBAN and
`payment_intents_iban_shape` requires it to look like one, but neither pins its **value**.

Two things the tests found that were not in the brief:

- **The guard does not run at all while the status is unchanged.** `IF NEW.status = OLD.status
  THEN RETURN NEW` is the first statement in the function, so an intent sitting in
  `AWAITING_CONFIRMATION` can have its IBAN rewritten with no guard whatsoever, and then be
  confirmed entirely legitimately. That is the wider of the two holes.
- **`contact_id` is checked and the check still misses NULL.** `NEW.contact_id <> OLD.contact_id`
  is three-valued: against `NULL` it is `NULL`, not `TRUE`, so the whole `OR` chain is `NULL` and
  the `IF` is not taken. Setting a favourite's id to `NULL` passes a guard written to catch
  exactly that column changing, erasing the only record of which contact a transfer was aimed at.

Not fixed. The fix is a migration redefining the function — it needs to name the four new columns
and use `IS DISTINCT FROM` rather than `<>` — and that is a change to the schema, not to a test.

---

## 6. The banking integration suite is not idempotent against the ledger

Not a failure, and not in the brief, but it changes what "the next e2e run measures the same
ledger" costs.

`tests/conftest.py` promises *"A connection whose work is undone at the end: tests leave no
trace."* One test opts out, for a good reason it states — what is under test is the database's
locking, which needs two committed connections:

`tests/test_payments.py::test_two_confirmations_racing_for_one_balance_pay_once`

Its cleanup deletes the `payment_intents` and `contacts` rows it made, and notes that *"the ledger
refuses DELETE on its own tables"*. So it cannot delete the rest. Measured across this run, each
execution of the suite permanently commits:

- 1 `customers` row and 1 `accounts` row (`ES-<12 hex>`, `CURRENT`, `ACTIVE`);
- 2 `ledger_transactions` and 4 `ledger_entries` — two on the new account, two on
  `ES-COMPENSACION`, whose balance moved from `-30125.94` to `-30145.94`.

Before the run: 6 accounts, 3356 entries. After: 7 accounts, 3360 entries, exactly as predicted.
The stray account was timestamped `20:31:56Z`, three seconds before the suite reported
`149 passed` at `20:31:59Z`.

`just seed` removes it, because the seed's `TRUNCATE` covers those tables. But it means
**`pytest -m integration` must be followed by `just seed`** if the ledger is a measurement
instrument, and nothing in the justfile or either README says so.

### One thing the brief got backwards

`test_the_dataset_can_be_reloaded.py` does **not** truncate or re-seed anything. All three of its
tests are non-destructive: two query `pg_constraint`/`pg_class` metadata, and the third reads
`seeds/0001_demo.sql` off disk and regex-matches its `TRUNCATE` **as text**. The suite's
destructiveness comes from the payments race test above, not from this file.

### `payment_intents`

The suite did not care. It held 10 rows before the run (6 `AWAITING_CONFIRMATION`, 4 `CANCELLED`)
and 10 after — no integration test reads pre-existing intents, and every test that makes one either
rolls back or deletes it. `just seed` then truncated them to 0, which is the seeded baseline.
Incidentally, `EXECUTED` is not a status this schema has: `payment_intents_status_check` admits
`AWAITING_CONFIRMATION`, `CONFIRMED`, `CANCELLED`, `EXPIRED`.

---

## 7. The state the database was left in

`just seed`, then `just seed-verify`. Byte-identical to the pre-run baseline:

```
== volume
NOTICE:  dataset is sound: balances per transaction, coherent currency, no orphans
 customers | accounts | transactions | entries
-----------+----------+--------------+---------
         4 |        6 |         1678 |    3356

== by status and period
 status  | transactions |   first    |    last
---------+--------------+------------+------------
 BOOKED  |         1675 | 2025-02-01 | 2026-07-28
 PENDING |            3 | 2026-07-31 | 2026-07-31

== balance per account, derived from the entries
 account_reference |        full_name        |        balance
-------------------+-------------------------+-----------------------
 ES-0001           | Laura Martin Ruiz       | 2270.4200000000000000
 ES-0001-A         | Laura Martin Ruiz       | 5485.5000000000000000
 ES-0001-F         | Laura Martin Ruiz       | 2700.0000000000000000
 ES-0002           | Diego Ferrer Gomez      | 1806.7400000000000000
 ES-0003           | Nuria Salas Ortega      |    17863.280000000000
 ES-COMPENSACION   | FinGuard (compensacion) |   -30125.940000000000
```

The stray account is gone, `ES-COMPENSACION` is back at `-30125.94`, `payment_intents` is empty and
`assistant.saved_metric` holds 0 rows. No container was built, started, stopped or restarted; the
only writes went through `docker exec`/`docker cp`, which is what `just seed` itself does.

---

## 8. What this changes in gate 1

The **Cross-account containment** row can be filled in, but not with the wording it had before
2026-08-28 and not with an unqualified ✅. 19 of 19 pass, and §2 is the honest reading of what that
buys. The row now says so, and names the privilege barrier and the views' `WHERE` as what is
actually under test.

Three findings remain open and none of them is a gate-1 row yet:

1. `assert_intent_transition` does not guard the destination (§5) — four failing tests in the tree.
2. `to_balance` has no unit test (§3), so the sign of every balance is unverified in the default
   gate even though nine integration tests cover it.
3. The banking integration suite accretes committed ledger rows (§6).
