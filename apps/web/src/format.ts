/**
 * Money and dates as Spain writes them.
 *
 * The same shape the service uses in its own sentences — "10.370,42 €" — so a figure read out by the
 * assistant and the same figure on the screen cannot look like two different numbers.
 *
 * Integer arithmetic all the way: minor units are divided only inside the formatter, because a float
 * carried around is how a cent goes missing between the ledger and a phone.
 */

// `useGrouping: "always"` on purpose, against the locale's own rule. Spanish CLDR does not group a
// four-digit number, so es-ES renders 2442,87 — while the service's own sentences say "2.442,87 €",
// because its formatter groups from the first thousand. One of the two had to give, and it cannot be
// the service: those figures are what the assistant reads out and what is already measured. A
// balance printed two ways on one phone is the thing this whole screen exists to avoid.
const MONEY = new Intl.NumberFormat("es-ES", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
  // `true`, which the spec normalises to "always". The string form needs a newer TS Intl lib than
  // this target declares, and the two mean the same thing.
  useGrouping: true,
});

export function money(amountMinor: number, currency = "EUR"): string {
  const symbol = currency === "EUR" ? " €" : ` ${currency}`;
  return `${MONEY.format(amountMinor / 100)}${symbol}`;
}

/** Signed on purpose: a movement list exists to tell a charge from a payment in. */
/**
 * Money out, whichever way the service spelled it.
 *
 * Two shapes reach this. The movement list sends signed amounts, where the sign carries the
 * meaning; a chat answer sends unsigned amounts with a direction, because banking returns positives
 * for a query filtered by EXPENSE. Reading only the sign turned every expense in the chat into an
 * income, which is the one distinction a movement list exists to make.
 */
export function outgoing(movement: { amount_minor: number; direction?: string }): boolean {
  if (movement.direction) return movement.direction === "EXPENSE";
  return movement.amount_minor < 0;
}

export function signed(amountMinor: number, currency = "EUR"): string {
  const sign = amountMinor > 0 ? "+" : "";
  return `${sign}${money(amountMinor, currency)}`;
}

const DAY = new Intl.DateTimeFormat("es-ES", { day: "2-digit", month: "short" });

export function day(iso: string): string {
  return DAY.format(new Date(iso)).replace(".", "");
}

const TIME = new Intl.DateTimeFormat("es-ES", { hour: "2-digit", minute: "2-digit" });

export function time(iso: string): string {
  return TIME.format(new Date(iso));
}

/**
 * A category as a person says it, and an emoji for the row.
 *
 * The keys are the ledger's identifiers, which are English and are data. The words are Spanish
 * because they are read by the user — the one exception to English everywhere.
 */
const CATEGORIES: Record<string, [string, string]> = {
  GROCERIES: ["🛒", "Supermercado"],
  TRANSPORT_FUEL: ["⛽", "Gasolina"],
  RESTAURANTS: ["🍽️", "Restaurantes"],
  TRANSPORT_PUBLIC: ["🚇", "Transporte"],
  HEALTH: ["💊", "Salud"],
  LEISURE: ["🎬", "Ocio"],
  SUBSCRIPTIONS: ["🔁", "Suscripciones"],
  HOUSING_RENT: ["🏠", "Alquiler"],
  SALARY: ["💼", "Nómina"],
  TRANSFER_BIZUM: ["📲", "Bizum"],
  OPENING: ["🏦", "Apertura"],
};

export function glyph(category: string | null): string {
  return (category && CATEGORIES[category]?.[0]) || "💳";
}

export function categoryName(category: string | null): string {
  return (category && CATEGORIES[category]?.[1]) || "Otros";
}


/**
 * "12,50" or "12.50" or "12" → 1250. `null` when it is not an amount.
 *
 * A lone dot is read as the decimal separator, because a phone keypad gives whichever one the layout
 * has. A *thousands* separator is refused: "1.200" could be 1200 or 1,2 depending on who typed it,
 * and the safe answer for money is to ask rather than to pick. The field says so when it refuses.
 *
 * Integer arithmetic throughout — `parseFloat` and a multiplication by 100 is how 0,29 € becomes 28
 * cents.
 */
export function minorUnits(typed: string): number | null {
  const cleaned = typed.trim().replace(/\s/g, "").replace(".", ",");
  if (!/^\d{1,7}(,\d{0,2})?$/.test(cleaned)) return null;
  const [whole, cents = ""] = cleaned.split(",");
  const amount = Number(whole) * 100 + Number(cents.padEnd(2, "0"));
  return amount > 0 ? amount : null;
}

/** A month key ("2026-07") as somebody reads it. */
const MONTH = new Intl.DateTimeFormat("es-ES", { month: "short" });

export function monthName(key: string): string {
  const [year, month] = key.split("-");
  if (!year || !month) return key;
  return MONTH.format(new Date(Number(year), Number(month) - 1, 1)).replace(".", "");
}

/** Initials for an avatar. Two words at most: "Marta Solis Vega" → "MS". */
export function initials(name: string): string {
  return name
    .split(/\s+/)
    .slice(0, 2)
    .map((part) => part.slice(0, 1).toUpperCase())
    .join("");
}
