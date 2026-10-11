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

import { ask, createVoiceClient, newAttempt, saveMetric, validateVoiceAnswer, type Answer, type ConversationState, type MetricSource } from "./api";
import { categoryName, day, ibanGroups, money, outgoing, signed } from "./format";
import { displayText } from "./displayText";
import { Icon } from "./Icon";
import { voiceEnabled } from "./api";
import { VoiceClient, type VoiceEvent, type VoiceStatus } from "./voice";
import { Chart, chartTitle } from "./Chart";
import { ActivityList } from "./ActivityList";
import { BookingPanel } from "./BookingPanel";

type ChartVisualization = Extract<NonNullable<Answer["visualization"]>, { kind: "chart" }>;

type ChartSource = {
  visualization: ChartVisualization;
  currency: string | null;
  // W-7: the evidence `AnswerEvidence` renders next to a chart -- the query that produced it and
  // the movements behind it -- read off the same answer the chart itself came from (a compound
  // part carries its own; the parent otherwise), not inherited from whichever answer the restyle
  // happened to arrive attached to.
  plan: Answer["plan"];
  groups: Answer["groups"];
  movementCount: Answer["movement_count"];
  movements: Answer["movements"];
};

function chartSource(
  answer: Answer,
  chartId: string,
  inheritedCurrency: string | null = null,
): ChartSource | null {
  const currency = answer.currency ?? inheritedCurrency;
  if (answer.visualization?.kind === "chart" && answer.visualization.chart_id === chartId) {
    return {
      visualization: answer.visualization,
      currency,
      plan: answer.plan,
      groups: answer.groups,
      movementCount: answer.movement_count,
      movements: answer.movements,
    };
  }
  for (const part of answer.parts ?? []) {
    const found = chartSource(part, chartId, currency);
    if (found) return found;
  }
  return null;
}

/** Materialise an additive wire derivation without changing any earlier turn or source rows. */
export function materializeVisualizationDerivation(turns: Turn[], answer: Answer): Answer {
  const derivation = answer.visualization_derivation;
  if (!derivation) return answer;
  let source: ReturnType<typeof chartSource> = null;
  for (let index = turns.length - 1; index >= 0 && !source; index -= 1) {
    const turn = turns[index];
    if (turn?.who === "assistant") source = chartSource(turn.answer, derivation.source_chart_id);
  }
  if (!source) throw new Error("visualization_source_missing");
  return {
    ...answer,
    currency: answer.currency ?? source.currency,
    // W-7: a restyle answer (`VISUAL_UPDATED`) carries `plan: null`, `groups: []`, `movements: []`
    // -- the orchestrator never re-derives evidence for a presentation-only change -- so without
    // this the restyled bubble drew a chart with rows and showed only "¿Por qué este gráfico?": no
    // "Ver la consulta", no "Ver los movimientos", no "Ver los datos". Inherited the same way
    // `currency` and the chart's own `data` already are: only when this answer's own is empty, so
    // a genuinely evidenced answer (should one ever carry a derivation) is never overwritten.
    plan: answer.plan ?? source.plan,
    groups: answer.groups.length > 0 ? answer.groups : source.groups,
    movement_count: answer.movement_count ?? source.movementCount,
    movements: answer.movements.length > 0 ? answer.movements : source.movements,
    visualization: {
      ...source.visualization,
      chart_id: derivation.chart_id,
      spec: derivation.spec,
      // Never inherited. The derived spec may be the one that lifted the limit -- that is what
      // "Ver todos" is -- and carrying the source's over would leave a caveat claiming eight rows
      // under a plot drawing all eighteen.
      limit: derivation.limit ?? null,
      // Kept by reference deliberately: a visual derivation changes presentation, never evidence.
      data: source.visualization.data,
    },
  };
}

export function metricSources(
  turns: Turn[],
  conversation: ConversationState | null = null,
): MetricSource[] {
  const found = new Map<string, MetricSource>();
  const collect = (answer: Answer) => {
    const candidate = answer.save_candidate;
    // A candidate without a chart is a figure worth keeping and nothing a restyle can target, so
    // it is never offered back as a visual source.
    if (candidate?.chart_id) {
      found.set(candidate.chart_id, { ...candidate, chart_id: candidate.chart_id });
    }
    answer.parts?.forEach(collect);
  };
  for (const turn of turns) if (turn.who === "assistant") collect(turn.answer);
  const active = conversation?.visual_contexts?.slice(-5) ?? [];
  return active.flatMap((context) => {
    const source = found.get(context.chart_id);
    return source ? [source] : [];
  });
}

/** The stage names the service emits, in words somebody can read. */
const STAGES: Record<string, string> = {
  transcribing: "Transcribiendo lo que has dicho…",
  reading: "Leyendo lo que has pedido…",
  planning: "Pensando la consulta…",
  planned: "Consulta lista",
  querying: "Consultando el banco…",
};

const AWAITING = "AWAITING_CONFIRMATION";
// A booking's preview has its own outcome (the service never reuses a payment's for a class), and
// it is drawn as the same kind of bubble: something is about to happen on a yes.
const PREVIEWS = new Set([AWAITING, "BOOKING_AWAITING_CONFIRMATION"]);

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
  initialDraft?: string;
};

type VoiceControlClient = Pick<VoiceClient, "start" | "stopCapture" | "cancel" | "close">;

type VoiceUiUpdate = {
  thread?: Thread;
  stage?: string | null;
  clearVoiceError?: boolean;
};

const ONE_SHOT_VOICE_EVENTS = new Set([
  "transcript.final",
  "assistant.text.final",
  "turn.completed",
  "response.cancelled",
]);

export function voiceEventIdentity(event: VoiceEvent): string {
  const suffix = ONE_SHOT_VOICE_EVENTS.has(event.type)
    ? event.turnId ?? "session"
    : `${event.turnId ?? "session"}:${event.sequence}`;
  return `${event.type}:${suffix}`;
}

/** Pure state transition shared by the callback and its same-tick event tests. */
export function applyVoiceEvent(
  current: Thread,
  event: VoiceEvent,
  onSettled: () => void,
): VoiceUiUpdate {
  if (event.type === "assistant.stage") {
    return { stage: STAGES[String(event.stage)] ?? null };
  }
  if (event.type === "transcript.final") {
    const text = typeof event.text === "string" ? event.text : "";
    return text
      ? { thread: { ...current, turns: [...current.turns, { who: "user", text }] }, clearVoiceError: true }
      : { clearVoiceError: true };
  }
  if (event.type === "assistant.text.final") {
    const answer = validateVoiceAnswer(event.answer);
    const rendered = materializeVisualizationDerivation(current.turns, answer);
    const turns = [...current.turns, { who: "assistant" as const, answer: rendered }];
    return { thread: settled(current, turns, rendered, onSettled), stage: null };
  }
  if (event.type === "turn.completed") {
    const conversation = event.conversation;
    if (
      conversation !== null &&
      (typeof conversation !== "object" || Array.isArray(conversation))
    ) {
      throw new Error("Conversación de voz inválida.");
    }
    return conversation === null
      ? { stage: null }
      : { thread: { ...current, conversation: conversation as ConversationState }, stage: null };
  }
  return {};
}

/** Apply the action represented by the voice button to the current session. */
export async function voiceButtonAction(
  status: VoiceStatus,
  client: VoiceControlClient,
): Promise<"stopped" | "barge-in" | "cancelled"> {
  if (status === "listening") {
    client.stopCapture();
    return "stopped";
  }
  if (status === "processing" || status === "speaking") {
    // VoiceClient.start() reuses the open session and performs the fencing/cancellation handshake.
    await client.start();
    return "barge-in";
  }
  if (status === "connecting") {
    client.cancel();
    await client.close();
    return "cancelled";
  }
  return "cancelled";
}

export function Conversation({ thread, onThread, onClose, onSettled, initialDraft = "" }: Props) {
  const [draft, setDraft] = useState(initialDraft);
  const [stage, setStage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const bottom = useRef<HTMLDivElement>(null);
  const choices = recipientChoices(thread);
  const replies = quickReplies(thread);
  // The sentence being attempted, and the name this attempt goes by. A sentence can be a payment —
  // "hazle un bizum de 20 a Marta" prepares one — so retyping it after a request that never came
  // back must reach the same intent rather than making a second. Cleared once the turn is answered,
  // because saying the same thing again after an answer is a second payment and means to be.
  const attempt = useRef<{ text: string; key: string } | null>(null);
  const [voiceStatus, setVoiceStatus] = useState<VoiceStatus>("idle");
  const [voiceError, setVoiceError] = useState<string | null>(null);
  const voice = useRef<VoiceClient | null>(null);
  const threadRef = useRef(thread);
  const seenVoiceEvents = useRef(new Set<string>());
  threadRef.current = thread;

  useEffect(() => () => { void voice.current?.close(); }, []);

  async function toggleVoice() {
    if (voice.current && voiceStatus !== "idle" && voiceStatus !== "error") {
      const action = await voiceButtonAction(voiceStatus, voice.current);
      if (action === "cancelled") {
        voice.current = null;
        setVoiceStatus("idle");
      }
      return;
    }
    setVoiceError(null);
    const client = createVoiceClient({
      onStatus: (status, error) => { setVoiceStatus(status); if (error) setVoiceError(error); },
      onFinished: () => { voice.current = null; },
      onEvent: (event: VoiceEvent) => {
        const key = voiceEventIdentity(event);
        if (seenVoiceEvents.current.has(key)) return;
        seenVoiceEvents.current.add(key);
        try {
          const update = applyVoiceEvent(threadRef.current, event, onSettled);
          if (update.clearVoiceError) setVoiceError(null);
          if ("stage" in update) setStage(update.stage ?? null);
          if (update.thread) {
            // Update synchronously: WebSocket can deliver transcript and answer before
            // React renders the first onThread call.
            threadRef.current = update.thread;
            onThread(update.thread);
          }
        } catch (error) {
          setVoiceError(error instanceof Error ? error.message : "Respuesta de voz inválida.");
          // W-2: an assistant.text.final that failed to materialise (a restyle whose source
          // chart is gone from the thread, say) never rendered a chart, so the fence must not
          // treat this turn as having one either -- audio for it would play over a bubble with
          // no chart, which is exactly the defect the fence exists to rule out.
          return false;
        }
      },
    });
    if (!client) return;
    voice.current = client;
    try { await client.start(); } catch { voice.current = null; }
  }

  function keyFor(text: string): string {
    if (attempt.current?.text !== text) attempt.current = { text, key: newAttempt() };
    return attempt.current.key;
  }

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
      for await (const step of ask(
        text,
        before.conversation,
        keyFor(text),
        "TEXT",
        metricSources(before.turns, before.conversation),
      )) {
        if ("name" in step) setStage(STAGES[step.name] ?? null);
        else {
          const rendered = materializeVisualizationDerivation(turns, step);
          turns.push({ who: "assistant", answer: rendered });
          attempt.current = null;
          onThread(settled(before, turns, rendered, onSettled));
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
        <span className="mark" aria-hidden="true" />
        <div className="sheet-title">
          <strong>FinGuard</strong>
          <small>Pregunta por tus gastos o envía un bizum</small>
        </div>
        <button className="close" onClick={onClose} aria-label="Cerrar">
          <Icon name="close" size={18} />
        </button>
      </header>
      {voiceEnabled && <div className="voice-controls" aria-label="Control de voz">
        <button type="button" className="voice-button" onClick={() => void toggleVoice()} aria-pressed={voiceStatus !== "idle" && voiceStatus !== "error"} aria-label={voiceStatus === "processing" || voiceStatus === "speaking" ? "Interrumpir y hablar" : undefined}>
          <Icon name={voiceStatus === "listening" ? "stop" : "mic"} size={18} />
          {voiceStatus === "idle" || voiceStatus === "error" ? "Iniciar voz" : voiceStatus === "processing" || voiceStatus === "speaking" ? "Interrumpir y hablar" : voiceStatus === "connecting" ? "Cancelar conexión" : "Detener voz"}
        </button>
        <span role="status">{voiceStatus === "connecting" ? "Conectando…" : voiceStatus === "listening" ? "Escuchando" : voiceStatus === "processing" ? "Procesando…" : voiceStatus === "speaking" ? "Reproduciendo" : voiceStatus === "error" ? "Voz no disponible" : "Voz apagada"}</span>
        <VoiceError message={voiceError} />
      </div>}

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
            {/* Still «sí» on the wire: the service reads the same closed yes for both. */}
            <button className="yes" onClick={() => send("sí")}>
              {awaitingBooking(thread) ? "Sí, reservar" : "Sí, enviar"}
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
          <Icon name="send" size={20} />
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

/**
 * Whether a payment or a booking is on the table waiting for a yes. The one thing the input box
 * renders on.
 */
export function awaitingConfirmation(thread: Thread): boolean {
  const workflow = thread.conversation?.workflow;
  return workflow === "PAYMENT_CONFIRMATION" || workflow === "BOOKING_CONFIRMATION";
}

/** Whether the yes on the table books a class rather than moving money. */
export function awaitingBooking(thread: Thread): boolean {
  return thread.conversation?.workflow === "BOOKING_CONFIRMATION";
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

/**
 * A voice-session failure, announced.
 *
 * W-8: this used to be a bare `<small>`, so "Permiso de micrófono denegado…" only ever reached a
 * screen reader if it happened to be focused on this exact node -- `SaveMetricControl`'s own
 * failure (`:role="alert"`, below) already gets this right. Extracted so the one attribute that
 * matters here is exercised by a render test without a DOM, the way this file tests everything
 * else: `role="alert"` alone is enough (it implies `aria-live="assertive"`; a redundant explicit
 * one would only risk the two ever saying something different).
 */
export function VoiceError({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <small className="voice-error" role="alert">
      {message}
    </small>
  );
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
    // Three rails, three labels. The chat gained the ability to confirm a move between the holder's
    // own accounts, and this said "Bizum a Fondo indexado" over it — the sentence above the panel
    // was right and the panel called it a payment to somebody else. Reported.
    const label = transfer ? "Transferencia a" : state.rail === "INTERNAL" ? "Traspaso a" : "Bizum a";
    return (
      <dl className="preview-detail">
        <dt>{label}</dt>
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
  const parts = answer.parts ?? [];
  const chart = chartForResponse(answer);
  const hasVisualization = Boolean(
    chart || parts.some((part) => chartForResponse(part)),
  );
  const className = [
    "bubble",
    "theirs",
    PREVIEWS.has(answer.outcome) ? "preview" : "",
    hasVisualization ? "has-visualization" : "",
  ].filter(Boolean).join(" ");
  return (
    <div className={className}>
      {parts.length > 0 ? (
        <>
          <p>He resuelto {parts.length === 2 ? "las dos consultas" : `${parts.length} consultas`}:</p>
          {parts.map((part, index) => (
            <section className="compound-part" key={index}>
              <p>{part.text}</p>
              <FinancialMetricBlocks answer={part} />
              <Chart visualization={chartForResponse(part)} currency={part.currency} />
              {/* Each part carries its own save authority and always did; this end was dropping it,
                  so a chart asked for in a compound question was the one chart nobody could keep. */}
              {part.save_candidate && (
                <SaveMetricControl
                  token={part.save_candidate.definition_token}
                  suggestion={chartTitle(chartForResponse(part))}
                />
              )}
              <AnswerEvidence answer={part} />
            </section>
          ))}
        </>
      ) : (
        <>
          <p>{answer.text}</p>
          <FinancialMetricBlocks answer={answer} />
          <Chart visualization={chart} currency={answer.currency} />
          {answer.save_candidate && (
            <SaveMetricControl
              token={answer.save_candidate.definition_token}
              suggestion={chartTitle(chart)}
            />
          )}
          <AnswerEvidence answer={answer} />
        </>
      )}
      {(answer.activities?.length || answer.agent_notices?.length) ? (
        <ActivityList slots={answer.activities ?? []} notices={answer.agent_notices ?? []} />
      ) : null}
      <PaymentPanel state={answer.conversation} />
      <BookingPanel state={answer.conversation} />
    </div>
  );
}

/** The server composition decides whether the chart block belongs in this response. */
function chartForResponse(answer: Answer): NonNullable<Answer["visualization"]> | null {
  const visualization = answer.visualization ?? null;
  if (answer.visual_response === undefined || answer.visual_response === null) {
    return visualization;
  }
  const block = answer.visual_response.blocks.find((item) => item.type === "chart");
  return block && visualization?.kind === "chart" && block.chart_id === visualization.chart_id
    ? visualization
    : null;
}

function FinancialMetricBlocks({ answer }: { answer: Answer }) {
  const blocks = answer.visual_response?.blocks.filter((block) => block.type === "metric") ?? [];
  if (blocks.length === 0) return null;
  return (
    <div className="financial-metrics" role="group" aria-label="Resumen financiero">
      {blocks.map((block) => {
        const value = block.field === "difference"
          ? answer.difference_minor
          : block.field === "total"
            ? answer.total_minor
            : answer.movement_count;
        if (value === null || value === undefined) return null;
        const label = block.field === "difference"
          ? "Variación"
          : block.field === "total"
            ? "Total"
            : "Movimientos";
        const display = block.field === "movement_count"
          ? `${value} ${value === 1 ? "movimiento" : "movimientos"}`
          : money(value, answer.currency ?? "EUR");
        return (
          <section className="financial-metric" key={block.block_id} aria-label={label}>
            <span>{label}</span>
            <strong>{display}</strong>
          </section>
        );
      })}
    </div>
  );
}

/**
 * Keep this chart in "Mis métricas".
 *
 * The name is prefilled with the title the service already wrote for the chart, because that is
 * what the metric is: an empty box asked somebody to name a thing that was named on screen half a
 * centimetre above the box. It stays editable — a prefilled name is a suggestion, not a decision.
 */
function SaveMetricControl({ token, suggestion }: { token: string; suggestion?: string | null }) {
  const [title, setTitle] = useState(suggestion ?? "");
  const [state, setState] = useState<"idle" | "saving" | "saved" | "error">("idle");
  const submit = () => {
    if (!title.trim()) return;
    setState("saving");
    void saveMetric(title.trim(), token).then(() => setState("saved")).catch(() => setState("error"));
  };
  if (state === "saved") {
    return <p className="metric-feedback saved">Guardada en «Mis métricas», en Finanzas.</p>;
  }
  return <details className="metric-save-disclosure">
    <summary>Guardar en Mis métricas</summary>
    <div className="metric-save">
    <input aria-label="Nombre de la métrica" value={title} maxLength={80}
      onChange={(event) => setTitle(event.target.value)} placeholder="Nombre de la métrica" />
    <button className="chip" disabled={state === "saving" || !title.trim()} onClick={submit}>
      {state === "saving" ? "Guardando…" : "Guardar métrica"}
    </button>
    {state === "error" && <span className="metric-feedback" role="alert">No se ha podido guardar.</span>}
    </div>
  </details>;
}

/**
 * The plan, the movements and the breakdown of one answer, each behind a disclosure.
 *
 * Exported because "Mis métricas" opens the same answer and owes the reader the same evidence. A
 * second table there would be a second opinion about the same figures.
 */
export function AnswerEvidence({ answer }: { answer: Answer }) {
  // Present exactly when the server sent one AND this answer actually shows a chart -- by the
  // time this renders, a wire `visualization_derivation` has already been materialised into
  // `answer.visualization` (`materializeVisualizationDerivation`, called before the turn is ever
  // pushed), so `answer.visualization` alone is what "shows a chart" means here, same as
  // `Bubble`'s own `hasVisualization` above. §12/R6: inert -- no button, no link, no handler.
  const displayedChart = chartForResponse(answer);
  const breakdownBlock = answer.visual_response?.blocks.find((item) => item.type === "breakdown");
  const showsBreakdown = answer.visual_response
    ? Boolean(breakdownBlock)
    : isBreakdown(answer);
  const explainsAChart = answer.explanation && displayedChart?.kind === "chart";
  return (
    <>
      {explainsAChart && (
        <details>
          <summary>¿Por qué este gráfico?</summary>
          {answer.explanation!.chart_reason && <p>{answer.explanation!.chart_reason}</p>}
          {answer.explanation!.ordering_reason && <p>{answer.explanation!.ordering_reason}</p>}
          {answer.explanation!.scope_summary && <p>{answer.explanation!.scope_summary}</p>}
          {answer.explanation!.provenance && <p>{answer.explanation!.provenance}</p>}
        </details>
      )}
      {answer.plan && (
        <details>
          <summary>Ver la consulta</summary>
          <p className="plan">{displayText(answer.plan)}</p>
        </details>
      )}
      {answer.movements.length > 0 && (
        <details open>
          <summary>
            Ver los movimientos
            {answer.movement_count !== null && answer.movements.length < answer.movement_count
              ? ` (${answer.movements.length} de ${answer.movement_count})`
              : ` (${answer.movements.length})`}
          </summary>
          <ul className="evidence">
            {answer.movements.map((movement, index) => (
              <li key={movement.transaction_id ?? `${movement.occurred_at}-${index}`}>
                <span>{day(movement.occurred_at)}</span>
                <span>{displayText(movement.merchant_name ?? movement.description)}</span>
                <span className={outgoing(movement) ? "out" : "in"}>
                  {signed(
                    outgoing(movement)
                      ? -Math.abs(movement.amount_minor)
                      : Math.abs(movement.amount_minor),
                    movement.currency ?? answer.currency ?? "EUR",
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
              ? `Se muestran ${answer.movements.length} de ${answer.movement_count} movimientos. El resultado incluye los ${answer.movement_count}.`
              : `Se muestran los ${answer.movements.length} movimientos encontrados.`}
          </p>
        </details>
      )}
      {showsBreakdown && (
        <details open={
          answer.conversation?.last_plan?.group_by === "MONTH" ||
          answer.conversation?.last_plan?.group_by === "CATEGORY"
        }>
          <summary>{displayedChart ? "Ver los datos" : "Ver el desglose"}</summary>
        <ul className="groups">
          {answer.groups.map((group) => (
            <li key={group.key}>
              <span>{groupLabel(group.key)}</span>
              <span>{groupFigure(answer, group)}</span>
            </li>
          ))}
        </ul>
        </details>
      )}
    </>
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

/** Render each row in the same unit the question asked for. */
export function groupFigure(answer: Answer, group: Answer["groups"][number]): string {
  const metric = answer.conversation?.last_plan?.metric;
  if (metric === "COUNT") {
    const noun = group.movement_count === 1 ? "movimiento" : "movimientos";
    return `${group.movement_count} ${noun}`;
  }
  if (metric === "AVERAGE") {
    return group.value_minor === null || group.value_minor === undefined
      ? `media no disponible (${group.movement_count})`
      : `media ${money(group.value_minor, answer.currency ?? "EUR")} (${group.movement_count})`;
  }
  return money(group.value_minor ?? group.total_minor, answer.currency ?? "EUR");
}

/** Make backend bucket keys useful in the auditable data disclosure. */
export function groupLabel(key: string): string {
  const month = /^(\d{4})-(\d{2})$/.exec(key);
  if (month && Number(month[2]) >= 1 && Number(month[2]) <= 12) {
    const date = new Date(Number(month[1]), Number(month[2]) - 1, 1);
    return new Intl.DateTimeFormat("es-ES", { month: "long", year: "numeric" }).format(date);
  }
  const day = /^(\d{4})-(\d{2})-(\d{2})$/.exec(key);
  if (day) {
    const date = new Date(Number(day[1]), Number(day[2]) - 1, Number(day[3]));
    if (
      date.getFullYear() === Number(day[1]) &&
      date.getMonth() === Number(day[2]) - 1 &&
      date.getDate() === Number(day[3])
    ) {
      return new Intl.DateTimeFormat("es-ES", { day: "numeric", month: "short", year: "numeric" }).format(date);
    }
  }
  const category = categoryName(key);
  return category === "Otros" ? key : category;
}

function message(failure: unknown): string {
  const code = failure instanceof Error ? failure.message : String(failure);
  // The service's codes, in words. Anything else keeps its code: inventing a friendly sentence for a
  // failure nobody anticipated is how a real problem reads as a shrug.
  if (code === "not_authorised") return "Esta credencial no puede hacer eso.";
  if (code === "not_authenticated") return "No hay credencial. Revisa .env.local.";
  if (code === "feature_disabled") return "Esa parte está desactivada en este despliegue.";
  if (code === "visualization_source_missing") {
    return "No encuentro el gráfico original en esta conversación. Vuelve a generarlo para aplicar el cambio.";
  }
  if (code.includes("Failed to fetch")) return "No puedo hablar con el orquestador.";
  return `No he podido responder (${code}).`;
}
