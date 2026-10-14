# Hexagonal review before phase 3

Review of `banking-api` and `orchestrator` as they stand at commit `fcc1dc0`, before
wiring the LLM service. Every finding carries the evidence it came from, so it can be
argued with instead of believed.

Verdict: the outer boundaries are right — ports exist, the read path is locked behind a
restricted role, the invariants live in the database. What is wrong is the **inside**:
the domain layer knows the HTTP contract, three files act as drawers, and the money
concepts are primitive types. None of it hurts today; all of it hurts the moment a
second inbound protocol (A2A) or a write operation (Bizum) arrives.

## Findings

Ordered by how much they cost to leave as they are.

### H1 — The domain depends on Pydantic and on the transport format · high

`domain/financial_query.py` and `domain/relative_period.py` import Pydantic, and carry
**9 transport aliases** (`alias_generator=to_camel`, `alias="from"`, `alias="to"`).

```python
# domain/financial_query.py
class DateRange(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, frozen=True)
    start: date = Field(alias="from")     # ← "from" is the HTTP contract, not the domain
```

Vision §4.4 says internal objects must not depend on FastAPI or on protocols. `from` /
`to` / camelCase are the shape of §13.2, not of the business. The rule is not
cosmetic: §7 plans an A2A gateway whose wire shape is a `data` Part, and §6 a voice
gateway. With aliases baked into the core, the second protocol either inherits
camelCase JSON it has no reason to use, or the same class grows a second set of
aliases.

**Change.** The domain becomes frozen dataclasses with explicit invariants and no
Pydantic. Pydantic stays where it belongs — in the inbound adapters, as DTOs — and one
mapper per adapter converts DTO → domain. `orchestrator` has the identical problem:
`LlmPolicy` is a Pydantic model in the domain **and** the request body of
`PUT /admin/llm-policy`.

Cost: medium. Touches 4 domain modules, 2 adapters, and the tests that build these
objects. It is the finding everything else depends on.

### H2 — Ledger states and entry types are magic strings · high

Excluding the dataset:

| Literal | Occurrences | Files |
| --- | --- | --- |
| `DEBIT` | 28 | migrations, seed generator, verify.sql, expected.sql, 3 test files |
| `CREDIT` | 17 | the same, plus `0002_read_views` |
| `BOOKED` | 9 | migrations, view, seed, verify, tests |
| `ACTIVE` | 3 | seed, conftest |
| `PENDING` | 3 | seed, 2 test files |

There is no `EntryType`, `TransactionStatus` or `AccountStatus` anywhere in the domain,
even though the SQL `CHECK` constraint already enumerates the valid values. Adding
`REVERSED`, or a `HELD` status for phase 5 holds, is a grep across eight files with no
compiler or type checker helping.

**Change.** Three `StrEnum`s in the domain, and a single place that turns them into SQL
literals. The migrations keep their literals — a migration is a historical record and
must not change meaning when an enum does — but the application, the seed generator and
the tests reference the enum.

Cost: low. High value: this is the one that makes the ledger extensible.

### H3 — `financial_query.py` is a drawer · high

80 lines holding five classes of three different kinds, plus three limits:

- 3 enums (`Metric`, `Direction`, `GroupBy`)
- 1 value object (`DateRange`)
- 1 aggregate (`FinancialQuery`)
- 3 caps: `MAX_SUPPORTING_MOVEMENTS`, 20 categories, 40 characters

**Change.** Split by responsibility:

```text
domain/
├── constants.py              caps and limits, one home
├── enums.py                  Metric, Direction, GroupBy, EntryType, TransactionStatus
├── value_objects/
│   ├── date_range.py
│   ├── money.py              see H6
│   └── category.py           the validated category, today an inline validator
├── financial_query.py        the plan only
├── relative_period.py
├── balance.py
├── movement.py
└── errors.py
```

Cost: low, mechanical.

### H4 — The domain plan and the HTTP DTO duplicate the field list · medium

`FinancialQuery` declares 8 fields; `FinancialQueryRequest` declares 9. They are kept
in sync by hand, and nothing fails if they drift: a field added to the domain and
forgotten in the DTO simply never arrives.

Today this duplication is an accident. After H1 it becomes deliberate and
one-directional — the DTO owns the wire, the domain owns the rules — and a mapper test
locks them together. **Do not deduplicate them by making the domain the wire model
again**: that is the current design and it is what H1 is about.

### H5 — Policy caps are scattered across layers · medium

```text
domain/financial_query.py:9     MAX_SUPPORTING_MOVEMENTS = 100
domain/relative_period.py:19    MAX_MONTHS = 24
domain/relative_period.py:20    MAX_DAYS = 366
adapters/.../query_compiler.py  MAX_GROUPS = 500     ← §8.6 calls this a business limit
domain/financial_query.py:73    20 categories, inline
domain/financial_query.py:78    40 characters, inline
```

`MAX_GROUPS` is the "maximum row limit" of §8.6 — a policy decision — and it lives in a
PostgreSQL adapter, where the next adapter would not see it.

**Change.** `domain/constants.py` is the single home; the adapter reads it.

### H6 — There is no money value object · medium, growing

`amount_minor: int` and `currency: str` travel as separate fields in `Balance`,
`Movement` and `QueryGroup`. Nothing in Python stops adding an amount in EUR to one in
USD; the only thing preventing it is the database trigger, which does not run until
COMMIT.

Phase 5 is exactly where this bites: preparing a Bizum means comparing an amount
against a balance, subtracting a hold, and producing a receipt. All arithmetic, all
currently on bare `int`s.

**Change.** A `Money` value object holding minor units plus currency, with operations
that refuse to mix currencies. The database columns do not change — `amount_minor` and
`currency` are correct — only the in-memory representation.

I deferred this deliberately in the balance slice, when there was no arithmetic. There
is about to be.

### H7 — The mapping layer is missing · medium

Row → domain conversion lives inline inside `PostgresFinancialQueryRepository.run()`
(two nested comprehensions), and domain → response inline inside the routes. The team
template calls for explicit mappers, and they are also the natural place to test the
conversion without a database or an HTTP client.

**Change.** `adapters/outbound/postgres/mappers.py` and
`adapters/inbound/http/mappers.py`.

### H8 — `routes.py` mixes DTOs, mapping and routing · medium

One file holds 7 Pydantic models, 2 routers, the relative-period resolution and 4
endpoints. It is the file that will grow fastest in phase 3.

**Change.** `http/schemas/` (requests and responses), `http/mappers.py`,
`http/routers/` (one module per resource).

### H9 — Error-to-HTTP mapping lives in the composition root · low

`bootstrap/main.py` registers the handlers for `AccountNotFoundError` → 404 and
`ValidationError` → 422; `orchestrator` does the same for `UnknownProviderError` in its
own `main.py`. Translating a domain error into a status code is an inbound-adapter
responsibility, not the composition root's, and there is no shared convention between
the two services.

**Change.** `adapters/inbound/http/error_handlers.py` per service, with the rule
written down: not-found → 404, invalid input → 422, policy refusal → 409.

### H10 — Tests import `conftest` as a module · low

`from conftest import as_pool, create_account, ...` works only because pytest injects
the test directory into `sys.path`. The helpers are library code, not fixtures.

**Change.** `tests/support/` as a real package; `conftest.py` keeps only fixtures.

### H11 — Domain module coupled to another domain module · low

`relative_period.py` imports `DateRange` from `financial_query.py`. It disappears on
its own once H3 gives `DateRange` its own module.

## What not to change

Naming the things that are already right matters as much, so the refactor does not
wander into them:

- **Two pools with different roles** and the read-only reader. It is the strongest
  guarantee in the codebase and it does not depend on the code being correct.
- **The `assistant_read` views** as the only read surface, and the test that no
  statement on the read path names a table.
- **The invariants in the database** (deferred balance trigger, immutability). They do
  not move into Python "to be hexagonal": their value is precisely that they are
  outside the application.
- **`explain` as opt-in** and the query trace.
- **The evaluation harness** with independently computed expectations.
- **`resolve` as a pure function** with the clock at the edge. That part already follows
  the pattern.

## Resolved: commands, queries and buses

Decided in favour of the team template: **CQRS with buses, from now**, not deferred to
phase 5.

Both services carry a `QueryBus` and a `CommandBus`. Reads are queries; swapping the LLM
policy is a command. Handlers answer with Results, never with domain models — the rule
the previous code broke by handing `Balance` and `FinancialQueryResult` straight to the
controller.

`banking-api` registers no commands yet, and that is the point of having the bus already:
when phase 5 adds a money movement, idempotency, audit and correlation IDs hook into one
seam instead of into every write.

Two conscious deviations from the template, both worth knowing:

- **Async, not sync.** The template says sync by default; this project is asyncpg and
  async FastAPI throughout. Converting would be a large rewrite with nothing to show.
- **The bus modules are duplicated** in both services rather than shared. Two copies of
  sixty lines beat a package nothing else uses; they move to `packages/python/` when a
  third service needs them. Marked with a `ponytail:` note in both.

## Suggested order

Each step leaves the suite green and is a commit of its own.

| # | Step | Findings |
| --- | --- | --- |
| 1 | Extract enums, constants and value objects into their own modules | H3, H5, H11 |
| 2 | Add ledger enums and use them outside the migrations | H2 |
| 3 | Remove Pydantic from the domain; DTOs and mappers in the adapters | H1, H4, H7 |
| 4 | Split `routes.py` into schemas, mappers and routers | H8 |
| 5 | `Money` value object and its arithmetic | H6 |
| 6 | Error handlers in the HTTP adapter | H9 |
| 7 | `tests/support/` package | H10 |
| 8 | The same treatment for `orchestrator` (`LlmPolicy` is domain + wire) | H1 |

Steps 1, 2 and 3 are worth doing before the LLM integration: step 3 changes the shape
of every DTO, and phase 3 is going to add several.

Steps 5 to 7 can wait, but 5 should land before phase 5 starts.
