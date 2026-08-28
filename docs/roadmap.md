# FinGuard — Roadmap and metrics

## Roadmap

### Security is not a phase

It appears in no phase below on purpose. The security section of the vision is an
**ordered stack** — Identity → Authorisation → Structural validation → Financial
policies → Human confirmation → Secure transaction → Audit — and phases 1 to 3 built
the third layer while skipping the first two. The result was a service where any
caller could read any account, because the account travelled in the request body.

That is what treating security as a late phase produces: not a missing feature, but an
architecture that assumes an identity nothing supplies. Every later item depends on
knowing who is asking — per-holder row-level security, the `/admin` endpoints, and the
Bizum confirmation in phase 5, which is a human authorising an operation and therefore
meaningless without an authenticated human.

| layer | state |
| --- | --- |
| Identity | Development token table, in the orchestrator. The account comes from the credential, not the request. `banking-api` authenticates nobody: the account arrives in the path, and migration 0003 says so in writing. |
| Authorisation | Eight per-operation scopes, deny by default, enforced as a route dependency rather than an `if` in a handler. `payments:confirm` is deliberately not implied by `payments:prepare`. No OAuth, no expiry, no revocation. |
| Structural validation | Closed schema, domain invariants, parameterised SQL, read-only role. |
| Financial policies | Ceilings and limits, and `MAX_PAYMENT_MINOR` is enforced in both services today. No daily limit, no rolling window, no per-holder policy. |
| Human confirmation | Built. Prepare returns a preview that moves nothing; confirm and cancel are separate requests; expiry is checked on confirm; a database trigger makes every terminal state terminal. |
| Secure transaction | Built. `FOR UPDATE` on the account and then the intent, always in that order, a required idempotency key with a reuse guard, and a deferred constraint trigger that refuses an unbalanced pair. |
| Audit | Built for banking's commands: `audit_events`, append-only by two triggers, written by the command bus for refusals as well as successes. Two gaps: the write sits outside the state-change transaction and is swallowed on failure, so a payment can succeed with no row; and the orchestrator writes nothing, so a question leaves no trail. |

**Before phase 5, not during it:** OAuth 2.0 with short-lived tokens, per-operation
scopes, `/admin` behind an administration scope, and per-holder row-level security so
the database enforces what the application currently promises. A payment confirmed by
an unauthenticated caller is not a demo, it is a bug with a receipt.

Three of those four arrived before phase 5 did: the scopes exist, `/admin/llm-policy`
sits behind `assistant:admin`, and migration `0016_holder_scope` rewrote every policy
holder-scoped. OAuth did not, and the last sentence is still live at the other end —
`banking-api` trusts its network, so the orchestrator's credential is the only thing
between a caller and a confirmation.

### Phase 0 — Design and preparation

- Create the repository.
- Record the architectural decisions.
- Define the contracts.
- Prepare Docker Compose.
- Create PostgreSQL.
- Set up CI.
- Define the demo scenarios.

*Outcome:* a runnable structure and a documented architecture.

### Phase 1 — Banking core

- Implement accounts.
- Implement the ledger.
- Create the projected balance.
- Generate synthetic movements.
- Implement contacts.
- Implement the query API.
- Add integrity tests.

*Outcome:* a deterministic banking API with no AI.

### Phase 2 — Financial queries

- Define `FinancialQuery`.
- Resolve relative dates.
- Implement the SQL compiler.
- Create the read views.
- Apply the read-only role.
- Prepare the evaluation dataset.
- Measure execution accuracy.

*Outcome:* financial questions answered in a verifiable way.

### Phase 3 — Conversational assistant

- Integrate an LLM provider — **done**, Ollama, local only. No API key, no egress.
- Implement tool selection — **done**. A Spanish sentence becomes a validated plan
  under a JSON Schema generated from the enums, so an invented category or period is
  unreachable at decode time rather than caught afterwards.
- Keep context — **done**, and stateless: the caller echoes back the previous plan, so
  there is no session to expire and the user can see what carries over.
- Add explainable answers — **done**. Every answer carries the plan that produced it.
- Show the movements used — **done**, as part of the answer rather than a debug extra.
- Implement text streaming — **done** as stage events. The answer text is a template
  and exists all at once; the model deciding what to ask is 2.2s of a 2.28s request
  and the ledger is 20ms, so the stages are what there is to stream.

Two things this phase added that were not on the list, both from the review:

- **An outcome taxonomy.** A plan that is schema-valid and answers a different question
  passes every check and returns a real figure. It is now one of six named outcomes,
  and asking a question back or refusing are correct answers rather than failures.
- **An evaluation for the step nobody was measuring.** 233 Spanish sentences with the
  plan each one determines, scored per field and by semantic equivalence, with the
  two rates that matter: a plan where a question was due, and the reverse.

*Outcome:* a working text assistant, and a number for how well it understands.

**Not closed, and the bar is now written down.** The two risks that kept the endpoint
disabled have both moved: refusing what cannot be expressed is a deterministic guard rather
than a model's disposition, and the known injection is refused by a rule that compares the
plan against the sentence.

What replaced "when it is better" is two signed gates in [next.md](next.md) — zero for what
deterministic code can guarantee, a number for what a model decides, and a different bar for
a scripted demo than for somebody's real balance. Gate 1 failed two of its eight properties
when this paragraph was written — unsafe execution above the 10% bar, and one invalid
structure in the adversarial set where the bar is zero — and its table in [next.md](next.md)
now records both as cleared. That table is the measurement of record, and the figures stay
there so there is one place to change when they move. Gate 2, the one for somebody's real
balance, still fails on OAuth, on banking authenticating its callers, on rate limiting
`/v1/messages`, on a contract checked in CI and on a payment dataset scored like the questions.

ORCHESTRATOR_QUESTIONS_ENABLED has no default: a deployment has to state it. `compose.yaml`
states `true`, because that stack is seeded data on loopback. A documented risk is not a
control, and neither is a threshold nobody agreed.

### Phase 4 — Visualisation and «Mis métricas»

Rewritten after phase 6, which is when it became the next thing to build.
[ADR-011](architecture/adr-011-derived-charts-and-saved-metrics.md) is the accepted design. The
vocabulary of a chart is open; what stays closed is where its numbers come from.

- Define `ChartSpec`: a typed, protocol-free value carrying encodings and **no measured figure** —
  **done**, and the closed half is closed by construction rather than by a check: an encoding's
  field has to match an identifier, so a figure cannot be written into a data position at all.
- Derive a default chart per capability already built — comparison, ratio, cumulative, frequency,
  recurrence, group ranking, breakdown — with no model call — **done for six of the seven.**
  Cumulative reads as an area, frequency keeps weekday order, recurrence is a cadence of points, a
  ranking is ordered bars honouring the plan's own limit, a comparison is two series on one axis
  and a breakdown is horizontal bars. Of the ratios only a share has its own stacked default; net
  cash flow, savings rate and percent change fall through to a plain bar, and none of the four puts
  the derived figure beside its operands. Not derived *over the corpus*, either: the conversation
  corpus asserts no chart on any of its 1167 turns.
- Emit Vega-Lite from a presenter in infrastructure, validated against its schema and against the
  fields the result has — **half done.** The presenter is in infrastructure and the field check is
  real and tested. No schema validation exists anywhere in the repository, and the presenter
  declares Vega-Lite v5 while the client resolves 6.4.3 and forwards that declaration untouched.
- Bring charts into the chat — **done**, and measured from the outside in the
  [UX audit](audits/2026-08-28-ux.md), which is where the chart's remaining rough edges are written
  down: an axis titled «Grupo», `SCREAMING_SNAKE_CASE` enums above a sentence that says «alquiler»,
  and a default ranking sorted alphabetically because `sort: descending` on a discrete channel
  orders the labels rather than the measure.
- Accept reshaping in the holder's own words as an **encoding patch** the model emits and code
  merges: «apílalo por cuenta», «ponlo en porcentaje», «solo el top 3», «mejor en barras».
  «Muéstralo por semanas» and «compara con junio» already work — they patch the plan, and the chart
  follows. **Done, and no model emits anything.** The interpreter is a deterministic regex over an
  allow-list, so a restyle costs no model call and can only ask for a change the service is certain
  it can make. Safer than the design asked for, and not what the design says.
- Give the orchestrator its first persistence: an `assistant` schema, its own migrations,
  row-level security. Banking's migrations do not grow — **done**, `assistant.saved_metric` with
  `FORCE ROW LEVEL SECURITY` and an Alembic history of its own. Never exercised by an automated
  run: the one test that executes the policy needs a database URL nothing in the repository sets,
  and CI applies only banking's migrations.
- Save **structure and not data**: `plan_json` plus `encoding_json`, no rows and no totals. A
  relative period stays relative — **done**, with five columns the design did not list because the
  routes needed them: position, version, idempotency key, request hash and `updated_at`.
- Re-execute a saved plan through the same path a question takes, treating the stored plan as
  untrusted input — **done**, and the scope re-check is narrower than it reads. It refuses a single
  account the holder owns and the rest of the system serves, and it does not look at
  `across_accounts` or the plural account field at all, so the wide case passes unchecked while the
  narrow one is refused.
- Build «Mis métricas»: save from a turn, re-execute on open, retitle, reshape, reorder — **four of
  five.** Reshape was never built: `PATCH /v1/metrics/{id}` accepts a title and reuses the stored
  definition by construction, and the card offers retitle, refresh, reorder and delete. The
  repository can write a new encoding; nothing reaches it.

*Outcome:* charts that show what the holder asked to see, and cannot show a figure the ledger did
not produce. A saved metric is a question that keeps answering itself, not a screenshot that ages.

**Not closed, and the gap is the measurement rather than the feature.** Of the eight acceptance
criteria in ADR-011, two have a proof, one has half of one, one is partial, and four have nothing
behind them — every criterion phrased "on the whole corpus" or "over the deterministic seed" has no
corpus run and no seed run, because charts appear nowhere in `evaluation/`, nowhere in the corpus
harness and nowhere in CI. The status is recorded criterion by criterion in
[ADR-011](architecture/adr-011-derived-charts-and-saved-metrics.md).

### Phase 5 — Secure Bizum

Built, and wider than the list: `payment_intents` grew two more rails after this phase closed, so
an external transfer and a transfer between the holder's own accounts travel the same lifecycle.

- Implement `payment_intents` — **done**, with the invariants as table constraints: booked if and
  only if confirmed, one idempotency key per account, expiry after creation.
- Add two-step confirmation — **done**. Prepare returns a preview that moves nothing; confirm and
  cancel are separate requests, expiry is checked on confirm, and a trigger refuses any transition
  out of a terminal state.
- Add idempotency — **done**, and the header is required rather than defaulted. A key reused for a
  different payment is refused, because a caller who corrected 200 € to 300 € and kept the key was
  shown 300 € above an identifier that pays 200 €.
- Lock the balance under concurrency — **done**, `FOR UPDATE` on the account and then the intent,
  always in that order so two confirmations serialise instead of deadlocking. The balance is
  recomputed at confirm rather than trusted from the preview.
- Implement double entry — **done**, and the invariant is a deferred constraint trigger in the
  database rather than an assertion in a test.
- Produce a receipt — **done**, carrying the ledger transaction id rather than a reference the
  payments table invented for itself.
- Record the audit trail — **done** for banking's commands, refusals included, append-only by two
  triggers. Not for the orchestrator, which writes nothing.

*Outcome:* a simulated, secure Bizum operation. **Reached, with a defect in front of it:** when a
confirmation is refused the user sees nothing on either money screen. `setError` is called and its
only render site sits after the preview's early return, so the message never appears and both
buttons re-enable — the user taps Confirmar again. Finding 1 of the [UX audit](audits/2026-08-28-ux.md).

### Phase 6 — Real-time voice

Built and measured locally. Two providers behind each port: a fake for the tests and CI, and a
local model for the demo. Nothing leaves the machine.

- Capture audio — **done**, in the browser.
- Integrate transcription — **done**, faster-whisper on CPU.
- Add synthesis — **done**, Piper.
- Implement barge-in — **done**, on both sides: the gateway's session state decides whether a turn
  was interrupted and the client acts on it.
- Synchronise voice, text and charts — **done**. A voice turn carries the same answer a typed one
  does, chart included, and the client refuses a voice answer whose visualisation carries values.
- Measure latency — **done**, twice, in
  [the gateway's own evaluation](../apps/voice-gateway/evaluation/RESULTS.md): once against the
  host and once in containers with real speech, each with the hardware and the models recorded.

*Outcome:* a multimodal conversation. The measurement is honest about what it is not — the GPU
transcription path failed to load and was measured on CPU, and the last milestone is when a frame
reaches the client rather than when a speaker makes it audible.

### Phase 7 — A2A preparation

- Build the A2A adapter.
- Publish the Agent Card.
- Expose a query skill.
- Expose a Bizum preparation skill.
- Create a fictional external agent.
- Validate authentication and tasks.

*Outcome:* an interoperability demo that does not compromise the core.

### Phase 8 — Evaluation and security

- Run the benchmark.
- Test ambiguous queries.
- Test prompt injection.
- Test duplicate operations.
- Test concurrency.
- Measure latencies.
- Review logs and sensitive data.

*Outcome:* verifiable metrics.

### Phase 9 — Deliverables

- Prepare the video script.
- Record the demo.
- Write the report.
- Prepare the diagrams.
- Summarise the metrics.
- Document the limitations.

### Final buffer

- Fixes.
- Re-recording if needed.
- Verification of the deliverables.
- Final submission.

## Success metrics

**Understanding**

- Intent accuracy.
- Date extraction accuracy.
- Category accuracy.
- Correct resolution of contextual references.

**Queries**

- Valid SQL.
- Execution accuracy.
- Amount accuracy.
- Grouping accuracy.
- No unauthorised access.

**Operations**

- Zero executions without confirmation.
- Zero duplicate operations.
- A ledger that always balances.
- Correct behaviour on insufficient funds.
- Correct behaviour on expired confirmations.

**Visualisation**

- Percentage of valid specifications.
- Chart suitability.
- No invented fields.
- Generation time.
- Ability to reformulate.

**User experience**

- Latency to first content.
- Total latency.
- Answer quality.
- Voice barge-in.
- Clarity of the confirmations.

### Initial targets

| Metric | Target |
| --- | --- |
| Query accuracy | ≥ 95 % |
| Safe SQL queries | 100 % |
| Valid charts | ≥ 98 % |
| Operations without confirmation | 0 |
| Duplicate operations | 0 |
| P50 text latency | < 2 s |
| P95 query latency | < 4 s |
| Unbalanced ledger | 0 |

The final results must be measured, not estimated.
