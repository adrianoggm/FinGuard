/**
 * Moving money between the holder's own accounts.
 *
 * The same three steps as a Bizum — pick, type, approve — and the third is not skippable here
 * either. The money never leaves the household, which lowers what can go wrong but not to nothing:
 * 200 € in the wrong direction between two of your own accounts is a mistake worth being asked
 * about, and the preview is what asks.
 *
 * **The source is fixed and the destination is chosen.** The credential names one account, so that
 * is what money comes out of; a second dropdown for it would be a control that looks like a choice
 * and is not. It also removes the failure a pair of identical dropdowns invites — an account
 * transferring to itself, which balances perfectly and moves nothing.
 *
 * The investment is offered like any other account and labelled for what it is. Putting money into
 * a fund is a real thing to do; what it must never look like is spending, and the balance beside it
 * is a cost basis rather than a valuation.
 */

import { useMemo, useState } from "react";

import {
  confirm,
  newAttempt,
  transferBetweenAccounts,
  type Answer,
  type Holding,
  type Summary,
} from "./api";
import { accountGlyph, accountName, minorUnits, money } from "./format";

const AWAITING = "AWAITING_CONFIRMATION";
const PAID = "PAID";

type Props = { summary: Summary | null; onPaid: () => void };

export function Transfer({ summary, onPaid }: Props) {
  const [target, setTarget] = useState<Holding | null>(null);
  const [typed, setTyped] = useState("");
  const [answer, setAnswer] = useState<Answer | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // How many transfers this screen has completed. It exists only to renew the key below: once one
  // is paid, the same account and the same amount is a *second* transfer, and replaying the first
  // would hand back an intent the bank has already consumed.
  const [completed, setCompleted] = useState(0);

  const holdings = summary?.holdings ?? [];
  // Where the money comes from is the account the credential names, so it cannot also be where it
  // goes. Filtered here rather than refused later: an option that always fails is not an option.
  const source = holdings.find((holding) => holding.booked_minor === summary?.booked_minor);
  const destinations = holdings.filter((holding) => holding.account_id !== source?.account_id);
  const amount = minorUnits(typed);
  // A name for this attempt: kept while the destination and the amount stand, so pressing again
  // after a timeout is the same preview and not a second one, and renewed the moment either
  // changes — the bank refuses a key reused for a different payment rather than answering with the
  // earlier one. `useMemo` is exactly that rule: recompute when the inputs change, keep otherwise.
  const attempt = useMemo(newAttempt, [target?.account_id, amount, completed]);

  async function prepare() {
    if (!target || amount === null || busy) return;
    setBusy(true);
    try {
      setAnswer(await transferBetweenAccounts(target.account_id, amount, target.currency, attempt));
      setError(null);
    } catch {
      setError("No he podido preparar el traspaso.");
    } finally {
      setBusy(false);
    }
  }

  async function answerPreview(reply: string) {
    if (!answer?.intent_id || busy) return;
    setBusy(true);
    try {
      const settled = await confirm(answer.intent_id, reply);
      setAnswer(settled);
      setCompleted((done) => done + 1);
      if (settled.outcome === PAID) onPaid();
    } catch {
      setError("No he podido confirmar el traspaso.");
    } finally {
      setBusy(false);
    }
  }

  function restart() {
    setAnswer(null);
    setTarget(null);
    setTyped("");
  }

  if (answer) {
    const awaiting = answer.outcome === AWAITING;
    return (
      <div className="screen">
        <h1 className="screen-title">Traspaso</h1>
        <section className={awaiting ? "card preview" : "card"}>
          <p>{answer.text}</p>
          {awaiting ? (
            <div className="confirm">
              <button className="yes" disabled={busy} onClick={() => void answerPreview("sí")}>
                Confirmar
              </button>
              <button className="no" disabled={busy} onClick={() => void answerPreview("no")}>
                Cancelar
              </button>
            </div>
          ) : (
            <button className="restart" onClick={restart}>
              Hacer otro
            </button>
          )}
        </section>
      </div>
    );
  }

  return (
    <div className="screen">
      <h1 className="screen-title">Traspaso</h1>
      {error && <p className="empty">{error}</p>}

      {destinations.length === 0 ? (
        // One account is not a reason to draw a form that cannot be completed.
        <p className="empty">Necesitas más de una cuenta para hacer un traspaso.</p>
      ) : (
        <>
          <section className="card">
            <h2>Desde</h2>
            <p className="from-account">
              <span aria-hidden="true">{accountGlyph(source?.kind ?? "CURRENT")}</span>{" "}
              {source ? accountName(source) : "Tu cuenta"} ·{" "}
              {money(summary?.booked_minor ?? 0, summary?.currency)}
            </p>
          </section>

          <section className="card">
            <h2>¿A cuál?</h2>
            <ul className="people">
              {destinations.map((holding) => (
                <li key={holding.account_id}>
                  <button
                    className={target?.account_id === holding.account_id ? "person on" : "person"}
                    onClick={() => setTarget(holding)}
                  >
                    <span className="avatar small" aria-hidden="true">
                      {accountGlyph(holding.kind)}
                    </span>
                    <span className="person-who">
                      <strong>{accountName(holding)}</strong>
                      <small>
                        {money(holding.booked_minor, holding.currency)}
                        {holding.kind === "ASSET" && " · lo invertido"}
                      </small>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </section>

          {target && (
            <section className="card">
              <h2>¿Cuánto?</h2>
              <div className="amount">
                <input
                  value={typed}
                  onChange={(event) => setTyped(event.target.value)}
                  inputMode="decimal"
                  placeholder="0,00"
                  aria-label="Importe"
                  autoFocus
                />
                <span aria-hidden="true">€</span>
              </div>
              {typed && amount === null && (
                <p className="warn">Escríbelo en euros y céntimos, por ejemplo 12,50.</p>
              )}
              <button
                className="primary"
                disabled={amount === null || busy}
                onClick={() => void prepare()}
              >
                {amount === null
                  ? "Continuar"
                  : `Mover ${money(amount)} a ${accountName(target)}`}
              </button>
            </section>
          )}
        </>
      )}
    </div>
  );
}
