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

export type Tab = (typeof TABS)[number][0] | "cuentas-buscar";

/** Which tab is highlighted for a destination. */
export function tabOf(where: Tab): string {
  return where === "cuentas-buscar" ? "cuentas" : where;
}
