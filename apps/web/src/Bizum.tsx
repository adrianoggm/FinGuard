/**
 * Sending money without saying a sentence.
 *
 * Three steps, and the third is not skippable: pick somebody from the list, type an amount, read what
 * would happen and say yes. §4.1 asks for exactly that, and a form gets there differently from a
 * sentence — there is nothing to interpret, so there is no grounding check, and the preview is doing
 * all of the work.
 *
 * The recipient is sent as the name that was chosen, not as an identifier. Banking resolves it against
 * this account's own contacts, so a name matching two people still comes back as a question rather
 * than a guess — which is why picking "Marta Ruiz Gomez" from a list is safe while typing "Marta" is
 * something the service has to interrogate.
 *
 * The preview and everything that can happen to it live in `Approval`, shared with Traspaso: this
 * screen drew that card itself, and so did that one, which is how one silent failure came to be two.
 */

import { useEffect, useMemo, useState } from "react";

import { Approval, replied, type Held, type Reply } from "./Approval";
import {
  confirm,
  contacts as fetchContacts,
  newAttempt,
  payChosen,
  type Contact,
} from "./api";
import { initials, minorUnits, money } from "./format";

const PAID = "PAID";

type Props = { onPaid: () => void };

export function Bizum({ onPaid }: Props) {
  const [people, setPeople] = useState<Contact[] | null>(null);
  const [chosen, setChosen] = useState<Contact | null>(null);
  const [typed, setTyped] = useState("");
  const [held, setHeld] = useState<Held | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // How many payments this screen has completed. It exists only to renew the key below: once one
  // is paid, the same recipient and the same amount is a *second* payment, and replaying the first
  // would hand back an intent the bank has already consumed.
  const [completed, setCompleted] = useState(0);

  useEffect(() => {
    (async () => {
      try {
        setPeople(await fetchContacts());
      } catch {
        setError("No he podido cargar tus contactos.");
      }
    })();
  }, []);

  const amount = minorUnits(typed);
  // A name for this attempt: kept while the recipient and the amount stand, so pressing again after
  // a timeout is the same preview and not a second one, and renewed the moment either changes — the
  // bank refuses a key reused for a different payment rather than answering with the earlier one.
  const attempt = useMemo(newAttempt, [chosen?.contact_id, amount, completed]);

  async function prepare() {
    if (!chosen || amount === null || busy) return;
    setBusy(true);
    try {
      setHeld({
        answer: await payChosen(chosen.display_name, amount, attempt),
        failure: null,
        again: null,
      });
      setError(null);
    } catch {
      setError("No he podido preparar el pago.");
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
      noun: "pago",
    });
    setHeld(next);
    // Only a reply the bank gave a verdict on counts as done, which is exactly `again === null`:
    // a failure and an unknown outcome both leave something to repeat. Holding the key back in
    // those two cases is the safe direction — if the confirmation did execute unseen, the next
    // prepare for the same recipient and amount finds that consumed intent and is refused, where
    // a fresh key would have minted a second payment.
    if (next.again === null) {
      setCompleted((done) => done + 1);
      if (next.answer.outcome === PAID) onPaid();
    }
    setBusy(false);
  }

  function restart() {
    setHeld(null);
    setChosen(null);
    setTyped("");
  }

  // The preview, and whatever came after it.
  if (held) {
    return (
      <Approval
        title="Bizum"
        held={held}
        busy={busy}
        onReply={(said) => void reply(said)}
        onRestart={restart}
      />
    );
  }

  return (
    <div className="screen">
      <h1 className="screen-title">Bizum</h1>
      {error && <p className="empty">{error}</p>}

      <section className="card">
        <h2>¿A quién?</h2>
        {!people && <p className="empty">Cargando…</p>}
        {people?.length === 0 && <p className="empty">No tienes contactos todavía.</p>}
        <ul className="people">
          {people?.map((person) => (
            <li key={person.contact_id}>
              <button
                className={chosen?.contact_id === person.contact_id ? "person on" : "person"}
                onClick={() => setChosen(person)}
              >
                <span className="avatar small" aria-hidden="true">
                  {initials(person.display_name)}
                </span>
                <span className="person-who">
                  <strong>{person.display_name}</strong>
                  {/* The number, always. Two contacts can share a name and this is what tells them
                      apart — the same reason the preview repeats it before anybody says yes. */}
                  <small>{person.phone}</small>
                </span>
              </button>
            </li>
          ))}
        </ul>
      </section>

      {chosen && (
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
            {amount === null ? "Continuar" : `Enviar ${money(amount)} a ${chosen.display_name}`}
          </button>
        </section>
      )}
    </div>
  );
}
