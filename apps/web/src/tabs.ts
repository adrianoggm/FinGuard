/**
 * Where the app can be.
 *
 * `cuentas-buscar` is the accounts screen with the search field focused, which is what the magnifying
 * glass in the header opens. It is a separate destination rather than a flag because that is what a
 * navigation is: the tab bar highlights the same tab either way, and the difference is only whether
 * the keyboard comes up.
 *
 * `cuentas:<account>` is the same screen about one account, and it exists for the same reason: the
 * home screen lists the holder's accounts and tapping one is a request to read *that* account.
 * Naming the account in the destination is what carries the tap across the navigation — without it
 * the tap arrives as "go to Cuentas" and the screen shows every account, which is what it did.
 */

export const TABS = [
  ["inicio", "Inicio", "◧"],
  ["cuentas", "Cuentas", "≡"],
  ["tarjetas", "Tarjetas", "💳"],
  ["finanzas", "Finanzas", "📊"],
  ["bizum", "Bizum", "📲"],
] as const;

export type Tab = (typeof TABS)[number][0] | "cuentas-buscar" | "traspaso" | `cuentas:${string}`;

/** Which tab is highlighted for a destination. */
export function tabOf(where: Tab): string {
  // All reached from the accounts screen and all highlight it. Destinations rather than tabs: a
  // sixth icon in a five-icon bar costs every other one its label, and none of these is
  // somewhere people spend time.
  if (where === "cuentas-buscar" || where === "traspaso" || accountOf(where) !== null) {
    return "cuentas";
  }
  return where;
}

/** The accounts screen, about one account: where tapping an account anywhere else goes. */
export function accountTab(accountId: string): Tab {
  return `cuentas:${accountId}`;
}

/**
 * Which account a destination is about, or `null` when it is about all of them.
 *
 * Read by whoever renders the screen rather than stored anywhere, because the destination *is* the
 * request: a second copy of "which account" in a state of its own is the copy that goes stale.
 */
export function accountOf(where: Tab): string | null {
  return where.startsWith("cuentas:") ? where.slice("cuentas:".length) : null;
}
