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
 * **A payment is answered in words.** When an answer is a preview, the reply goes to the confirm
 * endpoint — including a typed one. Buttons are there because "sí" is what most people want to press,
 * but "sí pero mándale 30" has to reach the service, which knows that is not a yes.
 *
 * **A payment clears the query context.** They are different conversations: after a bizum, "¿y este
 * mes?" is not a follow-up to the bizum, and merging a window onto a payment means nothing.
 */

import { useEffect, useRef, useState } from "react";

import { ask, confirm, type Answer, type PlanContext } from "./api";
import { money, signed, day } from "./format";

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

/** What the client remembers between turns. The service remembers nothing. */
export type Thread = {
  turns: Turn[];
  /** The plan behind the last answered question, or null if there has not been one. */
  plan: PlanContext | null;
  /** The payment waiting for a yes, if any. */
  pending: string | null;
  /**
   * The half-built plan behind the assistant's last question, if it asked one. Sending it back is
   * what turns "este año" into an answer instead of a fresh question with no history.
   */
  unfinished: PlanContext | null;
};

export const EMPTY_THREAD: Thread = { turns: [], plan: null, pending: null, unfinished: null };

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

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [thread.turns, stage]);

  async function send(text: string) {
    if (!text.trim() || busy) return;
    // Read once: the prop is a snapshot, and everything below is computed from this rather than from
    // a value that may already have been replaced.
    const before = thread;
    const turns: Turn[] = [...before.turns, { who: "user", text }];
    onThread({ ...before, turns });
    setDraft("");
    setBusy(true);

    try {
      if (before.pending) {
        // A payment on the table takes the reply, whatever it says. Routing a typed answer back to
        // the question path would lose the confirmation the user is in the middle of giving.
        const answer = await confirm(before.pending, text);
        onThread(settled(before, [...turns, { who: "assistant", answer }], answer, onSettled));
      } else {
        for await (const step of ask(text, before.plan, before.unfinished)) {
          if ("name" in step) setStage(STAGES[step.name] ?? null);
          else {
            turns.push({ who: "assistant", answer: step });
            onThread(settled(before, [...turns], step, onSettled));
          }
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
        {thread.pending && !busy && (
          <div className="confirm">
            <button className="yes" onClick={() => send("sí")}>
              Sí, enviar
            </button>
            <button className="no" onClick={() => send("no")}>
              No
            </button>
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
          placeholder={thread.pending ? "Responde sí o no" : "Escribe tu pregunta…"}
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
 * The thread after an answer: the turns, the plan to carry forward, and any pending payment.
 *
 * The plan is *kept* when an answer does not bring a new one — a refusal or a clarifying question
 * should not erase the context of the question before it, or "¿y este mes?" after one bad turn starts
 * from nothing. A payment is the exception and clears it, because a bizum and a query are not the same
 * conversation.
 */
function settled(before: Thread, turns: Turn[], answer: Answer, onSettled: () => void): Thread {
  const awaiting = answer.outcome === AWAITING ? answer.intent_id : null;
  const wasPayment = answer.intent_id !== null;
  if (!awaiting && wasPayment) onSettled();
  return {
    turns,
    plan: wasPayment ? null : (answer.plan_context ?? before.plan),
    pending: awaiting,
    // Not kept, unlike the plan: a draft belongs to the question that was just asked. Carrying a
    // stale one forward would let a window mentioned three turns later complete a plan the user has
    // long since moved on from.
    unfinished: answer.pending_context ?? null,
  };
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

function Bubble({ turn }: { turn: Turn }) {
  if (turn.who === "user") return <p className="bubble mine">{turn.text}</p>;
  if (turn.who === "error") return <p className="bubble failed">{turn.text}</p>;

  const { answer } = turn;
  return (
    <div className={answer.outcome === AWAITING ? "bubble theirs preview" : "bubble theirs"}>
      <p>{answer.text}</p>
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
                <span className={movement.amount_minor < 0 ? "out" : "in"}>
                  {signed(movement.amount_minor, answer.currency ?? "EUR")}
                </span>
              </li>
            ))}
          </ul>
          {/* Said plainly: these prove the data is real, they do not add up to the total. */}
          <p className="caveat">
            Una muestra de los movimientos, no todos los que suman el total.
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
