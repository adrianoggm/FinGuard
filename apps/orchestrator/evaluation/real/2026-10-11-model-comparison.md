# Gemini 3.5 Flash-Lite against 3.5 Flash, same turns (2026-10-11)

121 turns per model, replayed on revision `6bc08170` (LangGraph runtime) against the same seeded
bank: four 15-turn sessions, the 11-turn banking session, the model-comparison batteries and the
reserved holdout sessions. Raw records: `2026-10-11-6bc08170-35-flash(-lite)-*.json`.

Every turn was judged against its expectation (route, mechanism, intended query): OK when it did
what was meant, PARTIAL when the route was right but a filter or period was wrong or a needless
question was asked, FAIL otherwise. Totals were checked against the ledger where checkable.

| | 3.5 Flash-Lite | 3.5 Flash |
|---|---|---|
| OK / PARTIAL / FAIL | 97 / 6 / 18 | 100 / 13 / 8 |
| 15-turn sessions (60) | 53 / 2 / 5 | 57 / 2 / 1 |
| Totals checked against the ledger | 34, all match | 32, all match |
| Unreadable answers (PROVIDER_ERROR) | 3 | 0 |
| Rate limits (429) or provider failures | 0 | 0 |
| Turn time without pacing, P50 / P95 | 2.2 s / 5.1 s | 7.6 s / 14.9 s |
| Provider request time, P50 / P95 | 1.1 s / 2.3 s | 6.0 s / 12.9 s |

Flash-Lite carries stale filters into new questions and fails references to the answer on
screen («compáralo con el actual», «ese mes»); Flash drops filters on some follow-ups and read
«tarjeta» as an account name. Neither confirmed a payment: automated runs never answer «sí».

The demo configuration (`compose.gemini.yaml`) uses 3.5 Flash. With the visual planner's budget
at 30 s, five charts of five were the model's, verified; at the earlier 6 s every one timed out
into the rules' chart. With pacing turned off on a paid key (2026-10-12), a question takes about
5 s and a chart turn 10–16 s.
