# FinGuard simulated gym agent

`simulated-gym-agent` stands in for third parties. It is a genuine A2A 1.0 server built with the
official `a2a-sdk`, deployed as several gyms that answer the same question in different ways, so
that FinGuard's A2A gateway can be demonstrated and tested against agents it has never been coded
for. FinGuard knows only its URL; nothing in FinGuard imports this package.

Three styles, chosen with `GYM_STYLE`:

- `norte` answers with structured data parts in its own vocabulary (`sesiones`, `huecos`,
  `hora_inicio`), is protected by an API key header, and asks a member to link their account on
  `/vincular` before booking.
- `sur` answers in Spanish text parsed from the fixed request template, is protected by a bearer
  token, and books without linking.
- `legacy` is deliberately non-compliant: it serves an A2A 0.3 card and nothing else, for the
  conformance demo.

The timetable is deterministic for a given `GYM_SEED`. Tomorrow's spinning is a fixed demo anchor:
18:30 (6 places), 19:00 (4), 20:00 (full) and 21:00 (3).

## Local execution

From the repository root:

```bash
GYM_STYLE=norte GYM_A2A_URL=http://127.0.0.1:8000 GYM_PUBLIC_URL=http://127.0.0.1:8000 \
GYM_API_KEY=gym-demo-key \
  uv run --directory apps/simulated-gym-agent uvicorn finguard_simulated_gym.main:app
```

Endpoints: `GET /.well-known/agent-card.json` (public), JSON-RPC at `/a2a/jsonrpc`, HTTP+JSON at
`/a2a/rest`, `GET /health`, and `GET|POST /vincular` (the account-linking page; both styles serve
it, and only norte sends a member there).
Everything under `/a2a/` needs the style's credential; the card and the linking page do not.

## Configuration

All variables use the `GYM_` prefix; `.env.defaults` lists the development values.

| Variable | Meaning |
| --- | --- |
| `GYM_STYLE` | `norte`, `sur` or `legacy`. |
| `GYM_A2A_URL` | Base URL written into the card's interfaces, e.g. `http://gym-norte:8000`. |
| `GYM_PUBLIC_URL` | The card's `provider.url` and the origin of the linking page, e.g. `http://127.0.0.1:9101`. |
| `GYM_API_KEY` | `X-Gym-Key` value (required for norte). |
| `GYM_BEARER_TOKEN` | Bearer token (required for sur). |
| `GYM_SEED` | Timetable seed, default `0`. |
| `GYM_TIMEZONE` | Zone in which the day and the windows are read, default `Europe/Madrid`. |
| `GYM_TODAY` | Optional ISO date. Tests pin it; a deployment leaves it unset. |

## Tests and container

```bash
uv run --directory apps/simulated-gym-agent pytest -q
uv run --directory apps/simulated-gym-agent ruff check .
uv run --directory apps/simulated-gym-agent mypy src
```

The tests drive the app in process through the SDK client, built the way the gateway builds its
own. The image is built from `apps/simulated-gym-agent` and listens on port 8000:

```bash
docker build -t finguard-simulated-gym apps/simulated-gym-agent
```
