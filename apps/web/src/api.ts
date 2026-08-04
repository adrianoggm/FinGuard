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
  /** Present on the movement list, which is the only screen that can refile one. */
  transaction_id?: string | null;
  /** Whether the category is the holder's own rather than the bank's guess. */
  category_is_custom?: boolean;
};

export type Summary = {
  currency: string;
  booked_minor: number;
  available_minor: number;
  as_of: string;
  movements: Movement[];
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
  merchants: string[];
  group_by: string;
  metric: string;
};

/** A payment being built. Echoed back so the next reply finishes it instead of starting over. */
export type PaymentDraft = {
  recipient_name: string | null;
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

export type Answer = {
  outcome: string;
  text: string;
  question: string | null;
  intent_id: string | null;
  plan: string | null;
  plan_context: PlanContext | null;
  // Present with a question back: the reading that question interrupted. Echoed on the next turn
  // so the reply finishes the plan instead of starting a new one.
  pending_context: PlanContext | null;
  // Present while a bizum is still being built. Sending it back is what turns "15 euros" into a
  // payment instead of a question about movements, which is what it looks like on its own.
  payment_draft: PaymentDraft | null;
  // Present when the recipient names several contacts: the choice, with identifiers.
  candidates: Candidate[];
  // On a refusal: which capability is missing. Echoed back so "¿y el segundo?" gets the same
  // refusal rather than becoming a question about a period nothing can use.
  refused_capability: string | null;
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
/** Which build is answering. Unauthenticated, because it is a fact about the deployment. */
export async function version(): Promise<{ sha: string; ref: string }> {
  const response = await fetch(`${BASE}/version`);
  if (!response.ok) await readError(response);
  return response.json();
}

export async function* ask(
  sentence: string,
  previousPlan: PlanContext | null = null,
  pendingDraft: PlanContext | null = null,
  paymentDraft: PaymentDraft | null = null,
  previousRefusal: string | null = null,
): AsyncGenerator<Stage | Answer> {
  const response = await fetch(`${BASE}/v1/questions/stream`, {
    method: "POST",
    headers: headers(),
    // The plan behind the last answer travels with the question, and so does the half-built one
    // behind the last question back. The service remembers neither: the conversation is held by
    // whoever is having it, which is why both parameters exist at all.
    body: JSON.stringify({
      question: sentence,
      previous_plan: previousPlan,
      pending_draft: pendingDraft,
      payment_draft: paymentDraft,
      previous_refusal: previousRefusal,
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
    // A confirmation has no query behind it and nothing half-built: by this point the payment is
    // complete and waiting for a yes, which is its own piece of state.
    plan_context: null,
    pending_context: null,
    payment_draft: null,
    candidates: [],
    refused_capability: null,
    currency: body.currency ?? null,
    total_minor: null,
    movement_count: null,
    groups: [],
    movements: [],
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
}): Promise<Movements> {
  const query = new URLSearchParams();
  query.set("days", String(options.days ?? 90));
  query.set("limit", String(options.limit ?? 50));
  if (options.category) query.set("category", options.category);
  if (options.merchant) query.set("merchant", options.merchant);
  return read<Movements>(`/v1/account/movements?${query}`);
}

export type GroupBy = "CATEGORY" | "MONTH" | "WEEK" | "MERCHANT";

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
export async function payChosen(recipientName: string, amountMinor: number): Promise<Answer> {
  const response = await fetch(`${BASE}/v1/payments/chosen`, {
    method: "POST",
    headers: headers(),
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
    plan_context: null,
    pending_context: null,
    payment_draft: null,
    candidates: [],
    refused_capability: null,
    currency: body.currency ?? null,
    total_minor: null,
    movement_count: null,
    groups: [],
    movements: [],
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
