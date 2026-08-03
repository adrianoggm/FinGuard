/**
 * Conversation mode: the sheet that rises over the bank app.
 *
 * Two things here are not decoration.
 *
 * **The plan travels with every figure.** A number from a pipeline with a language model in it should
 * never have to be taken on faith, so each answer carries the query that produced it and the
 * movements behind it, both one tap away. That is the control that makes a residual error survivable:
 * somebody who asked about this month and reads "el mes pasado" can see it.
 *
 * **A payment is answered in words.** When an answer is a preview, the reply goes to the confirm
 * endpoint — including a typed one. Buttons are there because "sí" is what most people want to press,
 * but "sí pero mándale 30" has to reach the service, which knows that is not a yes. This app never
 * decides that somebody agreed.
 */

import { useEffect, useRef, useState } from "react";

import { ask, confirm, type Answer } from "./api";
import { money, signed, day } from "./format";

/** The stage names the service emits, in words somebody can read. */
const STAGES: Record<string, string> = {
  reading: "Leyendo lo que has pedido…",
  planning: "Pensando la consulta…",
  planned: "Consulta lista",
  querying: "Consultando el banco…",
};

const AWAITING = "AWAITING_CONFIRMATION";

type Turn =
  | { who: "user"; text: string }
  | { who: "assistant"; answer: Answer }
  | { who: "error"; text: string };

type Props = { onClose: () => void; onSettled: () => void };

export function Conversation({ onClose, onSettled }: Props) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [draft, setDraft] = useState("");
  const [stage, setStage] = useState<string | null>(null);
  const [pending, setPending] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns, stage]);

  async function send(text: string) {
    if (!text.trim() || busy) return;
    setTurns((previous) => [...previous, { who: "user", text }]);
    setDraft("");
    setBusy(true);
    try {
      // A payment on the table takes the reply, whatever it says. Routing a typed answer back to the
      // question path would lose the confirmation the user is in the middle of giving.
      if (pending) {
        const answer = await confirm(pending, text);
        setTurns((previous) => [...previous, { who: "assistant", answer }]);
        settle(answer);
      } else {
        for await (const step of ask(text)) {
          if ("name" in step) setStage(STAGES[step.name] ?? null);
          else {
            setTurns((previous) => [...previous, { who: "assistant", answer: step }]);
            settle(step);
          }
        }
      }
    } catch (failure) {
      setTurns((previous) => [
        ...previous,
        { who: "error", text: message(failure) },
      ]);
    } finally {
      setStage(null);
      setBusy(false);
    }
  }

  function settle(answer: Answer) {
    const awaiting = answer.outcome === AWAITING ? answer.intent_id : null;
    setPending(awaiting);
    // The balance on the screen behind changed if money moved. Refreshed on any settled payment
    // rather than only on PAID: a refusal we misread as a payment would otherwise leave a stale
    // figure on the home screen, which is the direction that misleads.
    if (!awaiting && answer.intent_id) onSettled();
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
        {turns.length === 0 && <Suggestions onPick={send} />}
        {turns.map((turn, index) => (
          <Bubble key={index} turn={turn} />
        ))}
        {stage && (
          <p className="thinking" role="status">
            {stage}
          </p>
        )}
        {pending && !busy && (
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
          placeholder={pending ? "Responde sí o no" : "Escribe tu pregunta…"}
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
