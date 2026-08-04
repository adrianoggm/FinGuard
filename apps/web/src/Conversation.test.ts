/**
 * What the client remembers between turns.
 *
 * This is the state machine the diagnosis pointed at: the service keeps no conversation, so
 * every question is as contextless as whatever the client sends with it. A bug here is not a
 * display bug — it deletes the conversation, and the model then answers a question nobody asked
 * because it was handed no history to answer the real one with.
 *
 * `settled` is the whole of it. Four rules, and each one exists because the opposite behaviour
 * was wrong in a way somebody noticed:
 *
 *   the plan is kept when an answer brings none    a refusal must not erase what came before it
 *   the plan is cleared by a payment               a bizum and a query are not one conversation
 *   the draft is replaced, never merged            it belongs to the question just asked
 *   the pending payment is exactly the awaited one
 */

import { describe, expect, it } from "vitest";

import { settled, type Thread } from "./Conversation";
import type { Answer, PaymentDraft, PlanContext } from "./api";

const LAST_MONTH: PlanContext = {
  direction: "EXPENSE",
  period: "LAST_MONTH",
  n: null,
  start: null,
  end: null,
  categories: ["GROCERIES"],
  merchants: [],
  group_by: "NONE",
  metric: "SUM",
};

const RENT_DRAFT: PlanContext = { ...LAST_MONTH, period: null, categories: ["HOUSING_RENT"] };

function answer(fields: Partial<Answer> = {}): Answer {
  return {
    outcome: "PLANNED",
    text: "",
    question: null,
    intent_id: null,
    plan: null,
    plan_context: null,
    pending_context: null,
    payment_draft: null,
    candidates: [],
    currency: null,
    total_minor: null,
    movement_count: null,
    groups: [],
    movements: [],
    ...fields,
  };
}

function thread(fields: Partial<Thread> = {}): Thread {
  return {
    turns: [],
    plan: null,
    pending: null,
    unfinished: null,
    payment: null,
    ...fields,
  };
}

const noop = () => {};

describe("the plan carried between turns", () => {
  it("keeps the previous plan when an answer brings none", () => {
    // A refusal or a question back must not erase the context of the question before it, or
    // "¿y este mes?" after one bad turn starts from nothing.
    const before = thread({ plan: LAST_MONTH });

    const after = settled(before, [], answer({ outcome: "UNSUPPORTED" }), noop);

    expect(after.plan).toBe(LAST_MONTH);
  });

  it("replaces it when an answer brings a new one", () => {
    const fuel: PlanContext = { ...LAST_MONTH, categories: ["TRANSPORT_FUEL"] };

    const after = settled(thread({ plan: LAST_MONTH }), [], answer({ plan_context: fuel }), noop);

    expect(after.plan).toBe(fuel);
  });

  it("clears it after a payment", () => {
    // A bizum and a query are not the same conversation, so "¿y la semana pasada?" after a
    // payment should not inherit the query from before it.
    const after = settled(
      thread({ plan: LAST_MONTH }),
      [],
      answer({ outcome: "SENT", intent_id: "abc" }),
      noop,
    );

    expect(after.plan).toBeNull();
  });
});

describe("the half-built plan behind a question back", () => {
  it("is kept when the assistant asks something", () => {
    const after = settled(
      thread(),
      [],
      answer({ outcome: "NEEDS_CLARIFICATION", pending_context: RENT_DRAFT }),
      noop,
    );

    expect(after.unfinished).toBe(RENT_DRAFT);
  });

  it("is dropped as soon as an answer arrives without one", () => {
    // Unlike the plan, which is kept. A draft belongs to the question that was just asked:
    // carrying a stale one forward would let a window mentioned three turns later complete a
    // plan the user has long since moved on from.
    const after = settled(thread({ unfinished: RENT_DRAFT }), [], answer(), noop);

    expect(after.unfinished).toBeNull();
  });

  it("is replaced when the assistant asks again", () => {
    const second: PlanContext = { ...RENT_DRAFT, categories: ["LEISURE"] };

    const after = settled(
      thread({ unfinished: RENT_DRAFT }),
      [],
      answer({ outcome: "NEEDS_CLARIFICATION", pending_context: second }),
      noop,
    );

    expect(after.unfinished).toBe(second);
  });
});

describe("the payment waiting for a yes", () => {
  it("is the intent while it is awaiting confirmation", () => {
    const after = settled(
      thread(),
      [],
      answer({ outcome: "AWAITING_CONFIRMATION", intent_id: "intent-1" }),
      noop,
    );

    expect(after.pending).toBe("intent-1");
  });

  it("is cleared once the payment resolves, and the screens are told", () => {
    // The balance and the movements have just changed, so whoever is showing them has to
    // reload. Without this the home screen kept a figure that was no longer true.
    let reloaded = 0;
    const after = settled(
      thread({ pending: "intent-1" }),
      [],
      answer({ outcome: "SENT", intent_id: "intent-1" }),
      () => (reloaded += 1),
    );

    expect(after.pending).toBeNull();
    expect(reloaded).toBe(1);
  });

  it("does not tell them anything when no payment was involved", () => {
    let reloaded = 0;

    settled(thread(), [], answer({ plan_context: LAST_MONTH }), () => (reloaded += 1));

    expect(reloaded).toBe(0);
  });
});


describe("the bizum being built", () => {
  const HALF_BUILT: PaymentDraft = {
    recipient_name: "Marta Solis Vega",
    amount_minor: null,
    currency: "EUR",
    concept: null,
  };

  it("is kept when the assistant asks for the missing piece", () => {
    const after = settled(
      thread(),
      [],
      answer({ outcome: "NEEDS_CLARIFICATION", payment_draft: HALF_BUILT }),
      noop,
    );

    expect(after.payment).toBe(HALF_BUILT);
  });

  it("survives a turn that answers something else entirely", () => {
    // The turn from the report: "no sé, ¿a quién tengo en contactos?" in the middle of a bizum.
    // The contact list is the answer, and abandoning the payment to give it would mean starting
    // the bizum over — which is the defect, one level up.
    const after = settled(
      thread({ payment: HALF_BUILT }),
      [],
      answer({ outcome: "CONTACTS", text: "Tienes estos contactos: …" }),
      noop,
    );

    expect(after.payment).toBe(HALF_BUILT);
  });

  it("is cleared once the payment is a preview waiting for a yes", () => {
    // From here the state is the intent, not the draft: there is nothing half-built left.
    const after = settled(
      thread({ payment: HALF_BUILT }),
      [],
      answer({ outcome: "AWAITING_CONFIRMATION", intent_id: "intent-1" }),
      noop,
    );

    expect(after.payment).toBeNull();
    expect(after.pending).toBe("intent-1");
  });

  it("is cleared once the payment resolves", () => {
    const after = settled(
      thread({ payment: HALF_BUILT, pending: "intent-1" }),
      [],
      answer({ outcome: "SENT", intent_id: "intent-1" }),
      noop,
    );

    expect(after.payment).toBeNull();
  });

  it("is replaced when a later turn fills a slot", () => {
    const withAmount: PaymentDraft = { ...HALF_BUILT, amount_minor: 1500 };

    const after = settled(
      thread({ payment: HALF_BUILT }),
      [],
      answer({ outcome: "NEEDS_CLARIFICATION", payment_draft: withAmount }),
      noop,
    );

    expect(after.payment).toBe(withAmount);
  });
});
