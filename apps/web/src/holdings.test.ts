/**
 * What "tu dinero" means once there is more than one account.
 *
 * The screen and the assistant have to agree, and they are computed in two different languages by
 * two different codebases. The assistant says:
 *
 *     "Tienes 7.755,92 € entre tus cuentas … Aparte, tienes 2.700,00 € en Fondo indexado,
 *      que es lo que invertiste y no lo que vale hoy."
 *
 * so the number over "Tu dinero" has to be 7.755,92 and not 10.455,92. A screen that added the
 * fund in would contradict the sentence sitting next to it — two figures on one phone, which is
 * the failure this whole client was written against.
 */

import { describe, expect, it } from "vitest";

import { accountName, categorySource, money, spendable } from "./format";

const CURRENT = { kind: "CURRENT", currency: "EUR", booked_minor: 227_042 };
const PIGGY = { kind: "SAVINGS", currency: "EUR", booked_minor: 548_550 };
const FUND = { kind: "ASSET", currency: "EUR", booked_minor: 270_000 };

describe("spendable", () => {
  it("adds up the accounts money can be spent from", () => {
    expect(spendable([CURRENT, PIGGY])).toEqual({ minor: 775_592, currency: "EUR" });
  });

  it("leaves the investment out, and agrees with the sentence to the cent", () => {
    const total = spendable([CURRENT, PIGGY, FUND]);

    expect(total?.minor).toBe(775_592);
    expect(money(total?.minor ?? 0)).toBe("7.755,92 €");
  });

  it("has no total across two currencies", () => {
    // A valid integer that means nothing. The caller shows the per-account list instead.
    expect(spendable([CURRENT, { ...PIGGY, currency: "USD" }])).toBeNull();
  });

  it("has no total when everything is invested", () => {
    // Not zero. Zero is a statement about somebody's money and this holder has 2.700 € of it.
    expect(spendable([FUND])).toBeNull();
  });

  it("is unbothered by a holder with one account", () => {
    expect(spendable([CURRENT])).toEqual({ minor: 227_042, currency: "EUR" });
  });
});

describe("accountName", () => {
  it("uses the name the holder gave the account", () => {
    expect(accountName({ kind: "SAVINGS", display_name: "Hucha" })).toBe("Hucha");
  });

  it("falls back to the kind, never to a reference nobody recognises", () => {
    const unnamed = accountName({ kind: "SAVINGS", display_name: null });

    expect(unnamed).toBe("Ahorro");
    expect(unnamed).not.toContain("ES-");
  });
});

describe("categorySource", () => {
  it("names only the layers a person can act on", () => {
    // Their own label, so an override visibly stuck; and unfiled, which is the only state worth
    // offering to correct.
    expect(categorySource("USER")).toBe("tuya");
    expect(categorySource("NONE")).toBe("sin clasificar");
  });

  it("says nothing about the ordinary case", () => {
    // A caption under every row is a caption people stop reading, and then it is not there when it
    // matters. BANK and SYSTEM are what almost every movement is.
    expect(categorySource("BANK")).toBeNull();
    expect(categorySource("SYSTEM")).toBeNull();
    expect(categorySource(undefined)).toBeNull();
  });
});
