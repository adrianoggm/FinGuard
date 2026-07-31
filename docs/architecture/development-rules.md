# Development rules

Rules that apply across the whole monorepo. See [monorepo.md](monorepo.md) for the structure that supports them.

1. Every application can be built and tested on its own.
2. Every Python service has its own `uv.lock`.
3. Every service owns its data.
4. No service queries another service's database directly.
5. Communication happens through public contracts.
6. Contracts are defined before the clients are generated.
7. A2A is an external adapter, not part of the financial domain.
8. The LLM never reaches the ledger directly.
9. Shared libraries contain no business logic.
10. Every event and request carries a `correlation_id`.
11. Every service exposes `/health`, `/ready` and metrics.
12. Any change to a financial operation requires concurrency and idempotency tests.
13. Credentials in the repository are strictly for local development: the services that use them publish no ports beyond `127.0.0.1`, and no deployment reuses them — deployments inject them from a secret store.
14. Everything written for other developers is in English: code, comments, tests, documentation, commit messages and pull requests. Only the product's own data stays in Spanish — merchant names, movement descriptions, and the questions a user asks — because that is what a Spanish-speaking user says and reads.
