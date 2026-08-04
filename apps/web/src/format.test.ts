/**
 * Which way the money went.
 *
 * From the reported conversation, every expense in the chat:
 *
 *     28 jul   Cafe Central   +22,11 €
 *
 * Banking returns positive amounts for a query filtered by EXPENSE and returns the direction
 * beside them; the orchestrator's DTO dropped the direction, and the client derived the sign from
 * the amount alone. So a coffee read as money coming in — the one distinction a movement list
 * exists to make.
 *
 * The fix keeps both shapes working, because both are real: the movement list sends signed amounts
 * (the sign carries the meaning) and a chat answer sends unsigned amounts with a direction.
 */

import { describe, expect, it } from "vitest";

import { outgoing, signed } from "./format";

describe("reading the direction of a movement", () => {
  it("believes the direction when the service sent one", () => {
    // The chat's shape: unsigned, with the direction beside it.
    expect(outgoing({ amount_minor: 2211, direction: "EXPENSE" })).toBe(true);
    expect(outgoing({ amount_minor: 244287, direction: "INCOME" })).toBe(false);
  });

  it("falls back to the sign when there is no direction", () => {
    // The movement list's shape, which has always been signed.
    expect(outgoing({ amount_minor: -2211 })).toBe(true);
    expect(outgoing({ amount_minor: 244287 })).toBe(false);
  });

  it("prefers the direction over the sign when they disagree", () => {
    // Which is the whole bug: +2211 with direction EXPENSE is money out.
    expect(outgoing({ amount_minor: 2211, direction: "EXPENSE" })).toBe(true);
  });
});

describe("what the user reads", () => {
  it("shows an expense with a minus whichever shape it arrived in", () => {
    const fromChat = { amount_minor: 2211, direction: "EXPENSE" };
    const amount = outgoing(fromChat)
      ? -Math.abs(fromChat.amount_minor)
      : Math.abs(fromChat.amount_minor);

    expect(signed(amount, "EUR")).toBe("-22,11 €");
  });

  it("shows an income with a plus", () => {
    expect(signed(244287, "EUR")).toBe("+2.442,87 €");
  });
});
