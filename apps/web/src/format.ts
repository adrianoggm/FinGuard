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

/** What to call an account the holder never named. Never the reference: nobody recognises "ES-0001-2". */
const KIND_NAMES: Record<string, string> = {
  CURRENT: "Cuenta corriente",
  SAVINGS: "Ahorro",
  ASSET: "Inversión",
};

export function accountName(holding: { kind: string; display_name: string | null }): string {
  return holding.display_name ?? KIND_NAMES[holding.kind] ?? "Cuenta";
}

/**
 * What the holder can actually spend, and what it deliberately leaves out.
 *
 * An `ASSET` balance is its cost basis — what was paid — because there is no price source. Adding
 * it into a figure labelled "tu dinero" reports a purchase price as money somebody has, which is
 * the same answer the assistant refuses to give in words. The two have to agree.
 *
 * `null` when the accounts are in different currencies, because then there is no total: adding
 * minor units across currencies gives a valid integer that means nothing. A caller renders the
 * per-account list instead.
 */
export function spendable(
  holdings: { kind: string; currency: string; booked_minor: number }[],
): { minor: number; currency: string } | null {
  const counted = holdings.filter((holding) => holding.kind !== "ASSET");
  const first = counted[0];
  if (first === undefined) return null;
  if (new Set(counted.map((holding) => holding.currency)).size > 1) return null;
  return {
    minor: counted.reduce((total, holding) => total + holding.booked_minor, 0),
    currency: first.currency,
  };
}

/**
 * An account number in groups of four, which is how it is printed and how it is read aloud.
 *
 * Whole and unmasked, deliberately. Approving a transfer means recognising twenty-four characters
 * as the ones you meant, and "····1332" is not something anybody can check against a note — a
 * masked destination turns the confirmation step into a formality, which is the one thing it must
 * not become.
 */
export function ibanGroups(iban: string): string {
  return iban.replace(/(.{4})/g, "$1 ").trim();
}

/**
 * Where a movement's category came from, in one short word, or null when saying nothing is right.
 *
 * Only the two layers a person can act on get a label. "USER" means they filed it themselves and
 * seeing that is what makes the override feel kept; "NONE" means nobody has, which is the only
 * state that is worth offering to correct. "BANK" and "SYSTEM" are the ordinary case, and a caption
 * under every row would be noise that teaches people to stop reading captions.
 */
export function categorySource(source: string | undefined): string | null {
  if (source === "USER") return "tuya";
  if (source === "NONE") return "sin clasificar";
  return null;
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
const CATEGORIES: Record<string, string> = {
  GROCERIES: "Supermercado",
  TRANSPORT_FUEL: "Gasolina",
  RESTAURANTS: "Restaurantes",
  TRANSPORT_PUBLIC: "Transporte",
  HEALTH: "Salud",
  LEISURE: "Ocio",
  SUBSCRIPTIONS: "Suscripciones",
  HOUSING_RENT: "Alquiler",
  SALARY: "Nómina",
  TRANSFER_BIZUM: "Bizum",
  OPENING: "Apertura",
  INTEREST: "Intereses",
  // Written by the dataset generator on its internal transfers. New ones carry no category at all,
  // because a category partitions spending and this is not spending — the word is here so the
  // rows that predate that decision do not render as "Otros".
  INTERNAL: "Traspaso",
};

export function categoryName(category: string | null): string {
  return (category && CATEGORIES[category]) || "Otros";
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

const COMPACT = new Intl.NumberFormat("es-ES", {
  notation: "compact",
  maximumFractionDigits: 1,
});

/** «2,1 mil»: a bar's label, where a full «2.099,80 €» does not fit and the axis says it is euros. */
export function compactMoney(amountMinor: number): string {
  return COMPACT.format(amountMinor / 100);
}

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
