/**
 * The app's icons, drawn rather than typed.
 *
 * They were emoji: each platform paints those its own way (a cartoon piggy bank on one phone, a flat
 * one on another), at its own weight and colour, and a bank app made of them reads as a toy. These
 * are one family instead: a 24-unit grid, a 1.75 stroke, round joins, and `currentColor`, so an icon
 * takes the colour of whatever it sits in and never brings its own.
 *
 * Decorative everywhere they appear — the label beside them says what they mean — so they are
 * hidden from assistive technology and carry no title.
 */

import type { ReactElement } from "react";

const PATHS = {
  home: <path d="M4 11.5 12 5l8 6.5V19a1 1 0 0 1-1 1h-4.5v-5h-5v5H5a1 1 0 0 1-1-1z" />,
  accounts: (
    <>
      <rect x="3.5" y="6" width="17" height="12" rx="2" />
      <path d="M3.5 10h17M7 14.5h3" />
    </>
  ),
  card: (
    <>
      <rect x="3" y="5.5" width="18" height="13" rx="2" />
      <path d="M3 9.5h18M6.5 15h4" />
    </>
  ),
  chart: <path d="M4 20V10M10 20V5M16 20v-7M21 20H3" />,
  bizum: (
    <>
      <rect x="6.5" y="3" width="11" height="18" rx="2" />
      <path d="m10 10 2-2 2 2M12 8v6M10.5 17.5h3" />
    </>
  ),
  list: <path d="M8 7h12M8 12h12M8 17h12M4 7h.01M4 12h.01M4 17h.01" />,
  search: (
    <>
      <circle cx="11" cy="11" r="6" />
      <path d="m20 20-4.2-4.2" />
    </>
  ),
  spark: <path d="M12 3.5c.6 4.4 2.6 6.9 8.5 8.5-5.9 1.6-7.9 4.1-8.5 8.5-.6-4.4-2.6-6.9-8.5-8.5 5.9-1.6 7.9-4.1 8.5-8.5Z" />,
  send: <path d="M5 12h13M13 6l6 6-6 6" />,
  close: <path d="M6.5 6.5l11 11M17.5 6.5l-11 11" />,
  mic: (
    <>
      <rect x="9" y="3.5" width="6" height="11" rx="3" />
      <path d="M5.5 11.5a6.5 6.5 0 0 0 13 0M12 18v2.5" />
    </>
  ),
  stop: <rect x="7" y="7" width="10" height="10" rx="2" />,
  current: (
    <>
      <path d="M3.5 9 12 4.5 20.5 9" />
      <path d="M5.5 10v7M10 10v7M14 10v7M18.5 10v7M3.5 19.5h17" />
    </>
  ),
  savings: (
    <>
      <path d="M5 13c0-3.6 3.1-6 7-6s7 2.4 7 6c0 2-1 3.4-2.4 4.3V20h-2.4v-1.6a9 9 0 0 1-4.4 0V20H7.4v-2.7C6 16.4 5 15 5 13Z" />
      <path d="M10.5 9.5h3M16 12h.01" />
    </>
  ),
  invest: <path d="M4 17l5-5 4 3 7-8M15 7h5v5" />,
  groceries: (
    <>
      <path d="M3.5 5h2l2 10h10l2-7H7" />
      <circle cx="9" cy="19" r="1.25" />
      <circle cx="17" cy="19" r="1.25" />
    </>
  ),
  fuel: (
    <>
      <path d="M5 20V6a2 2 0 0 1 2-2h5a2 2 0 0 1 2 2v14M4 20h11M5 10h9" />
      <path d="M14 8.5h2a2 2 0 0 1 2 2V16a1.5 1.5 0 0 0 3 0V9l-2.5-2.5" />
    </>
  ),
  dining: <path d="M7 3.5V11a2 2 0 0 0 2 2v7.5M11 3.5V11a2 2 0 0 1-2 2M17 20.5V3.5c-1.7 0-3 2.2-3 5.5 0 2 1 3 3 3" />,
  transit: (
    <>
      <rect x="5" y="3.5" width="14" height="13" rx="3" />
      <path d="M5 11h14M8.5 20.5 10 16.5M15.5 20.5 14 16.5M8.5 14h.01M15.5 14h.01" />
    </>
  ),
  health: <path d="M12 20s-7.5-4.6-7.5-10A4.3 4.3 0 0 1 12 7.6 4.3 4.3 0 0 1 19.5 10c0 5.4-7.5 10-7.5 10Z" />,
  leisure: (
    <>
      <path d="M4 8.5a2 2 0 0 0 0 7v2.5a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-2.5a2 2 0 0 1 0-7V6a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1z" />
      <path d="M14 5v14" strokeDasharray="1.5 2" />
    </>
  ),
  repeat: <path d="M17 3.5 20 6.5l-3 3M20 6.5H8a4 4 0 0 0-4 4M7 20.5l-3-3 3-3M4 17.5h12a4 4 0 0 0 4-4" />,
  rent: (
    <>
      <path d="M4 11.5 12 5l8 6.5" />
      <path d="M6 10v10h12V10M10 20v-5h4v5" />
    </>
  ),
  salary: (
    <>
      <rect x="3.5" y="7.5" width="17" height="11.5" rx="2" />
      <path d="M9 7.5V6a1.5 1.5 0 0 1 1.5-1.5h3A1.5 1.5 0 0 1 15 6v1.5M3.5 12.5h17" />
    </>
  ),
  interest: <path d="M18 6 6 18M7.5 9a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3ZM16.5 18a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3Z" />,
  transfer: <path d="M5 8.5h13M14.5 5 18 8.5 14.5 12M19 15.5H6M9.5 12 6 15.5 9.5 19" />,
  other: <circle cx="12" cy="12" r="2" />,
} satisfies Record<string, ReactElement>;

export type IconName = keyof typeof PATHS;

export function Icon({ name, size = 20 }: { name: IconName; size?: number }) {
  return (
    <svg
      className="icon"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.75}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {PATHS[name]}
    </svg>
  );
}

const CATEGORY_ICONS: Record<string, IconName> = {
  GROCERIES: "groceries",
  TRANSPORT_FUEL: "fuel",
  RESTAURANTS: "dining",
  TRANSPORT_PUBLIC: "transit",
  HEALTH: "health",
  LEISURE: "leisure",
  SUBSCRIPTIONS: "repeat",
  HOUSING_RENT: "rent",
  SALARY: "salary",
  TRANSFER_BIZUM: "bizum",
  OPENING: "current",
  INTEREST: "interest",
  INTERNAL: "transfer",
};

/** The icon for a ledger category; a plain dot for one the app has no picture for. */
export function categoryIcon(category: string | null): IconName {
  return (category && CATEGORY_ICONS[category]) || "other";
}

const ACCOUNT_ICONS: Record<string, IconName> = {
  CURRENT: "current",
  SAVINGS: "savings",
  ASSET: "invest",
};

/** The icon for a kind of account. */
export function accountIcon(kind: string): IconName {
  return ACCOUNT_ICONS[kind] ?? "accounts";
}
