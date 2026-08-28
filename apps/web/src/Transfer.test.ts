/**
 * What the traspaso screen offers to confirm.
 *
 * Measured: with 1.855,42 € in the account, typing 96,15 € more than that produced a preview
 * reading «Te quedarían -7.776,08 €» over a live Confirmar. The backend does refuse it — banking
 * recomputes the balance at confirmation and raises `insufficient_funds` — so no money moves. The
 * screen still asked the holder to approve something it already knew would be refused, and showed
 * them a balance their account cannot hold.
 *
 * The rule is banking's own, applied where it can still be acted on: `booked < amount` is
 * insufficient, off the booked balance of the account the credential names, with no overdraft
 * allowance. This screen already displays that exact number under «Desde», so refusing against it
 * is refusing against what is on screen.
 */

import { describe, expect, it } from "vitest";

import { overdraft } from "./Transfer";
import type { Summary } from "./api";

const HELD = 185_542;

function summary(over: Partial<Summary> = {}): Summary {
  return {
    currency: "EUR",
    booked_minor: HELD,
    available_minor: HELD,
    as_of: "2026-08-28T00:00:00+00:00",
    movements: [],
    holdings: [],
    ...over,
  };
}

describe("an amount the account cannot cover", () => {
  it("is refused before a preview is ever asked for", () => {
    // 1.961,57 € against 1.855,42 €: the amount that produced «Te quedarían -7.776,08 €» in the
    // audit came from this shape, and no round trip is needed to know the answer.
    const said = overdraft(summary(), 196_157);

    expect(said).not.toBeNull();
    expect(said).toContain("1.855,42 €");
    expect(said).toContain("1.961,57 €");
  });

  it("says nothing about an amount the account covers", () => {
    expect(overdraft(summary(), 2_000)).toBeNull();
  });

  it("allows spending the balance down to exactly nothing", () => {
    // Banking refuses `booked < amount`, so the whole balance is payable and one cent more is not.
    expect(overdraft(summary(), HELD)).toBeNull();
    expect(overdraft(summary(), HELD + 1)).not.toBeNull();
  });

  it("stays quiet while there is no amount and no balance to compare", () => {
    expect(overdraft(summary(), null)).toBeNull();
    expect(overdraft(null, 196_157)).toBeNull();
  });

  it("does not refuse across a currency it cannot compare", () => {
    /**
     * The field is labelled € and the amount is parsed as euros. Against an account held in
     * anything else the comparison is meaningless, and a local refusal built on a meaningless
     * comparison would block a payment the bank would have taken. The bank decides that one.
     */
    expect(overdraft(summary({ currency: "USD" }), 196_157)).toBeNull();
  });
});
