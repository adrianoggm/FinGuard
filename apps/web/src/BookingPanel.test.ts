/**
 * What a booking shows on the screen somebody taps «Sí, reservar» on.
 *
 * The sentence above it names the class; this is the class itself, field by field, so the yes is a
 * yes to something specific. Every value was verified by the service against the gym's own reply,
 * and every one is drawn as text: the activity and the gym are a third party's words, and a slot
 * reference may even look like an address without being one to follow.
 */

import { describe, expect, it } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { BookingPanel } from "./BookingPanel";
import { Conversation, awaitingConfirmation, type Thread } from "./Conversation";
import { validateVoiceAnswer, type ActivitySlot, type ConversationState } from "./api";

function slot(over: Partial<ActivitySlot> = {}): ActivitySlot {
  return {
    agent_id: "gym-norte",
    agent_name: "Gimnasio Norte",
    activity: "Spinning",
    start: "2026-10-01T19:00:00+02:00",
    end: "2026-10-01T19:45:00+02:00",
    free_places: 4,
    slot_ref: "N-1001-1900",
    ...over,
  };
}

function state(over: Partial<ConversationState>): ConversationState {
  return {
    workflow: null,
    awaiting: null,
    last_plan: null,
    draft: null,
    capability: null,
    payment: null,
    suspended: false,
    candidates: [],
    transfer_to: null,
    transfer_amount_minor: null,
    intent_id: null,
    recipient: null,
    amount_minor: null,
    currency: null,
    concept: null,
    rail: null,
    recipient_iban: null,
    ...over,
  };
}

function booking(over: Partial<ActivitySlot> = {}): Partial<ConversationState> {
  const booked = slot(over);
  return {
    workflow: "BOOKING_CONFIRMATION",
    awaiting: "CONFIRMATION",
    booking_id: "0e3c2f7a-9d61-4a8e-8f55-3b1d2c4e5f60",
    booking_slot: booked,
    booking_agent_name: booked.agent_name,
    booking_context_id: "ctx-norte",
  };
}

function html(over: Partial<ConversationState> | null): string {
  return renderToStaticMarkup(
    createElement(BookingPanel, { state: over === null ? null : state(over) }),
  );
}

describe("a booking waiting for a yes", () => {
  it("names the activity, the day and hours, the gym and the places", () => {
    const drawn = html(booking());

    expect(drawn).toContain('<dl class="preview-detail booking-detail">');
    expect(drawn).toContain("<dt>Actividad</dt><dd>Spinning</dd>");
    expect(drawn).toContain("<dt>Día y hora</dt><dd>jueves 1 de octubre, 19:00–19:45</dd>");
    expect(drawn).toContain("<dt>Gimnasio</dt><dd>Gimnasio Norte</dd>");
    expect(drawn).toContain("<dt>Plazas</dt><dd>4</dd>");
  });

  it("draws the reference as plain text, never as a link", () => {
    // A reference may hold `:` and `/`, so one can look like an address. It is still a name.
    const drawn = html(booking({ slot_ref: "http://evil.example/x" }));

    expect(drawn).toContain('<dd class="booking-reference">http://evil.example/x</dd>');
    expect(drawn).not.toContain("<a");
    expect(html(booking({ slot_ref: null }))).not.toContain("Referencia");
  });

  it("shows the class in its own offset, never the browser's", () => {
    const drawn = html(booking({ start: "2026-10-01T17:05:00+00:00", end: null }));

    expect(drawn).toContain("<dd>jueves 1 de octubre, 17:05</dd>");
  });

  it("says a full class, and a figure the gym did not give, plainly", () => {
    expect(html(booking({ free_places: 0 }))).toContain("<dt>Plazas</dt><dd>completa</dd>");
    expect(html(booking({ free_places: null }))).toContain("<dt>Plazas</dt><dd>—</dd>");
  });

  it("draws a gym's words as text, never as markup", () => {
    const drawn = html(booking({ activity: "<img src=x>", agent_name: "<b>Norte</b>" }));

    expect(drawn).toContain("&lt;img src=x&gt;");
    expect(drawn).not.toContain("<img");
    expect(drawn).not.toContain("<b>");
  });
});

describe("everything else", () => {
  it("draws nothing for a payment, an ordinary answer or no state", () => {
    expect(html({ workflow: "PAYMENT_CONFIRMATION", intent_id: "9", amount_minor: 1_500 })).toBe("");
    expect(html({})).toBe("");
    expect(html(null)).toBe("");
  });
});

describe("a booking in the conversation", () => {
  const preview = validateVoiceAnswer({
    outcome: "BOOKING_AWAITING_CONFIRMATION",
    text: "Vas a reservar Spinning, jueves 1 de octubre, 19:00–19:45, en Gimnasio Norte. ¿Confirmas?",
    conversation: state(booking()),
  });
  const thread: Thread = {
    turns: [{ who: "assistant", answer: preview }],
    conversation: state(booking()),
  };

  it("waits for a yes, with a button of its own", () => {
    const noop = () => {};
    const markup = renderToStaticMarkup(
      createElement(Conversation, { thread, onThread: noop, onClose: noop, onSettled: noop }),
    );

    expect(awaitingConfirmation(thread)).toBe(true);
    expect(markup).toContain("Sí, reservar");
    expect(markup).not.toContain("Sí, enviar");
    expect(markup).toContain('class="bubble theirs preview"');
    expect(markup).toContain('<dl class="preview-detail booking-detail">');
  });

  it("leaves a payment's yes as it was", () => {
    const paying: Thread = {
      turns: [],
      conversation: state({ workflow: "PAYMENT_CONFIRMATION", intent_id: "9", amount_minor: 1_500 }),
    };
    const noop = () => {};
    const markup = renderToStaticMarkup(
      createElement(Conversation, { thread: paying, onThread: noop, onClose: noop, onSettled: noop }),
    );

    expect(markup).toContain("Sí, enviar");
    expect(markup).not.toContain("Sí, reservar");
  });
});
