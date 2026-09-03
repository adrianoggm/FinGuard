/**
 * The card a payment is answered on, for both screens that have one.
 *
 * Bizum and Traspaso drew this twice, byte for byte apart from the heading, so the defect it was
 * extracted to fix was present twice. One card, one behaviour.
 *
 * **What was wrong.** Each screen set an error sentence when a confirmation failed and rendered it
 * from a site below the preview's early return — that is, on the form, which is not on screen while
 * a preview is open, which is exactly when a confirmation can fail. `finally { setBusy(false) }`
 * then handed both buttons back. Measured on the running app with the confirm call blocked: one
 * state over 2.1 s, same sentence, same two buttons, both enabled, no error. The holder taps
 * Confirmar on a live payment and the app does nothing observable, so they tap again.
 *
 * Banking's `UNIQUE (account_id, idempotency_key)` and its terminal-state trigger are what stop
 * that second tap paying twice. They are not what stop this being a defect: the holder still cannot
 * tell whether their money moved, which on a bank is the failure mode that matters most.
 *
 * **So the error lives here, in the card, immediately above the buttons** — beside the control that
 * produced it, in the only region the holder is looking at. And it is a live region, because a
 * screen reader user got even less than nothing: the whole document has no `aria-live` anywhere.
 */

import { ApiError, type Answer } from "./api";

const AWAITING = "AWAITING_CONFIRMATION";

/** The user's own words, because the service reads them and this client does not decide consent. */
export type Reply = "sí" | "no";

/** The word for what is moving, so one card serves a bizum and a traspaso. */
export type Noun = "pago" | "traspaso";

/** What the screen holds after one reply to a preview. */
export type Held = {
  /** The answer to draw. After a failed reply this is the preview that is still standing. */
  answer: Answer;
  /** What went wrong, in words the holder can act on. Null when nothing did. */
  failure: string | null;
  /**
   * The reply to repeat if the holder asks to. Null when there is nothing to repeat.
   *
   * The reply, not a boolean: a failed *cancellation* must retry the cancellation. Retrying it as
   * a confirmation would send money on the strength of somebody having tapped "Cancelar".
   */
  again: Reply | null;
};

/**
 * The outcome that is an answer without being a verdict.
 *
 * The service reaches it when banking failed *after* the confirmation started, so execution may
 * already have happened. It arrives as a perfectly good 200 carrying
 * `payment_writer.confirmation_unavailable()` — «puedes reintentar esta confirmación sin que se
 * ejecute dos veces» — which is the right sentence, and it must not be drawn over a button that
 * throws the intent away.
 */
const UNKNOWN = "BANKING_UNAVAILABLE";

/**
 * One reply to a preview, and the two things the screen holds afterwards.
 *
 * They travel together because they are one state, the same way `Accounts.load` carries a list and
 * its failure: the version this replaces set the sentence in a second piece of state and left the
 * preview drawing as though nothing had been attempted.
 */
export async function replied(
  ask: () => Promise<Answer>,
  { reply, standing, noun }: { reply: Reply; standing: Answer; noun: Noun },
): Promise<Held> {
  try {
    const settled = await ask();
    // No sentence of our own for this one — the service's is better and is already on the card.
    // What it needs is the button its own words promise.
    return { answer: settled, failure: null, again: settled.outcome === UNKNOWN ? reply : null };
  } catch (failure) {
    // The preview is kept, not cleared. The payment is still the subject and the holder needs the
    // amount and the recipient in front of them to recognise what may or may not have happened.
    return { answer: standing, failure: notice(failure, reply, noun), again: reply };
  }
}

/**
 * Statuses that mean the orchestrator refused the request at its own gate.
 *
 * 401 and 403 are identity and authorisation; 422 is a body the service would not read. In all
 * three the payment machinery was never reached, so nothing can have moved.
 */
const REFUSED_BEFORE_THE_BANK = new Set([401, 403, 422]);

/** The codes worth naming. Anything else keeps to what is known about the money. */
const CAUSE: Record<string, string> = {
  not_authenticated: "No hay credencial. Revisa .env.local.",
  not_authorised: "Esta credencial no puede hacer pagos.",
  feature_disabled: "Los pagos están desactivados en este despliegue.",
};

function nothingMoved(failure: unknown): boolean {
  // Not an `ApiError` means the request never got an answer at all — a blocked or dropped fetch —
  // and that is the case where the confirmation may well have executed.
  if (!(failure instanceof ApiError)) return false;
  return REFUSED_BEFORE_THE_BANK.has(failure.status) || failure.message === "feature_disabled";
}

/**
 * What a failed reply means for the money, in the holder's words.
 *
 * Two classes, and the split is the only thing on this card that matters more than the wording:
 * whether the money may have moved. Everything the client cannot place firmly in the first class
 * goes in the second — the conservative direction, and the same one `refusal_for` takes in the
 * orchestrator, where getting this wrong optimistically means telling somebody a payment failed
 * when it went through.
 *
 * The unknown sentences are deliberately close to `payment_writer.confirmation_unavailable()` and
 * `cancellation_unavailable()`, which the service already says for the same situation one layer
 * down. A transport failure and a bank outage should not read as two different events to the
 * person holding the phone.
 */
export function notice(failure: unknown, reply: Reply, noun: Noun): string {
  if (nothingMoved(failure)) {
    const code = failure instanceof Error ? failure.message : String(failure);
    const because = CAUSE[code] ? ` ${CAUSE[code]}` : "";
    return reply === "sí"
      ? `No he podido confirmar el ${noun} y no se ha movido nada.${because}`
      : `No he podido cancelar el ${noun}, así que sigue pendiente.${because}`;
  }
  return reply === "sí"
    ? `No sé si el banco ha completado el ${noun}. No hagas otro: puedes reintentar esta misma ` +
        `confirmación sin que se ejecute dos veces. Comprueba tus movimientos antes de salir.`
    : `No sé si el banco ha cancelado el ${noun}. Puedes reintentar la cancelación; si ya se ` +
        `hubiera enviado, lo verás en tus movimientos.`;
}

/**
 * A trailing instruction to type an answer, on a screen with no way to type one.
 *
 * The service writes one sentence for a preview and both channels read it: in the chat «Responde sí
 * o no» is exactly right — the composer's own placeholder becomes those words — and on these two
 * screens it asks for something the screen does not accept. So the fix is on the client's side of
 * the boundary, and the trim is the smallest thing that can be cut: the instruction is not a fact
 * about the payment, it is an instruction about a channel, and the channel is the client's own
 * business. Every figure and every name the service wrote survives untouched, because a client that
 * rewords those is a client that can round one.
 *
 * «¿Confirmas?» stays. It is the question the two buttons answer, and it is what marks the sentence
 * as one that needs acting on.
 *
 * Only when trailing, and only that exact instruction. Anything else is drawn whole: if the
 * service's wording changes, the sentence arrives verbatim rather than silently mangled.
 */
export function forButtons(text: string): string {
  return text.replace(/\s*Responde s[íi] o no\.?\s*$/u, "");
}

type Props = {
  title: string;
  held: Held;
  busy: boolean;
  onReply: (reply: Reply) => void;
  onRestart: () => void;
};

export function Approval({ title, held, busy, onReply, onRestart }: Props) {
  const { answer, failure, again } = held;
  const awaiting = answer.outcome === AWAITING;
  return (
    <div className="screen">
      <h1 className="screen-title">{title}</h1>
      <section className={awaiting ? "card preview" : "card"}>
        {/* A receipt or a refusal arrives without anybody navigating, so it is announced. */}
        <p role={awaiting ? undefined : "status"}>{forButtons(answer.text)}</p>
        {awaiting && (
          /* Rendered whether or not there is anything to say, so the region exists before the
             sentence does — a live region inserted already full is a live region some screen
             readers never announce. `:empty` keeps it out of the layout meanwhile. */
          <p className="approval-notice" role="alert">
            {failure}
          </p>
        )}
        {again !== null ? (
          /**
           * One button, and it repeats the reply that failed.
           *
           * Not the pair coming back enabled: that is what produces the second tap, because a
           * screen that has not visibly changed reads as a screen where nothing happened. Not
           * `Cancelar` either — after a confirmation whose outcome is unknown, a button offering
           * to cancel promises to stop a payment that may already be booked, and banking would
           * refuse it as `intent_not_confirmable`, so the label and the effect disagree. And not
           * `Hacer otro`, which would abandon a live intent to start a second payment.
           *
           * Retrying is safe: banking keys the confirmation on the intent's status under
           * `FOR UPDATE`, with a trigger making every terminal state terminal, so a second
           * confirmation of a consumed intent is refused rather than paid. It may well come back
           * «ese pago ya no está pendiente» — which is why the sentence above says so first,
           * rather than letting that answer arrive as a surprise.
           *
           * The way off this card is the tab bar, which stays live, and the sentence above names
           * the screen to look at. A dedicated route to the movements would need a prop this
           * screen is not given.
           */
          <button className="primary" disabled={busy} onClick={() => onReply(again)}>
            Reintentar
          </button>
        ) : awaiting ? (
          <div className="confirm">
            <button className="yes" disabled={busy} onClick={() => onReply("sí")}>
              Confirmar
            </button>
            <button className="no" disabled={busy} onClick={() => onReply("no")}>
              Cancelar
            </button>
          </div>
        ) : (
          <button className="restart" onClick={onRestart}>
            Hacer otro
          </button>
        )}
      </section>
    </div>
  );
}
