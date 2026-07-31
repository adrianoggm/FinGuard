# banking-api

Deterministic financial core. No AI. Local port `8001`.

The only service allowed to write financial data: accounts, double-entry ledger, balances, movements, contacts, payment intents, simulated Bizum, idempotency, financial policies and domain event publishing.

It knows nothing about the LLM or the conversational context.

Public contract: [`packages/contracts/openapi/banking-api.yaml`](../../packages/contracts/openapi/banking-api.yaml) (pending).

## Layers

```text
domain/          value objects, models, ports, errors — no framework, no pydantic
application/     use_cases/
infrastructure/  controllers/ · dto/ · mappers/ · persistence/ · evaluation/ · error_handlers.py
bootstrap/       composition root
```

The domain raises `DomainError` subclasses with a `code`, never bare `ValueError`, and [`error_handlers.py`](src/finguard_banking/infrastructure/error_handlers.py) maps that code to a status in one place: not found → `404`, invalid input → `422`, currency clash → `409`.

Amounts travel as [`Money`](src/finguard_banking/domain/value_objects/money.py) — an amount and its currency together — so nothing in Python can add euros to dollars. The database enforces that per transaction at COMMIT; `Money` enforces it where the arithmetic happens. On the wire it is flattened back into `amountMinor` + `currency`, because that is what the §13 contract says.

Limits come in two flavours: [`domain/constants.py`](src/finguard_banking/domain/constants.py) holds the **hard ceilings**, which configuration cannot raise, and `BANKING_MAX_*` holds the **policy** a deployment applies, always clamped to the ceiling. A dataclass invariant is evaluated when the object is built, so it cannot read runtime configuration — hence the split. `BANKING_MAX_GROUPS=5000` narrows nothing; `=10` does.

## Running it

```bash
just up              # PostgreSQL, if it is not already running
just install         # uv sync
just serve-banking   # http://127.0.0.1:8001
just check           # ruff + mypy + pytest
```

| Endpoint | What it does |
| --- | --- |
| `GET /health` | Confirms the process answers. Does not look at dependencies |
| `GET /ready` | Confirms PostgreSQL answers. `503` if it does not |
| `GET /v1/accounts/{id}/balance` | Balance derived from the ledger. `404` if the account does not exist |
| `POST /v1/financial-queries` | Runs a validated query plan. `422` if the plan does not fit the contract |

## Balance

The balance **is computed by adding up the entries**, never read from a mutable column: the ledger is the source of truth (ADR-001). Only entries of `BOOKED` transactions count.

The sign is the account holder's: a `DEBIT` on their account takes money away, a `CREDIT` gives it to them. That is the criterion of the Bizum example in the spec, where sending €25 is recorded as a debit on the sender's account.

```
GET /v1/accounts/{id}/balance
{"accountId":"…","availableMinor":146035,"bookedMinor":146035,"currency":"EUR","asOf":"…"}
```

`available` and `booked` match today and will stop matching in phase 5, when there are amounts held by operations awaiting confirmation. `balance_projections` exists but is **unused**: it earns its place in phase 5, when a row has to be locked with `FOR UPDATE` to charge without race conditions.

## Financial queries

The body of `POST /v1/financial-queries` is the **intermediate representation** from [vision.md §9.2](../../docs/vision.md), never SQL. The request DTO sets `extra="forbid"`: a field it does not recognise — `"sql"`, say — is a `422`, not something quietly ignored.

```jsonc
{
  "accountId": "…",
  "direction": "EXPENSE",              // EXPENSE | INCOME | ALL
  "categories": ["TRANSPORT_FUEL"],    // closed pattern, at most 20
  "dateRange": {"from": "2026-07-01", "to": "2026-07-31"},
  "groupBy": "WEEK",                   // NONE | DAY | WEEK | MONTH | CATEGORY | MERCHANT
  "supportingLimit": 3,                // evidence movements, at most 100
  "explain": true                      // returns the compiled SQL and its parameters
}
```

[`query_compiler.py`](src/finguard_banking/infrastructure/persistence/query_compiler.py) turns that plan into SQL: **the shape comes from enums** defined in code and **every value travels as a parameter**. You can see it with `"explain": true`:

```sql
SELECT to_char(date_trunc('week', t.occurred_at AT TIME ZONE $1), 'YYYY-MM-DD') …
  FROM assistant_read.transactions_v1 t
  WHERE t.account_id = $2
    AND t.occurred_at >= (($3::date)::timestamp AT TIME ZONE $1)
    AND t.occurred_at <  (($4::date + 1)::timestamp AT TIME ZONE $1)
    AND t.direction = $5 AND t.category = ANY($6)
```

Decisions that change the result:

| Decision | Why |
| --- | --- |
| Spending comes back **positive** | "I spent 34.20", not "-34.20". With `direction: ALL` the total is signed, because it is a net figure |
| The final day of the range is included **in full** | Comparing with `<= to` would drop everything after midnight |
| Grouping happens in `Europe/Madrid` | Configurable with `BANKING_REPORTING_TIMEZONE`. Under UTC a purchase at 23:30 would land on the next day |
| Only `BOOKED` counts | Same as the balance |
| The movements backing the figure come back with it | The sum has to be checkable by hand (§2.1) |
| The trace is opt-in | It exposes the SQL; useful for the report, not for normal answers |

### Relative periods

Instead of `dateRange` you can send a `period`, and the server resolves it. The LLM **names** the period, it never computes dates: calendar arithmetic inside a generative model is where silent off-by-one-month errors come from.

```jsonc
"period": {"period": "LAST_MONTH", "reference": "2026-07-15"}   // reference is optional
```

| Period | Range (reference = Wednesday 2026-07-15) |
| --- | --- |
| `TODAY` / `YESTERDAY` | 07-15 / 07-14 |
| `THIS_WEEK` | 07-13 → 07-15 — Monday to today, not the whole week |
| `LAST_WEEK` | 07-06 → 07-12 — complete |
| `THIS_MONTH` | 07-01 → 07-15 |
| `LAST_MONTH` | 06-01 → 06-30 — complete |
| `THIS_YEAR` | 01-01 → 07-15 |
| `LAST_N_DAYS`, `n` | today and the `n-1` days before it |
| `LAST_N_MONTHS`, `n` | from the 1st of `n-1` months ago, current month included |

`reference` pins what "today" means; without it the current date in the reporting time zone is used. [`resolve`](src/finguard_banking/domain/relative_period.py) is a pure function — the clock lives at the edge — so its tests cover month ends, leap years and year boundaries without depending on when they run.

Caps: 500 groups and 100 evidence movements per query (§8.6). Not implemented yet: `comparison` (comparing against the previous period) and the `AVG` metric. `metric` travels in the plan because it is part of the contract, but `SUM` and `COUNT` are both always returned: the client reads the one it asked for.

## Schema

Alembic migrations in [`migrations/`](migrations/). The URL comes from `BANKING_DATABASE_URL`, never from `alembic.ini`, so migrations and the service cannot end up pointing at different databases.

```bash
just migrate                  # alembic upgrade head
just migration "contacts"     # a new empty migration
```

The trigger bodies live in [`migrations/sql/`](migrations/sql/) rather than inside Python strings: they are the most delicate logic in the repository and deserve syntax highlighting and a linter. Alembic still owns the versioning, the `downgrade` and `alembic_version`.

`0001_ledger` creates `customers`, `accounts`, `ledger_transactions`, `ledger_entries` and `balance_projections` with the DDL from [vision.md §8.5](../../docs/vision.md), and leaves **two invariants enforced by PostgreSQL**, not by the code that writes:

| Invariant | How it is enforced | ADR |
| --- | --- | --- |
| Every transaction balances, and balances **per currency** | Deferred `CONSTRAINT TRIGGER`, at COMMIT | ADR-003 |
| An entry is never modified or deleted | `BEFORE UPDATE OR DELETE` trigger | ADR-002 |

An entry also cannot be in a currency other than its account's: 2,500 debited in USD against 2,500 credited in USD balances arithmetically, but if both accounts are in euros it is not a valid transaction.

Only `DELETE` is blocked on `ledger_transactions`: their `status` does change, for instance when moving to `BOOKED`. Undoing a movement means recording a compensating transaction.

## Read path

Nothing that is queried reads the tables. `0002_read_views` creates two views in the `assistant_read` schema (§8.6):

| View | What it gives |
| --- | --- |
| `transactions_v1` | One entry per row, already interpreted: `direction`, positive `amount_minor`, `signed_amount_minor`, `category`, `merchant_name`. `BOOKED` only |
| `account_balances_v1` | Balance per account. `LEFT JOIN`, so an account with no movements shows up at zero and one that does not exist does not show up |

The views are also the **only place** where two money rules live that used to be repeated across three different SQL statements: what counts as booked, and which sign each entry carries.

The service opens **two pools with different roles**: `finguard_banking` to write and check health, and `finguard_query_reader` (`BANKING_QUERY_DATABASE_URL`) for everything that is queried. That role:

- only has `SELECT`, and only on those two views;
- **cannot even see that the tables exist** — without `USAGE` on `public`, `SELECT ... FROM accounts` answers *"relation does not exist"*, not "permission denied";
- runs with `default_transaction_read_only`, `statement_timeout = 3s` and `idle_in_transaction_session_timeout = 10s`.

The read path being unable to write **does not depend on this code being correct**. That is checked by [`test_query_reader_privileges.py`](tests/test_query_reader_privileges.py), against the real database, and by [`test_read_path_only_uses_views.py`](tests/test_read_path_only_uses_views.py), which verifies that no statement on the read path names a table.

Still **not implemented**: per-holder RLS. Filtering by customer requires an authenticated identity, and until one exists (§12) the application itself would have to inject it, which is precisely the control RLS is supposed to impose on it.

> The role is created in [`init/02-query-reader.sql`](../../infrastructure/postgres/init/02-query-reader.sql), which only runs when the volume is empty. On an existing database: `just reset && just migrate && just seed`.

## Development data

The dataset is a **versioned artefact**, not something each machine generates differently: future tests, the phase 2 benchmark and the demo all work on exactly the same movements.

```bash
just seed            # loads seeds/0001_demo.sql and verifies it. CAREFUL: starts with a TRUNCATE
just seed-verify     # checks whatever is loaded right now
just seed-generate   # regenerates the SQL
```

[`seeds/0001_demo.sql`](seeds/0001_demo.sql) carries 1,624 transactions and 3,248 entries: 3 account holders with salary, rent, subscriptions, groceries, fuel, restaurants, transport, health, leisure and Bizums between 2025-02 and 2026-07, plus the clearing account that acts as counterparty for everything.

| Property | Why |
| --- | --- |
| Deterministic | Same seed → same file, byte for byte. Regenerating and finding an empty `git diff` is the proof |
| `category` and `merchant_name` in `metadata` | It is what the phase 2 engine queries (§9.2) |
| 3 `PENDING` transactions | So it can be checked that the balance ignores what is not booked |
| Fixed dates | Reproducibility. The price is that they age: for the demo, `--end-date` and regenerate |

[`seeds/verify.sql`](seeds/verify.sql) **fails** — it does not warn — if a transaction does not balance, if entries are in a currency other than their account's, or if there are transactions with no entries. Measuring a benchmark over incoherent data is worse than not measuring it.

The integration tests do **not** depend on this dataset: they create their own data and undo it. The one that needs it is the evaluation.

## Evaluation

Two kinds of case, because two kinds of question.

```bash
just seed && just evaluate
```

```
deterministic: 12/12 (100.0%)
  ok   fuel_july                       159.46 EUR
  ok   fuel_last_month                 102.80 EUR
  …
heuristic: 3/3 (100.0%)
  ok   fuel_july_explained             score 1.00
  ok   june_spending_breakdown         score 1.00
  ok   savings_last_three_months       score 1.00
```

**Deterministic** ([`evaluation/queries.yaml`](evaluation/queries.yaml)) — one right answer: 12 cases with the plan the LLM will emit and the figure that must come out. Totals, income, net, groupings by week, category and merchant, an empty period, and one that checks that `PENDING` does not count.

**Heuristic** ([`evaluation/heuristic.yaml`](evaluation/heuristic.yaml)) — no single exact answer, so the result is scored instead of asserted. Each case carries a rubric of weighted requirements and a threshold. Today the judge is [`RuleBasedJudge`](src/finguard_banking/infrastructure/evaluation/rule_based_judge.py), which verifies the properties a correct result must have regardless of wording: the figure reconciles with its own evidence, the grouping actually groups, nothing is dated in the future. A requirement it cannot score returns **zero**, never a pass — that is how a rubric quietly stops measuring anything.

When an LLM judge lands it fulfils the same [`ResultJudgePort`](src/finguard_banking/domain/ports/result_judge.py) and scores what a rule cannot: whether the answer reads as an answer, whether it covers the question. The harness and the dataset do not change.

What makes this measure anything: **the expected figures do not come from the engine**. They are computed with [`evaluation/expected.sql`](evaluation/expected.sql) (`just evaluate-expected`), SQL written separately, over the tables, with no views, no compiler and no period resolver, with the dates spelled out by hand. If they came from the engine itself, the evaluation would only be checking that the engine resembles itself.

Two details: cases with a relative period **pin `reference`** — the runner fails if one does not, because the result would change every day — and the queries go through the **read-only role**, the same one the service uses.

`question` is not used yet: it is the text against which interpretation accuracy will be measured once the assistant turns natural language into a plan (phase 3). It stays in Spanish because that is the language of the product.

The tests in [`test_ledger_integrity.py`](tests/test_ledger_integrity.py) check each rejection against the real database. They fire the deferred triggers with `SET CONSTRAINTS ALL IMMEDIATE` and roll the transaction back at the end, which is the only cleanup possible over an immutable ledger.

Next: accounts and contacts, phase 1 of the [roadmap](../../docs/roadmap.md).
