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
 */

import { useEffect, useMemo, useState } from "react";

import {
  confirm,
  contacts as fetchContacts,
  newAttempt,
  payChosen,
  type Answer,
  type Contact,
} from "./api";
import { initials, minorUnits, money } from "./format";

const AWAITING = "AWAITING_CONFIRMATION";
const PAID = "PAID";

type Props = { onPaid: () => void };

export function Bizum({ onPaid }: Props) {
  const [people, setPeople] = useState<Contact[] | null>(null);
  const [chosen, setChosen] = useState<Contact | null>(null);
  const [typed, setTyped] = useState("");
  const [answer, setAnswer] = useState<Answer | null>(null);
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
      setAnswer(await payChosen(chosen.display_name, amount, attempt));
      setError(null);
    } catch {
      setError("No he podido preparar el pago.");
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
      setError("No he podido confirmar el pago.");
    } finally {
      setBusy(false);
    }
  }

  function restart() {
    setAnswer(null);
    setChosen(null);
    setTyped("");
  }

  // The preview, and whatever came after it.
  if (answer) {
    const awaiting = answer.outcome === AWAITING;
    return (
      <div className="screen">
        <h1 className="screen-title">Bizum</h1>
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
