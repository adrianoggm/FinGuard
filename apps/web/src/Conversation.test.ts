/**
 * What the client remembers between turns — and, more to the point, what it no longer decides.
 *
 * This used to be five nullable fields the client combined by hand: the answered plan, the half-read
 * query, the half-built payment, the payment awaiting a yes, and the last refusal. `settled` worked
 * out what to keep from a pattern of nulls, which meant the frontend held the orchestrator's
 * semantics — and it got them wrong every time a new state appeared.
 *
 * Now the service says where the conversation is and this applies it. So these tests are about two
 * things and no others:
 *
 *   the state is stored, never recomputed    a field this end invents is a field that can disagree
 *   the screens are told when money moved    the one thing the service cannot do from its side
 *
 * A test asserting "the plan survives a refusal" would now be testing the orchestrator through the
 * client. That property still holds; it is checked where it is decided.
 */

import { describe, expect, it } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import {
  Conversation,
  awaitingConfirmation,
  pendingIntent,
  quickReplies,
  recipientChoices,
  settled,
  withChoice,
  type Thread,
} from "./Conversation";
import type { Answer, ConversationState, PlanContext } from "./api";

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

function state(fields: Partial<ConversationState> = {}): ConversationState {
  return {
    workflow: null,
    awaiting: null,
    last_asked: null,
    last_plan: null,
    retry_plan: null,
    draft: null,
    capability: null,
    payment: null,
    suspended: false,
    candidates: [],
    intent_id: null,
    recipient: null,
    amount_minor: null,
    currency: null,
    concept: null,
    rail: null,
    recipient_iban: null,
    ...fields,
  };
}

function answer(fields: Partial<Answer> = {}): Answer {
  return {
    outcome: "PLANNED",
    text: "",
    question: null,
    intent_id: null,
    plan: null,
    conversation: null,
    currency: null,
    total_minor: null,
    movement_count: null,
    groups: [],
    movements: [],
    suggestions: [],
    ...fields,
  };
}

function thread(fields: Partial<Thread> = {}): Thread {
  return { turns: [], conversation: null, ...fields };
}

const noop = () => {};

describe("the state the service sent", () => {
  it("is stored as it arrived", () => {
    // Verbatim, not merged: the service decided what this turn leaves open, and combining it with
    // what came before is exactly the inference this replaced.
    const sent = state({
      workflow: "FINANCIAL_CLARIFICATION",
      awaiting: "YEAR",
      draft: LAST_MONTH,
    });

    const after = settled(thread(), [], answer({ conversation: sent }), noop);

    expect(after.conversation).toBe(sent);
  });

  it("replaces whatever was there before", () => {
    const before = state({ workflow: "PAYMENT_IN_PROGRESS", awaiting: "AMOUNT" });
    const now = state({ last_plan: LAST_MONTH });

    const after = settled(
      thread({ conversation: before }),
      [],
      answer({ conversation: now }),
      noop,
    );

    expect(after.conversation).toBe(now);
  });

  it("closes a confirmed payment when the service returns an idle conversation", () => {
    const before = state({
      workflow: "PAYMENT_CONFIRMATION",
      awaiting: "CONFIRMATION",
      intent_id: "intent-1",
      recipient: "Marta Solis Vega",
      amount_minor: 1_500,
    });
    const idle = state({ last_plan: LAST_MONTH });

    const after = settled(
      thread({ conversation: before }),
      [],
      answer({ outcome: "PAID", intent_id: "intent-1", conversation: idle }),
      noop,
    );

    expect(awaitingConfirmation(after)).toBe(false);
    expect(after.conversation).toBe(idle);
  });

  it("keeps the last one when an answer carries none", () => {
    // The payment endpoints answer about a payment rather than about a conversation, so they send
    // no state. Dropping it there would end the conversation because a bizum was confirmed.
    const before = state({ last_plan: LAST_MONTH });

    const after = settled(thread({ conversation: before }), [], answer(), noop);

    expect(after.conversation).toBe(before);
  });
});

describe("what the client still works out for itself", () => {
  it("renders only the latest answer's structured recovery actions", () => {
    const retrying = thread({
      turns: [
        { who: "assistant", answer: answer({ suggestions: ["Reintentar", "Cambiar consulta"] }) },
      ],
    });

    expect(quickReplies(retrying)).toEqual(["Reintentar", "Cambiar consulta"]);

    const markup = renderToStaticMarkup(
      createElement(Conversation, {
        thread: retrying,
        onThread: noop,
        onClose: noop,
        onSettled: noop,
      }),
    );
    expect(markup).toContain('aria-label="Acciones sugeridas"');
    expect(markup).toContain("Reintentar");
  });

  it("does not keep an older action live after the user writes another turn", () => {
    const movedOn = thread({
      turns: [
        { who: "assistant", answer: answer({ suggestions: ["Reintentar"] }) },
        { who: "user", text: "otra cosa" },
      ],
    });

    expect(quickReplies(movedOn)).toEqual([]);
  });

  it("knows a payment is waiting for a yes", () => {
    const holding = thread({
      conversation: state({
        workflow: "PAYMENT_CONFIRMATION",
        awaiting: "CONFIRMATION",
        intent_id: "intent-1",
      }),
    });

    expect(awaitingConfirmation(holding)).toBe(true);
    expect(pendingIntent(holding)).toBe("intent-1");
  });

  it("knows a half-built payment is not one", () => {
    // The distinction the input box renders on: a payment being built takes a sentence, a payment
    // waiting for a yes takes a yes.
    const building = thread({
      conversation: state({ workflow: "PAYMENT_IN_PROGRESS", awaiting: "AMOUNT" }),
    });

    expect(awaitingConfirmation(building)).toBe(false);
    expect(pendingIntent(building)).toBeNull();
  });

  it("offers the structured contacts when the payment asks for a recipient choice", () => {
    const candidates = [
      { contact_id: "contact-1", display_name: "Marta Ruiz Gomez", phone: "+34600111222" },
      { contact_id: "contact-2", display_name: "Marta Solis Vega", phone: "+34600333444" },
    ];
    const choosing = thread({
      conversation: state({
        workflow: "PAYMENT_IN_PROGRESS",
        awaiting: "RECIPIENT_CHOICE",
        candidates,
      }),
    });

    expect(recipientChoices(choosing)).toBe(candidates);
    expect(recipientChoices(thread())).toEqual([]);
  });

  it("renders every recipient choice as a button with identifying details", () => {
    const choosing = thread({
      conversation: state({
        workflow: "PAYMENT_IN_PROGRESS",
        awaiting: "RECIPIENT_CHOICE",
        candidates: [
          { contact_id: "contact-1", display_name: "Marta Ruiz Gomez", phone: "+34600111222" },
          { contact_id: "contact-2", display_name: "Marta Solis Vega", phone: "+34600333444" },
        ],
      }),
    });

    const markup = renderToStaticMarkup(
      createElement(Conversation, {
        thread: choosing,
        onThread: noop,
        onClose: noop,
        onSettled: noop,
      }),
    );

    expect(markup).toContain("Marta Ruiz Gomez");
    expect(markup).toContain("+34600111222");
    expect(markup).toContain("Marta Solis Vega");
    expect(markup).toContain('aria-label="Elige el contacto"');
  });

  it("tells the screens to reload when a payment resolved", () => {
    // The one side effect the service cannot perform from its side: the balance and the movements
    // behind the sheet have changed, and whatever is showing them is stale.
    let reloaded = 0;

    settled(
      thread(),
      [],
      answer({ outcome: "SENT", intent_id: "intent-1", conversation: state() }),
      () => (reloaded += 1),
    );

    expect(reloaded).toBe(1);
  });

  it("does not tell them anything while the payment is still a question", () => {
    let reloaded = 0;

    settled(
      thread(),
      [],
      answer({
        outcome: "AWAITING_CONFIRMATION",
        intent_id: "intent-1",
        conversation: state({ workflow: "PAYMENT_CONFIRMATION", intent_id: "intent-1" }),
      }),
      () => (reloaded += 1),
    );

    expect(reloaded).toBe(0);
  });

  it("does not tell them anything when no payment was involved", () => {
    let reloaded = 0;

    settled(thread(), [], answer({ conversation: state({ last_plan: LAST_MONTH }) }), () => {
      reloaded += 1;
    });

    expect(reloaded).toBe(0);
  });
});

describe("the one field this client writes", () => {
  it("puts the tapped contact on the half-built payment", () => {
    const choosing = thread({
      conversation: state({
        workflow: "PAYMENT_IN_PROGRESS",
        awaiting: "RECIPIENT_CHOICE",
        payment: {
          recipient_name: null,
          recipient_contact_id: null,
          amount_minor: 1_500,
          currency: "EUR",
          concept: "comida",
        },
        candidates: [
          { contact_id: "contact-2", display_name: "Marta Solis Vega", phone: "+34600333444" },
        ],
      }),
    });

    const chosen = withChoice(choosing, "contact-2");

    expect(chosen.conversation?.payment?.recipient_contact_id).toBe("contact-2");
    // Everything else is the service's, and a client that rewrote any of it would be back to
    // deciding what the conversation means.
    expect(chosen.conversation?.payment?.amount_minor).toBe(1_500);
    expect(chosen.conversation?.payment?.concept).toBe("comida");
    expect(chosen.conversation?.awaiting).toBe("RECIPIENT_CHOICE");
  });

  it("changes nothing when there is no payment to choose for", () => {
    // A tap cannot arrive without candidates, and inventing a payment to hang an identifier on
    // would send the service a state it never issued.
    const idle = thread();

    expect(withChoice(idle, "contact-2")).toBe(idle);
  });
});
