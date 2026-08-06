/**
 * What the accounts screen is *about*, which is a different question from what it can filter.
 *
 * Reported in the user's own words: "cuando las selecciono en inicio y me voy a cuentas me lleva a
 * una vista general con todas las cuentas y desglose de todo en vez de un desglose filtrado por la
 * cuenta seleccionada". They tapped one account and read a screen about three, with every figure on
 * it real — which is the failure mode this client is written against, because nothing looks wrong.
 *
 * These tests are DOM-less like the rest of the suite. A tap is still a tap: a component called
 * from inside a render returns its element tree with the handlers attached, so the button can be
 * found by the words on it and its `onClick` invoked. What is asserted afterwards is the markup the
 * next screen produces, because that is what the person actually reads.
 */

import { describe, expect, it } from "vitest";
import { createElement, isValidElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { Accounts } from "./Accounts";
import { Home } from "./Home";
import type { Summary } from "./api";
import { accountOf, tabOf, type Tab } from "./tabs";

const CURRENT = "08aba04f-2b30-4639-88a6-eac741153ce0";
const PIGGY = "94d556d5-163c-4d8e-ad91-1507d973a0fd";
const FUND = "53e65064-36d0-47cb-8108-0bbcacbc14a7";

/** Laura's three accounts, with the figures the demo actually holds. */
const SUMMARY: Summary = {
  currency: "EUR",
  booked_minor: 185_542,
  available_minor: 185_542,
  as_of: "2026-08-06T00:00:00+00:00",
  movements: [],
  holdings: [
    {
      account_id: CURRENT,
      kind: "CURRENT",
      display_name: "Cuenta corriente",
      currency: "EUR",
      booked_minor: 185_542,
    },
    {
      account_id: PIGGY,
      kind: "SAVINGS",
      display_name: "Hucha",
      currency: "EUR",
      booked_minor: 548_550,
    },
    {
      account_id: FUND,
      kind: "ASSET",
      display_name: "Fondo indexado",
      currency: "EUR",
      booked_minor: 270_000,
    },
  ],
};

/**
 * The element tree a screen returns, handlers and all.
 *
 * `renderToStaticMarkup` gives HTML back, and an `onClick` is not HTML. Calling the component from
 * inside a render is what makes its hooks legal, and the tree it returns still has the handler on
 * it — so a tap can be exercised in a project that deliberately keeps a DOM out of its tests.
 */
function treeOf<P>(component: (props: P) => ReactNode, props: P): ReactNode {
  let tree: ReactNode = null;
  renderToStaticMarkup(
    createElement(function Probe() {
      tree = component(props);
      return null;
    }),
  );
  return tree;
}

function* elementsIn(node: ReactNode): Generator<ReactElement> {
  if (Array.isArray(node)) {
    for (const child of node) yield* elementsIn(child as ReactNode);
    return;
  }
  if (!isValidElement(node)) return;
  yield node;
  yield* elementsIn((node.props as { children?: ReactNode }).children);
}

function words(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map((child) => words(child as ReactNode)).join("");
  if (isValidElement(node)) return words((node.props as { children?: ReactNode }).children);
  return "";
}

/** The button somebody would tap, found the way they find it: by what it says. */
function buttonSaying(tree: ReactNode, said: string): { onClick: () => void } {
  for (const element of elementsIn(tree)) {
    if (element.type === "button" && words(element).includes(said)) {
      return element.props as { onClick: () => void };
    }
  }
  throw new Error(`no button saying ${said}`);
}

function accountsScreen(where: Tab): string {
  return renderToStaticMarkup(
    createElement(Accounts, {
      summary: SUMMARY,
      searching: false,
      account: accountOf(where),
      onGo: () => {},
    }),
  );
}

describe("tapping an account on the home screen", () => {
  it("opens the accounts screen about that account and nothing else", () => {
    /**
     * The reported bug, end to end. Breaking this costs somebody the difference between reading
     * their piggy bank and reading their whole financial life: they land on 1.855,42 € of current
     * account beside 5.485,50 € of savings, with the movements of both underneath, having asked
     * for one of them.
     */
    const taps: Tab[] = [];
    const home = treeOf(Home, {
      summary: SUMMARY,
      error: null,
      holder: "Laura",
      onGo: (tab: Tab) => taps.push(tab),
      onAsk: () => {},
    });

    buttonSaying(home, "Hucha").onClick();

    expect(taps).toHaveLength(1);
    const where = taps[0]!;

    // It is the accounts screen, so the tab bar highlights Cuentas exactly as before...
    expect(tabOf(where)).toBe("cuentas");
    // ...and the account travelled with the tap.
    expect(accountOf(where)).toBe(PIGGY);

    const screen = accountsScreen(where);

    expect(screen).toContain('class="chip on">Hucha');
    expect(screen).toContain('class="chip">Todas');
    expect(screen).toContain("5.485,50");
    // The other accounts' balances are not on a screen about this one. They are one tap away.
    expect(screen).not.toContain("1.855,42");
    expect(screen).not.toContain("2.700,00");
  });

  it("still opens about every account when the tab bar is what asked", () => {
    // The tab bar names no account, and that is not the same as naming one. Every balance is there
    // and "Todas" is the selected chip.
    const screen = accountsScreen("cuentas");

    expect(screen).toContain('class="chip on">Todas');
    expect(screen).toContain("1.855,42");
    expect(screen).toContain("5.485,50");
    expect(screen).toContain("2.700,00");
  });
});
