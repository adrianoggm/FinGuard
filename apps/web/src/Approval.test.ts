/**
 * What the screen somebody taps "Confirmar" on says when the confirmation does not complete.
 *
 * The defect, measured: with `POST /v1/payments/{id}/confirm` blocked, the Bizum screen produced
 * exactly one state over 2.1 s — the same sentence, the same two buttons, both re-enabled, no
 * error. The sentence was set; its only render site sat in the form branch, below the preview's
 * early return, on a screen that is not on screen while a preview is open. So the holder taps
 * Confirmar on a live payment, waits, gets both buttons back and is told nothing. The natural next
 * action is to tap Confirmar again.
 *
 * `UNIQUE (account_id, idempotency_key)` and a terminal-state trigger in banking mean that second
 * tap cannot pay twice. That is what stops this being a double-spend, and it is not what stops it
 * being a defect: the holder still cannot tell whether their money moved.
 *
 * DOM-less like the rest of the suite. `renderToStaticMarkup` runs no effects and re-renders
 * nothing, so the decision is a function and the card is a component that takes its answer — the
 * same shape `Accounts.load` uses for the same reason.
 */

import { describe, expect, it } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { Approval, forButtons, replied, type Held, type Noun, type Reply } from "./Approval";
import { ApiError, type Answer } from "./api";

const AWAITING = "AWAITING_CONFIRMATION";

/** The sentence the service actually writes for a preview, instruction and all. */
const PREVIEW_TEXT =
  "Vas a enviar 20,00 € a Marta Solis Vega (+34600111222). Te quedarían 1.855,42 €. " +
  "¿Confirmas? Responde sí o no.";

function answer(over: Partial<Answer>): Answer {
  return {
    outcome: AWAITING,
    text: PREVIEW_TEXT,
    question: null,
    intent_id: "f2f0a1cc-0e0e-4e3e-9a5a-2c9b1d7f0001",
    plan: null,
    conversation: null,
    currency: "EUR",
    total_minor: null,
    movement_count: null,
    groups: [],
    movements: [],
    suggestions: [],
    ...over,
  };
}

const STANDING = answer({});

/** One reply attempt that fails the way a blocked request fails in a browser. */
function blocked(reply: Reply = "sí", noun: Noun = "pago"): Promise<Held> {
  return replied(() => Promise.reject(new TypeError("Failed to fetch")), {
    reply,
    standing: STANDING,
    noun,
  });
}

function card(held: Held, busy = false): string {
  return renderToStaticMarkup(
    createElement(Approval, {
      title: "Bizum",
      held,
      busy,
      onReply: () => {},
      onRestart: () => {},
    }),
  );
}

describe("a confirmation that did not complete", () => {
  it("is said on the card the buttons are on", async () => {
    /**
     * The whole finding in one assertion. Before this, the card drew the preview sentence and
     * nothing else, and the only thing that changed on the screen was that the buttons stopped
     * being disabled.
     */
    const held = await blocked();

    expect(held.failure).not.toBeNull();
    expect(card(held)).toContain(held.failure);
  });

  it("keeps the preview it failed against, rather than clearing the screen", async () => {
    // The payment is still the subject. Dropping back to an empty form would lose the one thing
    // the holder needs to recognise what may or may not have happened.
    const held = await blocked();

    expect(held.answer).toBe(STANDING);
    expect(card(held)).toContain("Marta Solis Vega");
  });

  it("does not offer Confirmar and Cancelar again", async () => {
    /**
     * The second tap the audit predicted comes from `Confirmar` sitting there unchanged and
     * enabled. It is withdrawn, and so is `Cancelar`: after a confirmation whose outcome is
     * unknown, a button labelled Cancelar offers to stop a payment that may already be booked —
     * banking would refuse it as `intent_not_confirmable`, so the label and the effect disagree.
     */
    const drawn = card(await blocked());

    expect(drawn).not.toContain("Confirmar");
    expect(drawn).not.toContain("Cancelar");
    expect(drawn).toContain("Reintentar");
  });

  it("repeats the reply that failed, not a reply the holder never gave", async () => {
    // A failed cancellation must retry the cancellation. Retrying it as a confirmation would send
    // money on the strength of somebody having tapped "Cancelar".
    expect((await blocked("sí")).again).toBe("sí");
    expect((await blocked("no")).again).toBe("no");
  });

  it("announces itself, on a screen that had no live region at all", async () => {
    const drawn = card(await blocked());

    expect(drawn).toContain('role="alert"');
  });

  it("disables the retry while a retry is in flight", async () => {
    expect(card(await blocked(), true)).toContain("disabled");
  });
});

describe("what the holder is told about their money", () => {
  it("does not claim nothing moved when it cannot know", async () => {
    /**
     * A blocked or dropped request may have been executed. Saying "no se ha movido nada" here
     * would be a wrong answer about money in the direction that costs the most, so the card says
     * it cannot tell and says the retry is safe — which it is, because banking keys the
     * confirmation on the intent's status and refuses a second one.
     */
    const { failure } = await blocked("sí");

    expect(failure).not.toContain("no se ha movido nada");
    expect(failure).toContain("No sé si el banco");
    expect(failure).toContain("dos veces");
  });

  it("says plainly that nothing moved when the request never reached the bank", async () => {
    // 403 is the orchestrator's gate refusing the credential. The payment machinery was never
    // reached, so the honest sentence is the definite one.
    const held = await replied(() => Promise.reject(new ApiError("not_authorised", 403)), {
      reply: "sí",
      standing: STANDING,
      noun: "pago",
    });

    expect(held.failure).toContain("no se ha movido nada");
    expect(held.failure).toContain("Esta credencial no puede hacer pagos.");
  });

  it("treats a status it has no rule for as unknown, not as a refusal", async () => {
    // The conservative direction, and the same one `refusal_for` takes in the orchestrator.
    const held = await replied(() => Promise.reject(new ApiError("HTTP 502", 502)), {
      reply: "sí",
      standing: STANDING,
      noun: "pago",
    });

    expect(held.failure).toContain("No sé si el banco");
  });

  it("says the traspaso is still pending when a cancellation was refused at the gate", async () => {
    const held = await replied(() => Promise.reject(new ApiError("not_authorised", 403)), {
      reply: "no",
      standing: STANDING,
      noun: "traspaso",
    });

    expect(held.failure).toContain("traspaso");
    expect(held.failure).toContain("sigue pendiente");
  });
});

describe("a reply the bank answered without deciding", () => {
  /**
   * `BANKING_UNAVAILABLE` is a 200. The service reaches it when banking failed *after* the
   * confirmation started, so execution may already have happened, and it says exactly that:
   * «No he podido comprobar si el banco completó el pago… puedes reintentar esta confirmación sin
   * que se ejecute dos veces.» Drawn under the settled branch, that sentence sat over a button
   * labelled «Hacer otro», which abandons the intent it just told the holder to retry.
   */
  const unknown = answer({
    outcome: "BANKING_UNAVAILABLE",
    text:
      "No he podido comprobar si el banco completó el pago. No crees otro bizum: puedes " +
      "reintentar esta confirmación sin que se ejecute dos veces.",
  });

  function settled(reply: Reply = "sí"): Promise<Held> {
    return replied(() => Promise.resolve(unknown), { reply, standing: STANDING, noun: "pago" });
  }

  it("offers the retry its own sentence promises, not a fresh payment", async () => {
    const drawn = card(await settled());

    expect(drawn).toContain("Reintentar");
    expect(drawn).not.toContain("Hacer otro");
  });

  it("adds no sentence of its own, the service's being the better one", async () => {
    const held = await settled();

    expect(held.failure).toBeNull();
    expect(card(held)).toContain("No he podido comprobar si el banco completó el pago.");
  });

  it("leaves something to repeat, which is what keeps the key alive", async () => {
    /**
     * The screens renew the idempotency key on `again === null`. A renewed key after an outcome
     * this uncertain would let the next prepare mint a *second* intent for the same payment, where
     * the old key finds the one that may already have been consumed and is refused.
     */
    expect((await settled()).again).toBe("sí");
    expect((await settled("no")).again).toBe("no");
  });
});

describe("a reply that did complete", () => {
  it("shows what came back and offers to start again", () => {
    const settled = answer({ outcome: "PAID", text: "Hecho: 20,00 € enviados a Marta Solis Vega." });
    const drawn = card({ answer: settled, failure: null, again: null });

    expect(drawn).toContain("Hecho: 20,00 €");
    expect(drawn).toContain("Hacer otro");
    // The receipt is the one sentence worth announcing on this screen.
    expect(drawn).toContain('role="status"');
  });

  it("draws the confirmation pair while the preview is still live", () => {
    const drawn = card({ answer: STANDING, failure: null, again: null });

    expect(drawn).toContain("Confirmar");
    expect(drawn).toContain("Cancelar");
    expect(drawn).not.toContain("Reintentar");
  });
});

describe("the service's sentence on a screen with buttons", () => {
  it("does not tell the holder to type sí o no", () => {
    /**
     * The service writes one sentence for the chat, where "Responde sí o no" is exactly right, and
     * for these two screens, where there is nothing to respond *with*. The instruction is not a
     * fact about the payment — it is an instruction about a channel — and the channel is the
     * client's own business, so the client trims it.
     */
    expect(forButtons(PREVIEW_TEXT)).not.toContain("Responde sí o no");
    expect(forButtons(PREVIEW_TEXT).endsWith("¿Confirmas?")).toBe(true);
  });

  it("keeps every figure and every name the service wrote", () => {
    // The numbers come from the ledger. A client that rewords them is a client that can round one.
    const trimmed = forButtons(PREVIEW_TEXT);

    expect(trimmed).toContain("20,00 €");
    expect(trimmed).toContain("1.855,42 €");
    expect(trimmed).toContain("Marta Solis Vega (+34600111222)");
  });

  it("trims the same instruction after a reply it did not understand", () => {
    const unclear = `No estoy seguro de haberte entendido. ${PREVIEW_TEXT}`;

    expect(forButtons(unclear)).toContain("No estoy seguro");
    expect(forButtons(unclear)).not.toContain("Responde sí o no");
  });

  it("passes anything else through untouched", () => {
    // If the service's wording changes, the sentence is drawn whole rather than silently mangled.
    const receipt = "Hecho: 20,00 € enviados a Marta. Te quedan 1.835,42 €.";

    expect(forButtons(receipt)).toBe(receipt);
    expect(forButtons("Responde sí o no en el chat, no aquí.")).toBe(
      "Responde sí o no en el chat, no aquí.",
    );
  });
});
