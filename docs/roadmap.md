# FinGuard — Roadmap and metrics

## Roadmap

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

- Integrate an LLM provider.
- Implement tool selection.
- Keep context.
- Add explainable answers.
- Show the movements used.
- Implement text streaming.

*Outcome:* a working text assistant.

### Phase 4 — Dynamic visualisation

- Implement the visual planner.
- Generate Vega-Lite.
- Validate the specifications.
- Bring charts into the chat.
- Allow reformulation:
  - «Muéstralo por semanas».
  - «Mejor en barras».
  - «Compara con junio».

*Outcome:* dynamically generated charts.

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
