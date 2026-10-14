/**
 * What a payment shows on the screen somebody taps "Sí" on.
 *
 * Two states were invisible, and both are in the conversation the service already sends back.
 *
 * **A transfer looked exactly like a Bizum.** The sentence names an amount and a person; the one
 * thing that distinguishes what is about to happen — twenty-four characters of account number —
 * was drawn nowhere. §4.1 asks for explicit confirmation of a *specific* operation, and confirming
 * something you cannot fully see is a formality.
 *
 * **A paused payment existed only in the backend.** It is said once, in a sentence that scrolls
 * away, and after that resuming is something the user has to know is possible.
 */

import { describe, expect, it } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { PaymentPanel } from "./Conversation";
import type { ConversationState, PaymentDraft } from "./api";

const IBAN = "ES9121000418450200051332";

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

function html(over: Partial<ConversationState> | null): string {
  return renderToStaticMarkup(
    createElement(PaymentPanel, { state: over === null ? null : state(over) }),
  );
}

function draft(over: Partial<PaymentDraft>): PaymentDraft {
  return {
    recipient_name: null,
    recipient_contact_id: null,
    amount_minor: null,
    currency: "EUR",
    concept: null,
    ...over,
  };
}

const CONFIRMING = {
  workflow: "PAYMENT_CONFIRMATION" as const,
  intent_id: "9",
  recipient: "Marta Solis Vega",
  amount_minor: 1_500,
  currency: "EUR",
};

describe("a confirmation", () => {
  it("names the amount and who it is going to", () => {
    const drawn = html({ ...CONFIRMING, concept: "tapas" });

    expect(drawn).toContain("Marta Solis Vega");
    expect(drawn).toContain("15,00 €");
    expect(drawn).toContain("tapas");
  });

  it("says Bizum when no account is named", () => {
    // Absent means BIZUM, which is what a client written before transfers round-trips.
    expect(html(CONFIRMING)).toContain("Bizum a");
  });

  it("shows the account number of a transfer, whole", () => {
    // The number is the thing being recognised. Grouped for reading, never truncated.
    const drawn = html({ ...CONFIRMING, rail: "TRANSFER", recipient_iban: IBAN });

    expect(drawn).toContain("Transferencia a");
    expect(drawn).toContain("ES91 2100 0418 4502 0005 1332");
  });

  it("never masks the account number", () => {
    /**
     * The one that matters. "····1332" cannot be checked against a note, so a masked destination
     * turns the confirmation into a formality — and a transfer to the wrong account is the most
     * expensive mistake this client can help somebody make.
     */
    const drawn = html({ ...CONFIRMING, rail: "TRANSFER", recipient_iban: IBAN });

    expect(drawn).not.toContain("·");
    expect(drawn).not.toContain("****");
    expect(drawn.replace(/\s/g, "")).toContain(IBAN);
  });

  it("draws nothing for a Bizum that somehow carries an account", () => {
    // A state this service never issues. Drawing the IBAN under "Bizum a" would describe an
    // operation that does not exist.
    expect(html(CONFIRMING)).not.toContain("A la cuenta");
  });
});

describe("a paused payment", () => {
  it("says it is paused and what is missing", () => {
    const drawn = html({
      workflow: "PAYMENT_IN_PROGRESS",
      suspended: true,
      payment: draft({ recipient_name: "Marta" }),
    });

    expect(drawn).toContain("pausado");
    expect(drawn).toContain("Marta");
    expect(drawn).toContain("falta el importe");
  });

  it("says the recipient is missing when that is what is missing", () => {
    const drawn = html({
      workflow: "PAYMENT_IN_PROGRESS",
      suspended: true,
      payment: draft({ amount_minor: 1_500 }),
    });

    expect(drawn).toContain("falta el destinatario");
  });

  it("stays quiet while the payment is still the subject", () => {
    // Not suspended: the conversation is about it, so a badge saying so is noise.
    const drawn = html({
      workflow: "PAYMENT_IN_PROGRESS",
      suspended: false,
      payment: draft({ recipient_name: "Marta" }),
    });

    expect(drawn).toBe("");
  });
});

describe("everything else", () => {
  it("draws nothing for an ordinary answer", () => {
    expect(html({})).toBe("");
    expect(html(null)).toBe("");
  });
});
