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
 *
 * The preview and everything that can happen to it live in `Approval`, shared with Bizum.
 */

import { useMemo, useState } from "react";

import { Approval, replied, type Held, type Reply } from "./Approval";
import {
  confirm,
  newAttempt,
  transferBetweenAccounts,
  type Holding,
  type Summary,
} from "./api";
import { accountGlyph, accountName, minorUnits, money } from "./format";

const PAID = "PAID";

type Props = { summary: Summary | null; onPaid: () => void };

export function Transfer({ summary, onPaid }: Props) {
  const [target, setTarget] = useState<Holding | null>(null);
  const [typed, setTyped] = useState("");
  const [held, setHeld] = useState<Held | null>(null);
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
  const excess = overdraft(summary, amount);
  // A name for this attempt: kept while the destination and the amount stand, so pressing again
  // after a timeout is the same preview and not a second one, and renewed the moment either
  // changes — the bank refuses a key reused for a different payment rather than answering with the
  // earlier one. `useMemo` is exactly that rule: recompute when the inputs change, keep otherwise.
  const attempt = useMemo(newAttempt, [target?.account_id, amount, completed]);

  async function prepare() {
    if (!target || amount === null || excess !== null || busy) return;
    setBusy(true);
    try {
      setHeld({
        answer: await transferBetweenAccounts(target.account_id, amount, target.currency, attempt),
        failure: null,
        again: null,
      });
      setError(null);
    } catch {
      setError("No he podido preparar el traspaso.");
    } finally {
      setBusy(false);
    }
  }

  async function reply(said: Reply) {
    const intent = held?.answer.intent_id;
    if (!intent || busy) return;
    setBusy(true);
    const next = await replied(() => confirm(intent, said, held.answer.workflow_id), {
      reply: said,
      standing: held.answer,
      noun: "traspaso",
    });
    setHeld(next);
    // Only a reply the bank gave a verdict on counts as done, which is exactly `again === null`:
    // a failure and an unknown outcome both leave something to repeat. Holding the key back in
    // those two cases is the safe direction — if the confirmation did execute unseen, the next
    // prepare for the same destination and amount finds that consumed intent and is refused, where
    // a fresh key would have minted a second transfer.
    if (next.again === null) {
      setCompleted((done) => done + 1);
      if (next.answer.outcome === PAID) onPaid();
    }
    setBusy(false);
  }

  function restart() {
    setHeld(null);
    setTarget(null);
    setTyped("");
  }

  if (held) {
    return (
      <Approval
        title="Traspaso"
        held={held}
        busy={busy}
        onReply={(said) => void reply(said)}
        onRestart={restart}
      />
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
              {excess && <p className="warn">{excess}</p>}
              <button
                className="primary"
                disabled={amount === null || excess !== null || busy}
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

/**
 * The refusal this screen can make itself, said where the amount can still be changed.
 *
 * The preview used to offer «Te quedarían -7.776,08 €» over a live Confirmar. Banking does refuse
 * it — the balance is recomputed at confirmation and `insufficient_funds` raised — so no money
 * moved. What the holder got was a card asking them to approve something the app already knew would
 * be refused, showing a balance their account cannot hold, and a round trip through "Cancelar" to
 * change the number.
 *
 * Banking's rule, transcribed: `booked < amount` against the booked balance of the account the
 * credential names, with no overdraft allowance. This screen already prints that exact figure under
 * «Desde», so the refusal is made against the number in front of the holder — the strongest form of
 * honesty available to a client whose copy of a balance can always be a moment stale. Banking still
 * recomputes and still has the last word; being stale here can only cost a payment that has to be
 * retried, never one that goes through wrongly.
 *
 * Refused before preparing rather than on the preview, because that is where the holder can act:
 * the amount field is right there. The preview could not do it anyway — `api.ts` drops
 * `balanceAfterMinor` on the way through, so the card cannot see the balance it is printing.
 *
 * A sentence rather than a boolean, so the refusal and the reason it gives cannot drift apart.
 */
export function overdraft(summary: Summary | null, amount: number | null): string | null {
  // The field is labelled € and the amount is parsed as euros. Against an account held in anything
  // else the comparison is meaningless, and a local refusal built on a meaningless comparison would
  // block a payment the bank would have taken. That one is the bank's to decide.
  if (!summary || amount === null || summary.currency !== "EUR") return null;
  if (amount <= summary.booked_minor) return null;
  return (
    `Tienes ${money(summary.booked_minor, summary.currency)} en esta cuenta, ` +
    `así que no puedes mover ${money(amount)}.`
  );
}
