# web

The client, as a phone. React + TypeScript on Vite, local port `3000`.

Vite rather than Next.js — the vision permits either (§14). This is one screen that talks to
127.0.0.1, so server rendering and a routing framework would be scaffolding around a single page.

It holds no financial rules and never calls `banking-api`: only `orchestrator`. Every figure on screen
arrived through it, which is what makes that claim checkable rather than stated.

## Running it

```bash
cp .env.example .env.local     # the orchestrator's address and a development token
npm install
npm run dev                    # http://127.0.0.1:3000
```

It needs the stack up — `docker compose --profile poc up -d` — with `ORCHESTRATOR_PAYMENTS_ENABLED`
on if you want to send a bizum. The token in `.env.example` holds the payment scopes;
`demo-token-laura-0001` does not, and watching it answer *"No puedo hacer pagos con esta cuenta"* is
the other half worth seeing.

## The shape of it

A bank app with a floating button. Tapping it raises **conversation mode** as a sheet over the app,
which is the point of a floating button: you do not leave what you were doing, and the balance staying
visible behind the conversation is what makes asking about your money feel like part of the app rather
than a chat bolted onto it.

```
src/App.tsx           the phone: frame, status bar, the button, the sheet
src/Home.tsx          the bank screen — balance and recent movements
src/Conversation.tsx  the sheet: turns, stages, the plan behind each figure, payments
src/api.ts            the orchestrator. The only file that knows a URL
src/format.ts         money and dates as Spain writes them
src/styles.css        one stylesheet, no framework
```

Three decisions in there are not cosmetic.

**The home screen's balance is real.** It comes from `GET /v1/account/summary`, which reaches the
ledger through a plan built in code with no model involved. Mock data would have put a second balance
on the phone, and a home screen disagreeing with the chat is the first thing anybody would notice.

**The plan travels with every figure.** Each answer carries the query that produced it and the
movements behind it, one tap away. A number from a pipeline with a language model in it should not
have to be taken on faith, and that is the control that makes a residual error survivable: somebody
who asked about this month and reads "el mes pasado" can see it.

**A payment is answered in words.** The reply goes to the confirm endpoint as text — including a typed
one. The buttons exist because "sí" is what most people want to press, but "sí pero mándale 30" has to
reach the service, which knows that is not a yes. This app never decides that somebody agreed.

## Not here yet

- **Vega-Lite charts** (phase 4). The dependency is not installed until there is a spec to render.
- **Voice.** §6.1.3 gives it its own service and the roadmap puts it in phase 6. A microphone button
  that does nothing would be worse than its absence.
- **Tests.** Vitest is installed and there are none. The flows are verified by driving a real browser
  against the real stack, which is not a substitute — it is what exists today.
- **A container.** `npm run dev` is the whole story so far; compose has no `web` service.
