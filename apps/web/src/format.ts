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
