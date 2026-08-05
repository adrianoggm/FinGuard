/**
 * Conversation mode: the sheet that rises over the bank app.
 *
 * **The conversation is state, and it lives in `App` rather than here.** Two things depended on that
 * and both were broken: closing the sheet unmounted this component and threw away every turn, and
 * nothing carried the plan behind the last answer into the next question. The service has the whole
 * merge machinery — `previous_plan`, `plan_merger`, the changed-field set — and the web was calling it
 * with a bare sentence, so "¿y este mes?" arrived with no category, no metric and no window to patch.
 *
 * Three more things here are not decoration.
 *
 * **The plan travels with every figure.** A number from a pipeline with a language model in it should
 * never have to be taken on faith, so each answer carries the query that produced it and the movements
 * behind it, both one tap away. That is the control that makes a residual error survivable: somebody
 * who asked about this month and reads "el mes pasado" can see it.
 *
 * **A payment is answered in words.** When an answer is a preview, every reply stays on the
 * conversational endpoint. Buttons are there because "sí" is what most people want to press, but
 * "sí pero mándale 30" has to reach the service, which knows that is not a yes.
 *
 * **A payment clears the query context.** They are different conversations: after a bizum, "¿y este
 * mes?" is not a follow-up to the bizum, and merging a window onto a payment means nothing.
 */

import { useEffect, useRef, useState } from "react";

import { ask, type Answer, type ConversationState } from "./api";
import { day, ibanGroups, money, outgoing, signed } from "./format";

/** The stage names the service emits, in words somebody can read. */
const STAGES: Record<string, string> = {
  reading: "Leyendo lo que has pedido…",
  planning: "Pensando la consulta…",
  planned: "Consulta lista",
  querying: "Consultando el banco…",
};

const AWAITING = "AWAITING_CONFIRMATION";

export type Turn =
  | { who: "user"; text: string }
  | { who: "assistant"; answer: Answer }
  | { who: "error"; text: string };

/**
 * What the client remembers between turns. The service remembers nothing.
 *
 * Two fields, and only one of them is state: the turns on screen, and the conversation the service
 * handed back. This used to be five nullable fields the client combined by hand — the answered plan,
 * the half-read query, the half-built payment, the payment awaiting a yes, the last refusal — and
 * every conversational defect added another one. Deciding what a combination of them meant is the
 * orchestrator's job, and this end got it wrong every time a state was added.
 */
export type Thread = {
  turns: Turn[];
  conversation: ConversationState | null;
};

export const EMPTY_THREAD: Thread = { turns: [], conversation: null };

type Props = {
  thread: Thread;
  onThread: (next: Thread) => void;
  onClose: () => void;
  onSettled: () => void;
};

export function Conversation({ thread, onThread, onClose, onSettled }: Props) {
  const [draft, setDraft] = useState("");
  const [stage, setStage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);
  const choices = recipientChoices(thread);
  const replies = quickReplies(thread);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [thread.turns, stage]);

  /**
   * Sending a turn, optionally with the contact the user tapped.
   *
   * The identifier is the one thing this client puts *into* the state, and it is a choice among rows
   * the service listed rather than a reading of anything — the sentence beside it is the display
   * name, so a service that ignored the field would behave exactly as it does today.
   */
  async function send(text: string, chosen?: string) {
    if (!text.trim() || busy) return;
    // Read once: the prop is a snapshot, and everything below is computed from this rather than from
    // a value that may already have been replaced.
    const before = chosen ? withChoice(thread, chosen) : thread;
    const turns: Turn[] = [...before.turns, { who: "user", text }];
    onThread({ ...before, turns });
    setDraft("");
    setBusy(true);

    try {
      // Every turn goes through the conversational route, including a yes or no. That route owns
      // the state machine and returns either the same PAYMENT_CONFIRMATION (an unclear reply) or an
      // idle conversation (paid, cancelled or refused). Calling the bare payment endpoint here
      // returned no conversation state, so `settled` correctly retained the old preview and left
      // the buttons pointing at an intent the bank had already consumed.
      for await (const step of ask(text, before.conversation)) {
        if ("name" in step) setStage(STAGES[step.name] ?? null);
        else {
          turns.push({ who: "assistant", answer: step });
          onThread(settled(before, [...turns], step, onSettled));
        }
      }
    } catch (failure) {
      onThread({ ...before, turns: [...turns, { who: "error", text: message(failure) }] });
    } finally {
      setStage(null);
      setBusy(false);
    }
  }

  return (
    <section className="sheet" aria-label="Conversación">
      <header className="sheet-head">
        <span className="handle" aria-hidden="true" />
        <div className="sheet-title">
          <strong>FinGuard</strong>
          <small>Pregunta por tus gastos o envía un bizum</small>
        </div>
        <button className="close" onClick={onClose} aria-label="Cerrar">
          ✕
        </button>
      </header>

      <div className="turns">
        {thread.turns.length === 0 && <Suggestions onPick={send} />}
        {thread.turns.map((turn, index) => (
          <Bubble key={index} turn={turn} />
        ))}
        {stage && (
          <p className="thinking" role="status">
            {stage}
          </p>
        )}
        {awaitingConfirmation(thread) && !busy && (
          <div className="confirm">
            <button className="yes" onClick={() => send("sí")}>
              Sí, enviar
            </button>
            <button className="no" onClick={() => send("no")}>
              No
            </button>
          </div>
        )}
        {choices.length > 0 && !busy && (
          <div className="recipient-choices" role="group" aria-label="Elige el contacto">
            {choices.map((candidate) => (
              <button
                key={candidate.contact_id}
                onClick={() => send(candidate.display_name, candidate.contact_id)}
              >
                <strong>{candidate.display_name}</strong>
                <small>{candidate.phone}</small>
              </button>
            ))}
          </div>
        )}
        {replies.length > 0 && !busy && !awaitingConfirmation(thread) && choices.length === 0 && (
          <div className="suggestions" role="group" aria-label="Acciones sugeridas">
            {replies.map((reply) => (
              <button key={reply} onClick={() => send(reply)}>
                {reply}
              </button>
            ))}
          </div>
        )}
        <div ref={bottom} />
      </div>

      <form
        className="composer"
        onSubmit={(event) => {
          event.preventDefault();
          void send(draft);
        }}
      >
        <input
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder={
            awaitingConfirmation(thread)
              ? "Responde sí o no"
              : choices.length > 0
                ? "Elige un contacto"
                : "Escribe tu pregunta…"
          }
          aria-label="Mensaje"
          autoFocus
        />
        <button type="submit" disabled={busy || !draft.trim()} aria-label="Enviar">
          ▶
        </button>
      </form>
    </section>
  );
}

/**
 * The thread after an answer: the turns, and the state the service says it is in.
 *
 * This function used to decide what to keep — the plan unless a payment cleared it, the draft
 * replaced every turn, the payment kept unless it resolved — from a pattern of nulls. All of that
 * now arrives decided. What is left is applying it, and one side effect the service cannot perform:
 * telling the screens behind the sheet to reload when a payment moved money.
 */
export function settled(
  before: Thread,
  turns: Turn[],
  answer: Answer,
  onSettled: () => void,
): Thread {
  const resolved =
    answer.intent_id !== null && answer.conversation?.workflow !== "PAYMENT_CONFIRMATION";
  if (resolved) {
    // The balance and the movements have just changed, so whatever is showing them is stale.
    onSettled();
  }
  return { turns, conversation: answer.conversation ?? before.conversation };
}

/** Whether a payment is on the table waiting for a yes. The one thing the input box renders on. */
export function awaitingConfirmation(thread: Thread): boolean {
  return thread.conversation?.workflow === "PAYMENT_CONFIRMATION";
}

/** The intent a confirmation is about, when there is one. */
export function pendingIntent(thread: Thread): string | null {
  return awaitingConfirmation(thread) ? (thread.conversation?.intent_id ?? null) : null;
}

/**
 * The thread with the tapped contact on its half-built payment.
 *
 * Written rather than echoed, which every other field of the conversation is. It is safe for the
 * same reason the candidates were safe to render: the identifier came from the service's own list,
 * and the service resolves it against the account's contacts before anybody is paid.
 */
export function withChoice(thread: Thread, contactId: string): Thread {
  const conversation = thread.conversation;
  if (!conversation?.payment) return thread;
  return {
    ...thread,
    conversation: {
      ...conversation,
      payment: { ...conversation.payment, recipient_contact_id: contactId },
    },
  };
}

/** Structured choices for the one payment state that asks the user to pick a person. */
export function recipientChoices(thread: Thread) {
  const conversation = thread.conversation;
  if (
    conversation?.workflow !== "PAYMENT_IN_PROGRESS" ||
    conversation.awaiting !== "RECIPIENT_CHOICE"
  ) {
    return [];
  }
  return conversation.candidates;
}

/** Actions attached to the latest answer. Older suggestions must never remain live. */
export function quickReplies(thread: Thread): string[] {
  const last = thread.turns.at(-1);
  return last?.who === "assistant" ? last.answer.suggestions : [];
}

const EXAMPLES = [
  "¿Cuánto gasté en gasolina el mes pasado?",
  "¿En qué me he gastado más este mes?",
  "Hazle un bizum de 20 euros a Marta Solis",
];

function Suggestions({ onPick }: { onPick: (text: string) => void }) {
  return (
    <div className="suggestions">
      <p>Prueba con:</p>
      {EXAMPLES.map((example) => (
        <button key={example} onClick={() => onPick(example)}>
          {example}
        </button>
      ))}
    </div>
  );
}

/**
 * What the payment state looks like on screen, for the two states that were invisible.
 *
 * **A confirmation.** The sentence says the amount and the name, and until now that was all a
 * person had. A Bizum and a transfer drew the identical prompt, so the one thing that distinguishes
 * what is about to happen — twenty-four characters of account number — was nowhere on the screen
 * somebody taps "Sí" on. §4.1 asks for an explicit confirmation of a *specific* operation; a
 * confirmation of an operation you cannot fully see is a formality.
 *
 * **A payment paused by a change of subject.** The service keeps it and says so in words once, and
 * a sentence scrolls away. Follow-up 9 of the phase-4 review: without a visible marker, "sigue con
 * el bizum" is something the user has to know to say, about a state they have no way to see.
 */
export function PaymentPanel({ state }: { state: ConversationState | null | undefined }) {
  if (!state) return null;

  if (state.workflow === "PAYMENT_CONFIRMATION" && state.amount_minor !== null) {
    const transfer = state.rail === "TRANSFER";
    return (
      <dl className="preview-detail">
        <dt>{transfer ? "Transferencia a" : "Bizum a"}</dt>
        <dd>{state.recipient ?? "—"}</dd>
        {transfer && state.recipient_iban && (
          <>
            <dt>A la cuenta</dt>
            {/* Unmasked and grouped. Recognising the number is the whole job of this step. */}
            <dd className="iban">{ibanGroups(state.recipient_iban)}</dd>
          </>
        )}
        {state.concept && (
          <>
            <dt>Concepto</dt>
            <dd>{state.concept}</dd>
          </>
        )}
        <dt>Importe</dt>
        <dd className="preview-amount">{money(state.amount_minor, state.currency ?? "EUR")}</dd>
      </dl>
    );
  }

  if (state.workflow === "PAYMENT_IN_PROGRESS" && state.suspended) {
    const draft = state.payment;
    const missing = draft?.amount_minor === null ? "falta el importe" : "falta el destinatario";
    return (
      <p className="paused" role="status">
        <span aria-hidden="true">⏸</span> Bizum pausado
        {draft?.recipient_name ? ` · ${draft.recipient_name}` : ""} · {missing}
      </p>
    );
  }

  return null;
}

function Bubble({ turn }: { turn: Turn }) {
  if (turn.who === "user") return <p className="bubble mine">{turn.text}</p>;
  if (turn.who === "error") return <p className="bubble failed">{turn.text}</p>;

  const { answer } = turn;
  return (
    <div className={answer.outcome === AWAITING ? "bubble theirs preview" : "bubble theirs"}>
      <p>{answer.text}</p>
      <PaymentPanel state={answer.conversation} />
      {answer.plan && (
        <details>
          <summary>Ver la consulta</summary>
          <p className="plan">{answer.plan}</p>
        </details>
      )}
      {answer.movements.length > 0 && (
        <details>
          <summary>
            Ver los movimientos
            {answer.movement_count ? ` (${answer.movement_count})` : ""}
          </summary>
          <ul className="evidence">
            {answer.movements.map((movement, index) => (
              <li key={index}>
                <span>{day(movement.occurred_at)}</span>
                <span>{movement.merchant_name ?? movement.description}</span>
                <span className={outgoing(movement) ? "out" : "in"}>
                  {signed(
                    outgoing(movement)
                      ? -Math.abs(movement.amount_minor)
                      : Math.abs(movement.amount_minor),
                    answer.currency ?? "EUR",
                  )}
                </span>
              </li>
            ))}
          </ul>
          {/*
            Only when it *is* a sample. The line was rendered whenever there were any movements,
            so an answer showing both of its two movements still said it was showing some of
            them — a caveat that is false is worse than none, because it makes the true ones
            unbelievable.
          */}
          <p className="caveat">
            {answer.movement_count !== null && answer.movements.length < answer.movement_count
              ? "Una muestra de los movimientos, no todos los que suman el total."
              : `Se muestran los ${answer.movements.length} movimientos encontrados.`}
          </p>
        </details>
      )}
      {isBreakdown(answer) && (
        <ul className="groups">
          {answer.groups.map((group) => (
            <li key={group.key}>
              <span>{group.key}</span>
              <span>{money(group.total_minor, answer.currency ?? "EUR")}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/**
 * Whether the groups are a real breakdown.
 *
 * An ungrouped query still comes back with one row keyed "total", because the engine always groups by
 * something. Rendering it restates the figure directly under itself, which reads as a bug. The
 * service applies the same rule to the sentence it writes (`answer_writer._is_breakdown`), so this
 * mirrors it rather than inventing a second opinion about the same answer.
 */
function isBreakdown(answer: Answer): boolean {
  if (answer.groups.length === 0) return false;
  return !(answer.groups.length === 1 && answer.groups[0]?.key === "total");
}

function message(failure: unknown): string {
  const code = failure instanceof Error ? failure.message : String(failure);
  // The service's codes, in words. Anything else keeps its code: inventing a friendly sentence for a
  // failure nobody anticipated is how a real problem reads as a shrug.
  if (code === "not_authorised") return "Esta credencial no puede hacer eso.";
  if (code === "not_authenticated") return "No hay credencial. Revisa .env.local.";
  if (code === "feature_disabled") return "Esa parte está desactivada en este despliegue.";
  if (code.includes("Failed to fetch")) return "No puedo hablar con el orquestador.";
  return `No he podido responder (${code}).`;
}
