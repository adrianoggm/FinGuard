/**
 * The orchestrator, as this app needs it. The only file that knows a URL.
 *
 * It never calls banking-api. Every figure on screen came through the orchestrator, which is what
 * makes "the assistant holds no financial rules" checkable rather than claimed.
 *
 * The token is a development credential read from the environment at build time. That is defensible
 * only because everything here is loopback (rule 13); a real deployment gets a short-lived token
 * from an issuer and this becomes the one place that changes.
 */

import { VoiceClient, type VoiceEvent, type VoiceStatus } from "./voice";

const BASE = (import.meta.env.VITE_ORCHESTRATOR_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");
const TOKEN = import.meta.env.VITE_ORCHESTRATOR_TOKEN ?? "";

export const voiceGatewayUrl = import.meta.env.VITE_VOICE_GATEWAY_URL ?? "";
export const voiceEnabled = voiceGatewayUrl !== "" && TOKEN !== "";

export type VoiceCallbacks = {
  onEvent: (event: VoiceEvent) => void;
  onStatus: (status: VoiceStatus, error?: string) => void;
  onFinished?: () => void;
};

export function createVoiceClient(callbacks: VoiceCallbacks): VoiceClient | null {
  if (!voiceEnabled) return null;
  // Keep the credential in this module's closure. The UI receives a configured client, never the token.
  return new VoiceClient({ gatewayUrl: voiceGatewayUrl, token: TOKEN, ...callbacks });
}

export const configured = TOKEN !== "";

function headers(attempt?: string): Record<string, string> {
  const common = { Authorization: `Bearer ${TOKEN}`, "Content-Type": "application/json" };
  // Names this attempt so a resend of it is the same preview rather than a second one. The service
  // mints one when it is absent, which is the same as having none: a key that differs between a
  // request and its retry cannot tell them apart. Whoever knows what "one attempt" means has to be
  // the one to say it, and that is the screen, not this module.
  return attempt ? { ...common, "Idempotency-Key": attempt } : common;
}

/**
 * A name for one attempt at one payment.
 *
 * Kept across a failure and renewed when the payment changes: resending after a timeout must not
 * become a second intent, and reusing it for a different amount is refused by the bank with a 409
 * rather than answered with the earlier preview. So a screen holds one per thing it is about to
 * send, and takes a new one when what it is sending changes.
 */
export function newAttempt(): string {
  return crypto.randomUUID();
}

export type Movement = {
  occurred_at: string;
  description: string;
  /**
   * Signed on the movement list, unsigned in a chat answer — a query filtered by EXPENSE comes back
   * from banking with positive amounts and the direction beside them. Read `direction` when it is
   * there and fall back to the sign when it is not: deriving it from the amount alone is what showed
   * every expense in the chat as "+22,11 €".
   */
  amount_minor: number;
  /** "EXPENSE" or "INCOME". Present in chat answers; absent on screens that send signed amounts. */
  direction?: string;
  merchant_name: string | null;
  category: string | null;
  transaction_type?: string | null;
  counterparty_name?: string | null;
  counterparty_type?: string;
  receipt_category?: string | null;
  /** Present on the movement list, which is the only screen that can refile one. */
  transaction_id?: string | null;
  /** Whether the category is the holder's own rather than the bank's guess. */
  category_is_custom?: boolean;
  /**
   * Which of the four layers decided the category: "BANK" (what the statement said), "SYSTEM"
   * (what FinGuard inferred), "USER" (the holder's override) or "NONE" (nobody has). NONE is not a
   * gap to fill with a guess — it is the only state a person can be asked to correct.
   */
  category_source?: string;
  /**
   * Money that stayed between the holder's own accounts. Banking already leaves these out of
   * totals; a list still has to say so, because 200 € to the piggy bank drawn like a purchase is
   * spending that never happened (ADR-009 §2).
   */
  is_internal?: boolean;
};

/**
 * One of the holder's accounts.
 *
 * `kind` has to be read before anything is added up. On an `ASSET` the balance is what was paid,
 * not what it is worth — there is no price source — so a total across all three without saying so
 * reports a purchase price as somebody's money.
 *
 * `display_name` is null when the holder never named the account. Fall back to the kind, never to
 * the reference: "ES-0001-2" is not something anybody recognises as theirs.
 */
export type Holding = {
  account_id: string;
  kind: "CURRENT" | "SAVINGS" | "ASSET";
  display_name: string | null;
  currency: string;
  booked_minor: number;
};

export type Summary = {
  /**
   * Of the account the credential names, not of the holder. `holdings` is all of them, so the sum
   * of those is a different number — see `spendable` in format.ts for the one this app shows.
   */
  currency: string;
  booked_minor: number;
  available_minor: number;
  as_of: string;
  movements: Movement[];
  /** Empty from a service that predates multi-account, which is why nothing here may require it. */
  holdings?: Holding[];
};

/**
 * The plan behind an answer, in fields.
 *
 * It goes back on the next question as `previous_plan`, which is how a follow-up works: the service
 * merges the fields the new sentence changes onto this instead of re-deriving the whole plan from
 * three words. Without it "¿y este mes?" loses the category, the metric and everything else — the
 * merge machinery exists on the server and this is the half that feeds it.
 */
export type PlanContext = {
  direction: string;
  period: string | null;
  n: number | null;
  start: string | null;
  end: string | null;
  categories: string[];
  excluded_categories?: string[];
  merchants: string[];
  /** Explicit substring matching; exact merchant names remain in `merchants`. */
  merchant_terms?: string[];
  excluded_merchants?: string[];
  counterparties?: string[];
  excluded_counterparties?: string[];
  counterparty_types?: ("MERCHANT" | "PERSON" | "EMPLOYER" | "BILLER" | "ACCOUNT" | "BANK" | "UNKNOWN")[];
  group_by: string;
  metric: string;
  group_limit?: number | null;
  include_internal?: boolean;
  internal_only?: boolean;
  across_accounts?: boolean;
  only_account_id?: string | null;
  only_account_ids?: string[];
  transaction_ids?: string[];
  transaction_types?: string[];
};

/** A payment being built. Part of the conversation state, never held on its own. */
export type PaymentDraft = {
  recipient_name: string | null;
  /**
   * The contact the user picked, when they picked one. The only field of the conversation this
   * client ever *writes*, and it writes an identifier the service itself listed rather than a
   * reading of anything: sending the display name back means the ambiguity is resolved by matching
   * a name again, which is what made "Marta Vega" three turns of the same question.
   */
  recipient_contact_id: string | null;
  amount_minor: number | null;
  currency: string;
  concept: string | null;
};

/** One of several people a recipient could be, with the identifier that resolves the ambiguity. */
export type Candidate = {
  contact_id: string;
  display_name: string;
  phone: string;
};

/** Which workflow is under way, if any. */
export type Workflow =
  | "FINANCIAL_CLARIFICATION"
  | "ACCOUNT_SCOPE"
  | "UNSUPPORTED_CAPABILITY"
  | "PAYMENT_IN_PROGRESS"
  | "TRANSFER_IN_PROGRESS"
  | "PAYMENT_CONFIRMATION";

/** What the last turn asked the user for. */
export type Slot =
  | "WINDOW"
  | "YEAR"
  | "CATEGORY"
  | "CORRECTION"
  | "AMOUNT"
  | "RECIPIENT"
  | "RECIPIENT_CHOICE"
  | "CONFIRMATION"
  // Which of the holder's accounts. Asked rarely: a question naming none is answered with a
  // breakdown, because a breakdown is a better answer than a question.
  | "ACCOUNT";

/**
 * Where the conversation is, as the service sees it.
 *
 * Stored and echoed back without being interpreted. That is the whole point of the shape: this
 * client used to hold five independent fields — the answered plan, the half-read query, the
 * half-built payment, the payment awaiting a yes, the last refusal — and decide what each
 * combination of nulls meant. Deciding that is the orchestrator's job, and every new state it
 * gained was a new way for the client to get it wrong.
 *
 * Read it to *render* (which workflow, which slot, which candidates); never to work out what to
 * keep.
 */
export type ConversationState = {
  workflow: Workflow | null;
  awaiting: Slot | null;
  // What the last turn asked for even when it left no workflow behind. Round-tripped like the
  // rest and never read here: it is how the service notices it has asked the same question twice
  // and offers choices instead of a third open question.
  last_asked: Slot | null;
  // Whether the last turn said how much money there is. Round-tripped and never read here: it is
  // how the service knows that "¿y en la hucha?" is about the balance rather than about spending.
  answered_the_balance: boolean;
  // Category explicitly highlighted by the previous answer; echoed without interpretation.
  referenced_category?: string | null;
  // Typed result row and exact movement evidence shown by the previous answer. The UI only
  // round-trips these; the orchestrator alone decides what a later pronoun means.
  referenced_group_by?: "CATEGORY" | "MERCHANT" | "COUNTERPARTY" | "ACCOUNT" | null;
  referenced_group_key?: string | null;
  referenced_transaction_ids?: string[];
  // User-taught account names. Opaque conversational memory: the server revalidates each account
  // identifier against the authenticated holder before applying it.
  account_aliases?: { alias: string; account_id: string; display_name: string }[];
  account_selection_mode?: "ALL" | "SELECTED" | null;
  account_selection_ids?: string[];
  counterparty_aliases?: {
    alias: string;
    counterparty_name: string;
    counterparty_type: "MERCHANT" | "PERSON" | "EMPLOYER" | "BILLER" | "ACCOUNT" | "BANK" | "UNKNOWN";
  }[];
  // One-turn reference used only to understand an immediate "otro" after a completed payment.
  recent_payment_rail?: "BIZUM" | "TRANSFER" | null;
  // What has been said, oldest first and bounded by the service. Every other field here describes
  // the turn immediately before; this is the thread, and it is what lets an ellipsis several turns
  // deep still resolve. Round-tripped like the rest and never read here — rendering the chat uses
  // the messages this client already holds, not the service's copy of them.
  turns?: { sentence: string; outcome: string; plan?: string | null; question?: string | null }[];
  // Opaque, value-free chart memory. The client echoes it; only the orchestrator interprets it.
  visual_contexts?: {
    chart_id: string;
    spec: Record<string, unknown>;
    profile: {
      fields: { name: string; type: "nominal" | "quantitative" | "temporal" }[];
      cardinality: number;
      metric?: string | null;
      unit?: "money" | "count" | "ratio" | "unknown" | null;
      positive: boolean;
      mutually_exclusive: boolean;
      verified_total: boolean;
    };
  }[];
  last_plan: PlanContext | null;
  prior_plan?: PlanContext | null;
  retry_plan: PlanContext | null;
  last_ratio_kind?: "SHARE" | "SAVINGS_RATE" | "PERCENT_CHANGE" | "NET_CASH_FLOW" | null;
  last_ratio_part?: PlanContext | null;
  last_ratio_whole?: PlanContext | null;
  pending_ratio_kind?: "SHARE" | "SAVINGS_RATE" | "PERCENT_CHANGE" | "NET_CASH_FLOW" | null;
  pending_ratio_part?: PlanContext | null;
  pending_ratio_whole?: PlanContext | null;
  last_comparison_earlier?: PlanContext | null;
  last_comparison_latest?: PlanContext | null;
  draft: PlanContext | null;
  pending_plan?: PlanContext | null;
  pending_requested_count?: number | null;
  pending_explicit_visualization?: boolean;
  account_candidates?: { account_id: string; display_name: string; kind: string }[];
  capability: string | null;
  payment: PaymentDraft | null;
  suspended: boolean;
  candidates: Candidate[];
  // TRANSFER_IN_PROGRESS: where the money is going and how much, whichever of the two the sentence
  // supplied. Round-tripped so the reply that fills in the other one can be understood.
  transfer_to: string | null;
  transfer_amount_minor: number | null;
  intent_id: string | null;
  recipient: string | null;
  amount_minor: number | null;
  // What the preview is denominated in. It travels with the amount because the two describe one
  // figure: without it the service rebuilt every echoed confirmation as euros, so a payment
  // prepared in another currency came back from one round trip as the same number of euros.
  currency: string | null;
  concept: string | null;
  // How the money leaves, and where. Exactly one of the two identifiers is filled: a Bizum names a
  // person and the phone is already on `payment`, a transfer names the account. The IBAN arrives
  // whole and unmasked on purpose — approving a transfer means recognising twenty-four characters,
  // and four of them with dots between is not something anybody can check against a note.
  // INTERNAL is a move between the holder's own accounts, which the chat can now stage and confirm.
  // It was missing here, so the preview panel fell through to the Bizum label and drew "Bizum a
  // Fondo indexado" over a transfer.
  rail: "BIZUM" | "TRANSFER" | "INTERNAL" | null;
  recipient_iban: string | null;
  // `rank` used to be here and left with the refusal it served. The service rejects fields it does
  // not know, so a stale one on this type is a 422 waiting for whoever fills it in.
};

export type Answer = {
  outcome: string;
  text: string;
  question: string | null;
  intent_id: string | null;
  plan: string | null;
  // Present with a question back: the reading that question interrupted. Echoed on the next turn
  // so the reply finishes the plan instead of starting a new one.
  // Where the conversation is after this turn. Sent back verbatim on the next one.
  conversation: ConversationState | null;
  currency: string | null;
  total_minor: number | null;
  movement_count: number | null;
  groups: {
    key: string;
    total_minor: number;
    movement_count: number;
    value_minor?: number | null;
  }[];
  movements: Movement[];
  visualization?: Visualization | null;
  visualization_derivation?: {
    source_chart_id: string;
    chart_id: string;
    spec: Record<string, unknown>;
  } | null;
  suggestions: string[];
  /** Independent read-only questions asked in one turn, each with its own plan and evidence. */
  parts?: Answer[];
  save_candidate?: { chart_id: string; definition_token: string } | null;
};

export type Visualization =
  | {
      kind: "chart";
      chart_id: string;
      unit: "money" | "count" | "ratio";
      spec: Record<string, unknown>;
      data: Record<string, unknown>[];
      /** Rows the plot draws, when it draws fewer than `data` holds. Null when it draws them all. */
      limit?: number | null;
    }
  | {
      kind: "kpi";
      value_field: "value_minor";
      unit: "money" | "count";
      data: Record<string, unknown>[];
    };

/** Narrow transport boundary for answers arriving through the voice gateway. */
export function validateVoiceAnswer(value: unknown): Answer {
  return validateVoiceAnswerAt(value, 0);
}

function validateVoiceAnswerAt(value: unknown, depth: number): Answer {
  if (depth > 2) throw new Error("Respuesta de voz inválida.");
  if (!value || typeof value !== "object") throw new Error("Respuesta de voz inválida.");
  const body = value as Record<string, unknown>;
  const nullableString = (field: unknown) => field === null || typeof field === "string";
  const finiteNumber = (field: unknown) => typeof field === "number" && Number.isFinite(field);
  const nullableNumber = (field: unknown) => field === null || finiteNumber(field);
  const containsChartValues = (field: unknown): boolean => {
    if (Array.isArray(field)) return field.some(containsChartValues);
    if (!field || typeof field !== "object") return false;
    return Object.entries(field as Record<string, unknown>).some(
      ([key, nested]) => key === "data" || key === "values" || containsChartValues(nested),
    );
  };
  const validVisualization = (field: unknown) => {
    if (field === undefined || field === null) return true;
    if (!field || typeof field !== "object" || Array.isArray(field)) return false;
    const item = field as Record<string, unknown>;
    if (item.kind === "kpi") {
      if (
        item.value_field !== "value_minor" ||
        (item.unit !== "money" && item.unit !== "count") ||
        !Array.isArray(item.data) || item.data.length !== 1
      ) return false;
      const row = item.data[0];
      return !!row && typeof row === "object" && !Array.isArray(row) &&
        finiteNumber((row as Record<string, unknown>).value_minor);
    }
    const spec = item.spec as Record<string, unknown> | undefined;
    if (
      item.kind !== "chart" || !spec || typeof spec !== "object" ||
      (item.unit !== "money" && item.unit !== "count" && item.unit !== "ratio") ||
      typeof item.chart_id !== "string" || !/^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/.test(item.chart_id) ||
      typeof spec.mark !== "object" || !spec.encoding || typeof spec.encoding !== "object" ||
      !Array.isArray(item.data) ||
      !item.data.every((row) => !!row && typeof row === "object" && !Array.isArray(row)) ||
      // How many rows the plot draws. It decides whether the chart admits to showing a sample, so
      // a nonsensical one is refused rather than shrugged at: a zero would draw nothing under a
      // caveat, and a fraction would be a count that is not one.
      (item.limit !== undefined && item.limit !== null &&
        (!Number.isSafeInteger(item.limit) || (item.limit as number) < 1))
    ) return false;
    const channels = Object.values(spec.encoding as Record<string, unknown>);
    const encodedFields = channels.flatMap((channel) =>
      (Array.isArray(channel) ? channel : [channel]).flatMap((encoding) => {
        if (!encoding || typeof encoding !== "object" || Array.isArray(encoding)) return [];
        const encoded = encoding as Record<string, unknown>;
        return typeof encoded.field === "string" ? [encoded.field] : [];
      })
    );
    return item.data.every((row) =>
      encodedFields.every((encodedField) => encodedField in (row as Record<string, unknown>))
    );
  };
  const validDerivation = (field: unknown) => {
    if (field === undefined || field === null) return true;
    if (!field || typeof field !== "object" || Array.isArray(field)) return false;
    const item = field as Record<string, unknown>;
    const spec = item.spec as Record<string, unknown> | undefined;
    const chartId = /^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$/;
    return typeof item.source_chart_id === "string" && chartId.test(item.source_chart_id) &&
      typeof item.chart_id === "string" && chartId.test(item.chart_id) &&
      item.source_chart_id !== item.chart_id && !!spec && typeof spec === "object" &&
      !!spec.mark && typeof spec.mark === "object" && !Array.isArray(spec.mark) &&
      !!spec.encoding && typeof spec.encoding === "object" && !Array.isArray(spec.encoding) &&
      !containsChartValues(spec);
  };
  if (
    typeof body.outcome !== "string" || typeof body.text !== "string" ||
    !nullableString(body.question) || !nullableString(body.intent_id) ||
    !nullableString(body.plan) ||
    (body.conversation !== null &&
      (typeof body.conversation !== "object" || Array.isArray(body.conversation))) ||
    !nullableString(body.currency) || !nullableNumber(body.total_minor) ||
    !nullableNumber(body.movement_count) || !Array.isArray(body.groups) ||
    !Array.isArray(body.movements) || !Array.isArray(body.suggestions) ||
    !validVisualization(body.visualization) ||
    !validDerivation(body.visualization_derivation) ||
    body.groups.some((group) => {
      if (!group || typeof group !== "object") return true;
      const item = group as Record<string, unknown>;
      return typeof item.key !== "string" || !finiteNumber(item.total_minor) ||
        !finiteNumber(item.movement_count) ||
        (item.value_minor !== undefined && !nullableNumber(item.value_minor));
    }) || body.suggestions.some((item) => typeof item !== "string") ||
    body.movements.some((movement) => {
      if (!movement || typeof movement !== "object") return true;
      const item = movement as Record<string, unknown>;
      return typeof item.occurred_at !== "string" || typeof item.description !== "string" ||
        !finiteNumber(item.amount_minor) || !nullableString(item.merchant_name) ||
        !nullableString(item.category);
    }) ||
    (body.parts !== undefined && (!Array.isArray(body.parts) || body.parts.some((part) => {
      try { validateVoiceAnswerAt(part, depth + 1); return false; } catch { return true; }
    })))
  ) throw new Error("Respuesta de voz inválida.");
  return body as unknown as Answer;
}

export type Stage = { name: string };
export type MetricSource = { chart_id: string; definition_token: string };

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function readError(response: Response): Promise<never> {
  let detail = `HTTP ${response.status}`;
  try {
    const body = await response.json();
    if (body?.code) detail = String(body.code);
  } catch {
    // A body that is not JSON tells us nothing more than the status did.
  }
  throw new ApiError(detail, response.status);
}

export async function summary(): Promise<Summary> {
  const response = await fetch(`${BASE}/v1/account/summary`, { headers: headers() });
  if (!response.ok) await readError(response);
  return (await response.json()) as Summary;
}

/**
 * Sends a sentence and yields the stages as they arrive, then the answer.
 *
 * Server-sent events over `fetch` rather than `EventSource`, because EventSource cannot send an
 * Authorization header — and the account comes from the credential, so there is no unauthenticated
 * version of this call.
 *
 * The stages matter as much as the answer: what takes the time is the model deciding what to ask,
 * and `planned` arrives before any figure so a plan that misread the question can be seen while
 * that still helps.
 */
/** Which build is answering. Unauthenticated, because it is a fact about the deployment. */
export async function version(): Promise<{ sha: string; ref: string }> {
  const response = await fetch(`${BASE}/version`);
  if (!response.ok) await readError(response);
  return response.json();
}

export async function* ask(
  sentence: string,
  conversation: ConversationState | null = null,
  attempt?: string,
  channel: "TEXT" | "VOICE" = "TEXT",
  metricSources: MetricSource[] = [],
): AsyncGenerator<Stage | Answer> {
  const response = await fetch(`${BASE}/v1/questions/stream`, {
    method: "POST",
    // A question can be a payment — "hazle un bizum de 20 a Marta" prepares one — so this endpoint
    // takes the key too, and a resent question leaves one intent behind instead of two.
    headers: headers(attempt),
    // The plan behind the last answer travels with the question, and so does the half-built one
    // behind the last question back. The service remembers neither: the conversation is held by
    // whoever is having it, which is why both parameters exist at all.
    // One object, stored and returned. The service decides what the next state is; this only
    // carries it there and back.
    body: JSON.stringify({
      question: sentence,
      conversation,
      channel,
      metric_sources: channel === "TEXT" ? metricSources : [],
    }),
  });
  if (!response.ok || !response.body) await readError(response);

  const reader = response.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    // One event per blank line. A chunk can split an event in half, so whatever is left after the
    // last blank line stays in the buffer.
    const events = buffer.split("\n\n");
    buffer = events.pop() ?? "";
    for (const event of events) {
      const data = event
        .split("\n")
        .filter((line) => line.startsWith("data:"))
        .map((line) => line.slice(5).trim())
        .join("");
      if (!data) continue;
      const payload = JSON.parse(data);
      if (payload.type === "stage") yield { name: payload.name } as Stage;
      else yield validateVoiceAnswer(payload);
    }
  }
}

/**
 * Answers a payment preview with the user's own words.
 *
 * `reply` is text and deliberately not a boolean: sending `true` would be this app deciding that
 * somebody agreed, and that decision belongs to the service, which reads the words against an
 * allow-list. "Sí pero mándale 30" is not a yes, and it has to reach the place that knows that.
 */
export async function confirm(intentId: string, reply: string): Promise<Answer> {
  const response = await fetch(`${BASE}/v1/payments/${intentId}/confirm`, {
    method: "POST",
    headers: headers(),
    body: JSON.stringify({ reply }),
  });
  if (!response.ok) await readError(response);
  const body = await response.json();
  // The payment endpoints answer with their own shape; the chat only needs these fields.
  return {
    outcome: body.outcome,
    text: body.text,
    question: null,
    intent_id: body.intent_id ?? null,
    plan: null,
    // The confirmation endpoints answer about a payment, not about a conversation: by this point
    // it is complete and either done or refused, so nothing is left open. The chat's own state is
    // whatever it was, which the caller still holds.
    conversation: null,
    currency: body.currency ?? null,
    total_minor: null,
    movement_count: null,
    groups: [],
    movements: [],
    suggestions: [],
  };
}


export type Contact = { contact_id: string; display_name: string; phone: string };

export type Group = { key: string; total_minor: number; movement_count: number };

export type Movements = {
  currency: string;
  total_minor: number;
  movement_count: number;
  movements: Movement[];
};

export type Breakdown = {
  currency: string;
  total_minor: number;
  movement_count: number;
  groups: Group[];
};

export type SavedMetric = {
  id: string;
  title: string;
  position: number;
  version: number;
  created_at?: string;
  updated_at?: string;
};

export type MetricAnswer = SavedMetric & { answer: Answer };

export async function metrics(): Promise<SavedMetric[]> {
  return (await read<{ metrics: SavedMetric[] }>("/v1/metrics")).metrics;
}

export async function openMetric(id: string): Promise<MetricAnswer> {
  return read<MetricAnswer>(`/v1/metrics/${id}`);
}

export async function deleteMetric(id: string): Promise<void> {
  const response = await fetch(`${BASE}/v1/metrics/${id}`, {
    method: "DELETE",
    headers: headers(),
  });
  if (!response.ok) await readError(response);
}

export async function renameMetric(id: string, title: string, version: number): Promise<SavedMetric> {
  const response = await fetch(`${BASE}/v1/metrics/${id}`, {
    method: "PATCH", headers: { ...headers(), "If-Match": String(version) }, body: JSON.stringify({ title }),
  });
  if (!response.ok) await readError(response);
  return (await response.json()) as SavedMetric;
}

export async function reorderMetrics(ids: string[]): Promise<void> {
  const response = await fetch(`${BASE}/v1/metrics/order`, { method: "PUT", headers: headers(), body: JSON.stringify(ids) });
  if (!response.ok) await readError(response);
}

export async function saveMetric(title: string, definitionToken: string): Promise<SavedMetric> {
  const response = await fetch(`${BASE}/v1/metrics`, {
    method: "POST",
    headers: headers(newAttempt()),
    body: JSON.stringify({ title, definition_token: definitionToken }),
  });
  if (!response.ok) await readError(response);
  return (await response.json()) as SavedMetric;
}

async function read<T>(path: string): Promise<T> {
  const response = await fetch(`${BASE}${path}`, { headers: headers() });
  if (!response.ok) await readError(response);
  return (await response.json()) as T;
}

export async function contacts(): Promise<Contact[]> {
  return (await read<{ contacts: Contact[] }>("/v1/contacts")).contacts;
}

/**
 * The movement list, optionally narrowed.
 *
 * `category` is an identifier the ledger knows and `merchant` is free text, which is the difference
 * the search field lives on: a shop name is data, a category is vocabulary.
 */
export async function movements(options: {
  days?: number;
  limit?: number;
  category?: string;
  merchant?: string;
  /** Money moved between the holder's own accounts. Off by default: the tally is a net, and a
      transfer nets to nothing across the household. */
  includeInternal?: boolean;
  /** One of the holder's accounts. Absent is the one the credential names. */
  account?: string;
  /** Every account the holder has. It cannot reach past them — the scope is the server's. */
  acrossAccounts?: boolean;
}): Promise<Movements> {
  const query = new URLSearchParams();
  query.set("days", String(options.days ?? 90));
  query.set("limit", String(options.limit ?? 50));
  if (options.category) query.set("category", options.category);
  if (options.merchant) query.set("merchant", options.merchant);
  if (options.includeInternal) query.set("include_internal", "true");
  if (options.account) query.set("account", options.account);
  if (options.acrossAccounts) query.set("across_accounts", "true");
  return read<Movements>(`/v1/account/movements?${query}`);
}

export type GroupBy = "CATEGORY" | "MONTH" | "WEEK" | "MERCHANT" | "ACCOUNT";

export async function breakdown(by: GroupBy, days: number): Promise<Breakdown> {
  return read<Breakdown>(`/v1/account/breakdown?by=${by}&days=${days}`);
}

/**
 * Prepares a payment built in a form.
 *
 * The recipient travels as the name that was *chosen from the list*, not as an identifier: banking
 * resolves it against this account's own contacts, so two Martas still produce a question instead of
 * a guess, and a forged id is useless because there is no id to forge.
 */
/**
 * Money into another of the holder's own accounts.
 *
 * No recipient, because there is nobody to name. The account is an identifier the service listed,
 * so nothing here turns a word into a destination.
 */
export async function transferBetweenAccounts(
  toAccountId: string,
  amountMinor: number,
  currency = "EUR",
  attempt?: string,
): Promise<Answer> {
  const response = await fetch(`${BASE}/v1/payments/between-accounts`, {
    method: "POST",
    headers: headers(attempt),
    body: JSON.stringify({
      to_account_id: toAccountId,
      amount_minor: amountMinor,
      currency,
    }),
  });
  if (!response.ok) await readError(response);
  const body = await response.json();
  return {
    outcome: body.outcome,
    text: body.text,
    question: null,
    intent_id: body.intent_id ?? null,
    plan: null,
    conversation: null,
    currency: body.currency ?? null,
    total_minor: null,
    movement_count: null,
    groups: [],
    movements: [],
    suggestions: [],
  };
}

export async function payChosen(
  recipientName: string,
  amountMinor: number,
  attempt?: string,
): Promise<Answer> {
  const response = await fetch(`${BASE}/v1/payments/chosen`, {
    method: "POST",
    headers: headers(attempt),
    body: JSON.stringify({
      recipient_name: recipientName,
      amount_minor: amountMinor,
      currency: "EUR",
    }),
  });
  if (!response.ok) await readError(response);
  const body = await response.json();
  return {
    outcome: body.outcome,
    text: body.text,
    question: null,
    intent_id: body.intent_id ?? null,
    plan: null,
    // The confirmation endpoints answer about a payment, not about a conversation: by this point
    // it is complete and either done or refused, so nothing is left open. The chat's own state is
    // whatever it was, which the caller still holds.
    conversation: null,
    currency: body.currency ?? null,
    total_minor: null,
    movement_count: null,
    groups: [],
    movements: [],
    suggestions: [],
  };
}


export type Card = {
  card_id: string;
  display_name: string;
  brand: string;
  kind: string;
  last_four: string;
  /** Already "09/28": how it is written on the card, assembled by the service exactly once. */
  expires: string;
  status: string;
  /** null means the balance is the limit, which is a debit card — not a limit of zero. */
  spending_limit_minor: number | null;
  spent_minor: number;
  charge_count: number;
};

export type Wallet = {
  /** The date the figures start from. It travels with them so no screen has to remember. */
  since: string;
  cards: Card[];
};

export async function cards(days: number): Promise<Wallet> {
  return read<Wallet>(`/v1/cards?days=${days}`);
}

export type Charge = {
  transaction_id: string;
  occurred_at: string;
  description: string;
  merchant_name: string | null;
  category: string | null;
  /** Unsigned: every charge is money out, and the screen writes the minus. */
  amount_minor: number;
};

export type Charges = { card_id: string; since: string; charges: Charge[] };

export async function chargesOn(cardId: string, days: number): Promise<Charges> {
  return read<Charges>(`/v1/cards/${cardId}/charges?days=${days}&limit=50`);
}

export async function categories(): Promise<string[]> {
  return (await read<{ categories: string[] }>("/v1/categories")).categories;
}

/**
 * Files a movement under a category, or hands it back to the bank with `null`.
 *
 * `null` is a choice, not an absence: the ledger keeps what it inferred, so there is always something
 * to fall back to. Which categories exist is banking's answer — this only sends one.
 */
export async function categorise(transactionId: string, category: string | null): Promise<void> {
  const response = await fetch(`${BASE}/v1/movements/${transactionId}/category`, {
    method: "PUT",
    headers: headers(),
    body: JSON.stringify({ category }),
  });
  if (!response.ok) await readError(response);
}
