/**
 * Where the app can be.
 *
 * `cuentas-buscar` is the accounts screen with the search field focused, which is what the magnifying
 * glass in the header opens. It is a separate destination rather than a flag because that is what a
 * navigation is: the tab bar highlights the same tab either way, and the difference is only whether
 * the keyboard comes up.
 */

export const TABS = [
  ["inicio", "Inicio", "◧"],
  ["cuentas", "Cuentas", "≡"],
  ["tarjetas", "Tarjetas", "💳"],
  ["finanzas", "Finanzas", "📊"],
  ["bizum", "Bizum", "📲"],
] as const;

export type Tab = (typeof TABS)[number][0] | "cuentas-buscar" | "traspaso";

/** Which tab is highlighted for a destination. */
export function tabOf(where: Tab): string {
  // Both reached from the accounts screen and both highlight it. Destinations rather than tabs: a
  // sixth icon in a five-icon bar costs every other one its label, and neither of these is
  // somewhere people spend time.
  return where === "cuentas-buscar" || where === "traspaso" ? "cuentas" : where;
}
