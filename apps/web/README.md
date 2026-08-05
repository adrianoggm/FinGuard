# web

The client, as a phone. React + TypeScript on Vite, local port `3000`.

Vite rather than Next.js — the vision permits either (§14). Five screens reached by tapping do not
need URLs, so server rendering and a routing framework would be scaffolding around a `useState`.

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

Five screens and a floating button. Tapping the button raises **conversation mode** as a sheet over
whichever screen you are on, which is the point of a floating button: you do not leave what you were
doing, and the figures staying visible behind the conversation are what make asking about your money
part of the app rather than a chat bolted onto it.

```
src/App.tsx           the phone: frame, status bar, tabs, the button, the sheet
src/Home.tsx          balance, month by month, four shortcuts, accounts, last movements
src/Accounts.tsx      every movement, with a merchant search and category filters
src/Cards.tsx         the cards, with what each one was charged in the window
src/Finances.tsx      where the money went: by category and by month
src/Bizum.tsx         pick a contact, type an amount, read the preview, say yes
src/Conversation.tsx  the sheet: turns, stages, the plan behind each figure, payments
src/api.ts            the orchestrator. The only file that knows a URL
src/format.ts         money, dates and amounts as Spain writes them
src/tabs.ts           where the app can be
src/styles.css        one stylesheet, no framework
```

**Every button goes somewhere.** The first version of this had four shortcuts and a tab bar that did
nothing, and that is worse than not drawing them: a button that does not respond teaches somebody the
app is broken, and they are right.

Four decisions in there are not cosmetic.

**Every figure is real.** The balance comes from `GET /v1/account/summary`, the lists and the
breakdowns from `/v1/account/movements` and `/v1/account/breakdown`, the cards from `/v1/cards`. All
of them reach the ledger through a plan or a view, with no model involved. Mock data would have put a second balance on the phone,
and a home screen disagreeing with the chat is the first thing anybody would notice.

**The plan travels with every figure the conversation produces.** Each answer carries the query behind
it and the movements behind that, one tap away. A number from a pipeline with a language model in it
should not have to be taken on faith, and that is the control that makes a residual error survivable:
somebody who asked about this month and reads "el mes pasado" can see it.

**A payment is answered in words.** In the chat the reply goes to the confirm endpoint as text,
including a typed one — the buttons exist because "sí" is what most people press, but "sí pero mándale
30" has to reach the service, which knows that is not a yes. This app never decides that somebody
agreed.

**The Bizum form has no grounding check, and that is right rather than a shortcut.** Grounding catches a
figure a model invented; there, the figure was typed into a field and the recipient was chosen from a
list this account was allowed to see. What does not change is the preview and the confirmation.

## Not here yet

- **Vega-Lite charts** (phase 4). The bars are CSS. Installing a charting library to draw six divs
  would be paying for that decision ahead of making it; the validated spec the service will send is
  what justifies the dependency.
- **More than one account.** The credential names exactly one, so `CUENTAS` lists one. Showing several
  means a credential naming a *customer* and every request naming which account — a change to identity,
  to the RLS declaration and to every endpoint. It is the only item here with a real architectural cost
  rather than a screen's worth of work.
- **Loans.** There is no credit in the ledger, so there is no "tu financiación" to put beside the
  balance. What is there instead is this month's spending, which is real. The cards are real now —
  migration 0009 gave them a table and tied each charge to one — but a credit card's *balance owed* is
  not modelled, so the screen shows what was charged rather than what is owed.
- **Voice.** §6.1.3 gives it its own service and the roadmap puts it in phase 6. A microphone button
  that does nothing would be worse than its absence.
- **Tests.** Vitest is installed and there are none. The flows are verified by driving a real browser
  against the real stack, which is not a substitute — it is what exists today.
- **A container.** `npm run dev` is the whole story so far; compose has no `web` service.
