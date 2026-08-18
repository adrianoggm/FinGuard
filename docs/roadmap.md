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
| Identity | Development token table. The account comes from the credential, not the request. |
| Authorisation | One caller, one account, deny by default. No scopes yet. |
| Structural validation | Closed schema, domain invariants, parameterised SQL, read-only role. |
| Financial policies | Ceilings and limits. Amount policies come with phase 5. |
| Human confirmation | Phase 5. |
| Secure transaction | Phase 5. |
| Audit | Not started. |

**Before phase 5, not during it:** OAuth 2.0 with short-lived tokens, per-operation
scopes, `/admin` behind an administration scope, and per-holder row-level security so
the database enforces what the application currently promises. A payment confirmed by
an unauthenticated caller is not a demo, it is a bug with a receipt.

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
a scripted demo than for somebody's real balance. As measured, gate 1 fails two of eight
properties: unsafe execution at 12.7% against a bar of 10%, and one invalid structure in the
adversarial set where the bar is zero.

ORCHESTRATOR_QUESTIONS_ENABLED stays false until both clear. A documented risk is not a
control, and neither is a threshold nobody agreed.

### Phase 4 — Visualisation and «Mis métricas»

Rewritten after phase 6, which is when it became the next thing to build.
[ADR-011](architecture/adr-011-derived-charts-and-saved-metrics.md) is the accepted design. The
vocabulary of a chart is open; what stays closed is where its numbers come from.

- Define `ChartSpec`: a typed, protocol-free value carrying encodings and **no measured figure**.
- Derive a default chart per capability already built — comparison, ratio, cumulative, frequency,
  recurrence, group ranking, breakdown — with no model call.
- Emit Vega-Lite from a presenter in infrastructure, validated against its schema and against the
  fields the result has.
- Bring charts into the chat.
- Accept reshaping in the holder's own words as an **encoding patch** the model emits and code
  merges: «apílalo por cuenta», «ponlo en porcentaje», «solo el top 3», «mejor en barras».
  «Muéstralo por semanas» and «compara con junio» already work — they patch the plan, and the chart
  follows.
- Give the orchestrator its first persistence: an `assistant` schema, its own migrations,
  row-level security. Banking's migrations do not grow.
- Save **structure and not data**: `plan_json` plus `encoding_json`, no rows and no totals. A
  relative period stays relative.
- Re-execute a saved plan through the same path a question takes, treating the stored plan as
  untrusted input.
- Build «Mis métricas»: save from a turn, re-execute on open, retitle, reshape, reorder.

*Outcome:* charts that show what the holder asked to see, and cannot show a figure the ledger did
not produce. A saved metric is a question that keeps answering itself, not a screenshot that ages.

### Phase 5 — Secure Bizum

- Implement `payment_intents`.
- Add two-step confirmation.
- Add idempotency.
- Lock the balance under concurrency.
- Implement double entry.
- Produce a receipt.
- Record the audit trail.

*Outcome:* a simulated, secure Bizum operation.

### Phase 6 — Real-time voice

- Capture audio.
- Integrate transcription.
- Add synthesis.
- Implement barge-in.
- Synchronise voice, text and charts.
- Measure latency.

*Outcome:* a multimodal conversation.

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
