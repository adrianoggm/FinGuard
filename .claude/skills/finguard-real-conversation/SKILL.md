---
name: finguard-real-conversation
description: "Validate FinGuard with real end-to-end conversations against the running stack: replay the person's conversations, improvise new ones as a holder with a goal, drive two through the real web with Playwright's MCP, check every figure against the ledger in SQL and judge each turn against a rubric. Use before and after any change to conversation handling, and whenever asked to 'probar de verdad', 'validación real' or 'end to end'."
---

# FinGuard: real conversations, end to end

Tests and the offline battery run on a simulated bank and a fixed date; a person trying FinGuard in
ordinary conversation still found it brittle. This skill is the gate that decides whether a change
to conversation handling worked (spec §4.1,
`docs/superpowers/specs/2026-10-07-conversation-state-slice-1-design.md`): whole conversations
against the stack **as the demo runs it**, judged two ways that are kept apart:

- **Figures, mechanically.** `scripts/real_conversation.py` re-does in SQL every answer that is a
  single sum or count of the plan it ran (read-only role, holder-scoped views) and records
  `figure_check: match | mismatch | n/a`. A `mismatch` is a failure, whatever else is right.
- **The conversation, against the rubric below.** That is your judgement, one line of reason per
  turn, written into the run's JSON.

Everything runs from `apps/orchestrator`. The runner's own help: `uv run python
scripts/real_conversation.py --help`. What it checks and why: `evaluation/real/README.md`.

## Rules that are not negotiable

1. **Never load, truncate, migrate or write the database** at 127.0.0.1:5442. Not `just seed`, not
   `just migrate`, not a `psql` as the owner. Loading truncates the demo's bank; it is the person's
   step. The runner only reads, as `finguard_query_reader`.
2. **Never confirm anything that executes.** No «sí», «vale», «ok», «confirmo» to a payment,
   transfer or booking preview, through the API or the web. In the web, **never click a yes
   button**, each of which sends «sí»: «Sí, enviar» and «Sí, reservar» in the conversation
   sheet (`apps/web/src/Conversation.tsx`), «Confirmar» on the Bizum and Traspaso preview card
   (`apps/web/src/Approval.tsx`). Use «No» or «Cancelar», or type «no». A goal that ends in a
   payment is met at a correct preview. The runner refuses a yes to an open preview (exit 3) —
   if you hit it, that was your mistake — but it cannot see the browser: there, only you stand
   between a click and a real payment.
3. **Never edit an expectation** in `evaluation/real/conversations.yaml` to make a turn pass, and
   never "correct" the person's words (typos, missing accents, chips stay as typed).
4. **A `figure_check: mismatch` is a failed turn.** No exceptions, no "the model was close".
   So is `figure_check: error`: the figure should have been checked and could not be.
5. Never start, stop or rebuild the compose stack or the model unless the person asked you to.

## 1. Preflight

```bash
uv run python scripts/real_conversation.py preflight
```

- Exit **0**: go on.
- Exit **1**: a service (orchestrator 8000, banking 8001, gateway 8003) or the ledger is not
  reachable. Stop and tell the person which one.
- Exit **2**: the newest movement is older than yesterday. Stop and ask the person to load the data
  with `just seed` (it truncates the ledger — their call, never yours). Only if the person
  explicitly accepts older data, rerun with `--max-age-days N` on `preflight` and `replay`; the
  report records it.
- Read the rest of the output:
  - `product code same as HEAD` — the stack runs the code under test. If it says `differs`, the
    running service is not this commit: stop, unless the person asked for a run of whatever is
    running, and say so in the findings.
  - GPU state and the model in Ollama's memory. If none is loaded, the first turn includes the load:
    say so when you read the latency.

The run's name is `evaluation/real/<YYYY-MM-DD>-<sha>`, with the orchestrator's `/version` sha
(`replay` uses it by default).

## 2. Replay the person's conversations

```bash
uv run python scripts/real_conversation.py replay --ux evaluation/ux/conversations.yaml \
  --out evaluation/real/<YYYY-MM-DD>-<sha>
```

It runs preflight again, then sends every conversation of `evaluation/real/conversations.yaml`
and, with `--ux`, the battery's ten episodes as `ux-<name>` (their `route` is the expectation;
turns without a mechanism tag count as `untagged`), turn by turn, echoing the conversation the
service returns as a client does. It writes `<run>.json` (every record plus the preflight) and
`<run>.md` (transcripts, empty verdict columns) **after every turn**, so a crash keeps what ran.
One line per turn while it runs. An HTTP error or a timeout is a failed turn, recorded as such;
a figure the SQL could not check is `error`, also a failure.

## 3. Improvise at least three conversations

Play the holder (Laura). Pick at least three goals, a different mix from the last run when you can:

1. Find out what the gym costs, then pay Marta 20 € — stop at a correct preview and say «no».
2. Compare two months, then change the category.
3. Ask about subscriptions, correct yourself, then abandon («déjalo», «olvídalo»).
4. Ask what you sent someone this month, then what they sent you, then something else entirely.
5. Ask the balance of one account, then what you spent this week, then «¿y la anterior?».

Write each next message **only from what the person sees in the last answer** (its text and its
suggestion chips), never from the plan or the state in the record. Write like a hurried person on
a phone: short; typos and no accents sometimes («cuanto gaste», «pasao»); ellipsis («y en
julio?», «y ella?»); corrections («no, digo restaurantes»); a change of mind halfway; now and then
a chip, copied exactly. Stop when the goal is met or after **eight** turns.

```bash
# first turn names the goal; the state file is created
uv run python scripts/real_conversation.py say --state <tmp>/<name>.json --goal "<objetivo>" "<frase>"
# every next turn
uv run python scripts/real_conversation.py say --state <tmp>/<name>.json "<frase>"
```

`<tmp>` is a scratch directory outside the repository (your scratchpad); `<name>` is short and
unique, e.g. `impro-gimnasio-marta`. The conversation is named after the file and always starts
with `impro-` (the script adds it if missing), so it can never take the place of a replayed
conversation; `summarise` refuses one that would. Each call prints what FinGuard said, the
suggestions, the query line, the SQL figure and the open state. The state files are folded
into the run in step 6.

**An improvised conversation that fails a turn becomes a replayed one.** Append it to
`evaluation/real/conversations.yaml` (new `name`, its `goal`, the turns exactly as you sent them,
and for each turn `expect: {mechanism, route?, want}` describing the **intended** behaviour, not
what happened). Commit it on its own:
`git commit -m "test(evaluation): replay <name>, found improvising" -- apps/orchestrator/evaluation/real/conversations.yaml`.

## 4. Two conversations through the real web

What the person sees is judged too: the answer, «Ver la consulta», the evidence, the chart.

**Start the client if it is not up** (`curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:5173/`
is not `200`):

- `apps/web/node_modules` missing → `npm --prefix apps/web ci`.
- `apps/web/.env.local` missing → copy `apps/web/.env.example` (it is gitignored; it carries
  `VITE_ORCHESTRATOR_URL=http://127.0.0.1:8000` and `VITE_ORCHESTRATOR_TOKEN=demo-token-laura-pays`).
- Start it in the background on **5173** (3000 may be held by another project; the orchestrator
  accepts 5173): `npm --prefix apps/web run dev -- --port 5173`.

**Drive it with Playwright's MCP:**

1. `browser_navigate` to `http://127.0.0.1:5173`, `browser_snapshot`, click the floating button
   «Hablar con FinGuard».
2. Install the capture hook with `browser_evaluate` (again after every navigation). The web client
   asks `/v1/questions/stream` (server-sent events) and answers payment replies through
   `/v1/payments/{id}/confirm`; the hook keeps each final answer and the time it took:

   ```js
   () => {
     if (window.__finguardAnswers) return "ya estaba";
     window.__finguardAnswers = [];
     const original = window.fetch.bind(window);
     window.fetch = async (input, init) => {
       const started = performance.now();
       const response = await original(input, init);
       const url = typeof input === "string" ? input : input.url;
       if (url.includes("/v1/questions") || url.includes("/v1/payments/")) {
         response.clone().text().then((body) => {
           const events = body.split("\n\n")
             .map((chunk) => chunk.split("\n").filter((line) => line.startsWith("data:"))
               .map((line) => line.slice(5).trim()).join(""))
             .filter(Boolean);
           const parsed = events.length ? events.map((data) => JSON.parse(data)) : [JSON.parse(body)];
           const answer = parsed.filter((item) => item.type !== "stage").at(-1);
           answer._seconds = Math.round(performance.now() - started) / 1000;
           window.__finguardAnswers.push(answer);
         }).catch((error) => window.__finguardAnswers.push({ error: String(error) }));
       }
       return response;
     };
     return "instalado";
   }
   ```

3. Each turn: `browser_type` into the «Mensaje» box with `submit: true` (or click a suggestion chip
   in «Acciones sugeridas»), then `browser_wait_for` (`time`, at most 30 s; repeat — a cold model
   takes longer) until `window.__finguardAnswers` has grown and the new bubble is on screen, then
   `browser_snapshot`. Open «Ver la consulta» and «Ver los movimientos» (click their summaries) and
   snapshot again; `browser_take_screenshot` when a chart is drawn. Check
   `browser_console_messages` for errors.
4. Capture the answer: `browser_evaluate` with `() => window.__finguardAnswers.at(-1)` and
   `filename` set to `<tmp>/captures/<name>-<n>.json` — a directory of its own, so the step-6
   glob over `<tmp>/*.json` finds only conversations (if the tool cannot write there, take the
   returned JSON and write the file yourself) — and record the turn with its figure check:

   ```bash
   uv run python scripts/real_conversation.py observe --state <tmp>/web-<name>.json \
     --goal "<objetivo>" --said "<lo que escribiste o el chip>" \
     --response <tmp>/captures/<name>-<n>.json
   ```

Judge the page as well as the record: the bubble says what the API said; «Ver la consulta» shows
the plan that answered; «Ver los movimientos (n de m)» agrees with the count; a chart's title,
axis and values agree with the groups; the chips offered are ones the service can act on; nothing
is broken, cut off or in English. The same two rules hold: no «sí» and no yes button («Sí,
enviar», «Sí, reservar», «Confirmar») on a preview, and a chip is typed or clicked exactly as
shown. The web conversations are named `web-<name>` (the script adds the prefix).

## 5. Judge every turn

Write a `judgement` into every record — in the run's `.json` (`records`) and in each state file
(`turns`):

```json
"judgement": {
  "verdict": "pass",
  "intent": "ok",
  "context": "ok",
  "pending": "—",
  "clarification": "—",
  "recovery": "—",
  "naturalness": 4,
  "mechanism": "follow_up",
  "notes": "Gimnasio en los últimos tres meses, como pidió."
}
```

The rubric, one value per column (Spanish or a short code; `—` when it does not apply):

| Column | Question | Fails when |
|---|---|---|
| `intent` | Did it do what was asked (query, balance, payment, activities, reset…) about the right subject? | Wrong route or wrong subject. |
| `context` | Was what the conversation had settled carried — window, subject, account, person, comparison? | A settled slot was lost or invented. |
| `pending` | If a question was open, was the reply absorbed whole, or the question correctly abandoned for a new intent? | The reply was ignored, misread as something else, or the old question was asked again. |
| `clarification` | If it asked, was asking needed, and was the question useful (naming the subject)? | It asked what it could answer, or asked uselessly. |
| `recovery` | After a failed turn, is this one right? | It is still wrong. |
| `naturalness` | 1–5: would a person find the wording natural? Give the reason in `notes`. | ≤ 2. |
| `mechanism` | Which mechanism the turn exercises (tags below). Keep `expect.mechanism` on replayed turns. | — |
| `notes` | One line: the reason for the verdict, in Spanish. | — |

- `verdict` is `fail` if any column fails, and always if `figure_check` is `mismatch` or `error`
  (the summary enforces this whatever you write).
- On replayed turns, judge against `expect.want`: it is the intended behaviour. A run before a
  change is expected to fail several of them; that is the baseline, judge it honestly.
- `n/a` figures (comparisons, groupings, rankings, balances, payments) are not checked by the
  script. Read them: do a comparison's difference and its two figures agree; do the groups add up
  to the total; is the window the one the sentence says. Write what you checked in `notes`. Never
  query the database yourself with anything but the runner.

Mechanism tags (the battery's vocabulary):

| Tag | The turn tests that… |
|---|---|
| `intent` | the request itself is understood: route, subject, window. |
| `slot_carryover` | an elliptical turn keeps what was settled («y de ocio?»). |
| `pending_resolution` | a reply to an open question is absorbed, whatever slot it fills. |
| `interruption` | a new intent while something is open leaves it cleanly. |
| `follow_up` | a patch on the last answer: narrow, widen, group, flip the direction, correct the subject. |
| `comparison` | a comparison survives its follow-ups (both windows kept, one filter changed). |
| `reference` | a pronoun or «ese» points at something shown. |
| `reset` | «olvídalo», «no era eso», «déjalo» clear what was open. |
| `unnecessary_clarification` | it answers without asking what it can assume (and says what it assumed). |
| `execution` | a payment, transfer or booking does the right thing: preview, cancel, never executes unasked. |

## 6. Report

Add `"findings"` to the run's JSON: three to eight lines in Spanish — what broke, by mechanism, with
the conversation and turn; what changed against the previous run; anything in the preflight that
qualifies the numbers (older data accepted, a cold model, product code not HEAD). Then:

```bash
uv run python scripts/real_conversation.py summarise evaluation/real/<run>.json <tmp>/*.json \
  --previous evaluation/real/<previous-run>.json
```

It folds the improvised and web conversations into the run (a file that is not a conversation,
such as a stray capture, is skipped and named; a conversation named like a replayed one is
refused and nothing is written), enforces the mismatch and error rules, and rewrites
`<run>.md`: preflight, findings, the per-mechanism table (with the previous run's rate
beside each), the recovery rate (after a failed turn, is the next one right?), the figure checks,
latency (median, p95, max, and per turn in every table), then every transcript with your verdicts.
`--previous` is the newest earlier `evaluation/real/*.json`; leave it out only for the first run.

The `.md` is generated: never edit it by hand. Judge the improvised and web turns in their state
files **before** the first `summarise`; afterwards the run's JSON is the record — to change a
verdict, edit the run's JSON and run `summarise evaluation/real/<run>.json` alone (passing a
state file again replaces its turns, judgements included, with the file's). Commit the two
files of the run, and nothing from `<tmp>`:

```bash
git commit -m "docs(evaluation): real conversations <YYYY-MM-DD>, <sha>" -- \
  apps/orchestrator/evaluation/real/<run>.json apps/orchestrator/evaluation/real/<run>.md
```

## 7. Before you say it passed

- Every turn has a `judgement`; the summary shows no `Sin juzgar`.
- No `mismatch` and no `error` anywhere. One is a failed run, whatever the rest says.
- At least three improvised conversations and two web ones are in the run.
- No expectation was edited; any new replayed conversation is in its own commit.
- The person's data was not touched and no payment, transfer or booking was confirmed — not by
  a «sí», not by a yes button.
