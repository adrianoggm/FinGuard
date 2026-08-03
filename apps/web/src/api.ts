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

const BASE = (import.meta.env.VITE_ORCHESTRATOR_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");
const TOKEN = import.meta.env.VITE_ORCHESTRATOR_TOKEN ?? "";

export const configured = TOKEN !== "";

function headers(): Record<string, string> {
  return { Authorization: `Bearer ${TOKEN}`, "Content-Type": "application/json" };
}

export type Movement = {
  occurred_at: string;
  description: string;
  /** Signed: negative is money out. The sign is the difference between a charge and a payment in. */
  amount_minor: number;
  merchant_name: string | null;
  category: string | null;
};

export type Summary = {
  currency: string;
  booked_minor: number;
  available_minor: number;
  as_of: string;
  movements: Movement[];
};

export type Answer = {
  outcome: string;
  text: string;
  question: string | null;
  intent_id: string | null;
  plan: string | null;
  currency: string | null;
  total_minor: number | null;
  movement_count: number | null;
  groups: { key: string; total_minor: number; movement_count: number }[];
  movements: Movement[];
};

export type Stage = { name: string };

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
export async function* ask(sentence: string): AsyncGenerator<Stage | Answer> {
  const response = await fetch(`${BASE}/v1/questions/stream`, {
    method: "POST",
    headers: headers(),
    body: JSON.stringify({ question: sentence }),
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
      else yield payload as Answer;
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
    currency: body.currency ?? null,
    total_minor: null,
    movement_count: null,
    groups: [],
    movements: [],
  };
}
