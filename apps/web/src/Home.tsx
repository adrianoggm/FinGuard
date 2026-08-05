/**
 * The bank app's home screen: two figures, the trend, four shortcuts, and the account.
 *
 * Laid out like the app a person already knows — a greeting, "tu dinero" beside what has gone out,
 * a bar per month, a row of round buttons, then the accounts. What is different is what the numbers
 * are: every one of them comes from the ledger through `/v1/account/summary` and
 * `/v1/account/breakdown`, so nothing here is a placeholder waiting to be replaced.
 *
 * **The four shortcuts all go somewhere.** The first version of this screen had four that did
 * nothing, which is worse than not drawing them: a button that does not respond teaches somebody
 * that the app is broken, and they are right.
 */

import { useEffect, useState } from "react";

import {
  breakdown as fetchBreakdown,
  version,
  type Breakdown,
  type Summary,
} from "./api";
import {
  accountGlyph,
  accountName,
  categoryName,
  day,
  glyph,
  money,
  monthName,
  signed,
  spendable,
} from "./format";
import type { Tab } from "./tabs";

type Props = {
  summary: Summary | null;
  error: string | null;
  holder: string;
  onGo: (tab: Tab) => void;
  onAsk: () => void;
};

export function Home({ summary, error, holder, onGo, onAsk }: Props) {
  const [months, setMonths] = useState<Breakdown | null>(null);
  const [thisMonth, setThisMonth] = useState<Breakdown | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const [byMonth, byCategory] = await Promise.all([
          fetchBreakdown("MONTH", 365),
          fetchBreakdown("CATEGORY", 30),
        ]);
        setMonths(byMonth);
        setThisMonth(byCategory);
      } catch {
        // The chart is the part of this screen that can be missing without lying. The balance cannot,
        // and it is handled where it is read.
        setMonths(null);
      }
    })();
  }, [summary]);

  // Across the accounts rather than the declared one. Null when their currencies differ, which is
  // when there is no total to show — see `spendable`.
  const holdings = summary?.holdings ?? [];
  const total = spendable(holdings);
  const investments = holdings.filter((holding) => holding.kind === "ASSET");
  const firstInvestment = investments[0];
  const invested =
    firstInvestment !== undefined &&
    new Set(investments.map((holding) => holding.currency)).size === 1
      ? {
          minor: investments.reduce((sum, holding) => sum + holding.booked_minor, 0),
          currency: firstInvestment.currency,
        }
      : null;

  const bars = months?.groups.slice(-6) ?? [];
  const biggest = Math.max(1, ...bars.map((group) => group.total_minor));
  const [build, setBuild] = useState<string | null>(null);

  useEffect(() => {
    if (!import.meta.env.DEV) return;
    // Failing quietly is right: not knowing the build is a worse home screen than one without a
    // label, and this is a development aid rather than part of the product.
    version()
      .then((it) => setBuild(`${it.ref}@${it.sha}`))
      .catch(() => setBuild(null));
  }, []);

  return (
    <div className="screen home">
      <header className="brand">
        <span className="mark" aria-hidden="true">
          ◆
        </span>
        <strong>FinGuard</strong>
        {/*
          Which build this is talking to, in development only. It exists because "I read HEAD and
          the app still does X" is unanswerable without it: the container may be older than the
          tree. Hidden in a production build, where a commit is nobody's business.
        */}
        {import.meta.env.DEV && build && <span className="build">{build}</span>}
        <span className="brand-right">
          <button onClick={() => onGo("cuentas-buscar")} aria-label="Buscar movimientos">
            🔍
          </button>
          <button onClick={onAsk} aria-label="Hablar con FinGuard">
            ✨
          </button>
        </span>
      </header>

      <p className="hello">Hola, {holder}</p>

      <section className="figures" aria-label="Resumen">
        <div>
          <p className="figure-label">Tu dinero</p>
          <p className="figure">
            {/*
              Across the holder's accounts, and deliberately without the investments: an ASSET
              balance is what was paid and not what it is worth. The assistant says exactly this in
              words — "es lo que invertiste y no lo que vale hoy" — and a screen that quietly added
              it in would contradict the sentence beside it.

              `total` is null when the accounts are in different currencies, because then there is
              no total. Falling back to the declared account's balance is right there: it is a real
              figure that is honestly labelled by the account list underneath.
            */}
            {summary ? (
              money(total?.minor ?? summary.booked_minor, total?.currency ?? summary.currency)
            ) : (
              // Never a zero while we are still asking: 0,00 € is a statement about somebody's money.
              <span className="figure-missing">{error ?? "···"}</span>
            )}
          </p>
          {invested && (
            <p className="figure-aside">+ {money(invested.minor, invested.currency)} invertidos</p>
          )}
        </div>
        <div>
          <p className="figure-label">Gastos del mes</p>
          <p className="figure out">
            {thisMonth ? (
              `-${money(thisMonth.total_minor, thisMonth.currency)}`
            ) : (
              <span className="figure-missing">···</span>
            )}
          </p>
        </div>
      </section>

      {bars.length > 1 && (
        <section className="card chart" aria-label="Gasto por mes">
          <div className="bars">
            {bars.map((group) => (
              <div className="bar-slot" key={group.key}>
                <div
                  className="bar"
                  style={{ height: `${Math.round((group.total_minor / biggest) * 100)}%` }}
                  title={money(group.total_minor, months?.currency ?? "EUR")}
                />
                <small>{monthName(group.key)}</small>
              </div>
            ))}
          </div>
          <button className="chart-more" onClick={() => onGo("finanzas")}>
            Ver mis finanzas →
          </button>
        </section>
      )}

      <nav className="shortcuts" aria-label="Accesos">
        <button className="shortcut" onClick={() => onGo("bizum")}>
          <span className="shortcut-icon" aria-hidden="true">
            📲
          </span>
          <span>Bizum</span>
        </button>
        <button className="shortcut" onClick={() => onGo("cuentas")}>
          <span className="shortcut-icon" aria-hidden="true">
            ≡
          </span>
          <span>Movimientos</span>
        </button>
        <button className="shortcut" onClick={() => onGo("tarjetas")}>
          <span className="shortcut-icon" aria-hidden="true">
            💳
          </span>
          <span>Tarjetas</span>
        </button>
        <button className="shortcut" onClick={onAsk}>
          <span className="shortcut-icon" aria-hidden="true">
            💬
          </span>
          <span>Preguntar</span>
        </button>
      </nav>

      <section aria-label="Cuentas">
        <h2 className="section-head">Cuentas</h2>
        {/*
          One row per account the holder has. This was a single hardcoded row reading "Cuenta
          corriente · ES-0001", which was accurate while there was one account and became a lie the
          moment there were three — and the kind of lie nobody notices, because the figure beside
          it was real.
        */}
        {holdings.length > 0
          ? holdings.map((holding) => (
              <button
                className="account-row"
                key={holding.account_id}
                onClick={() => onGo("cuentas")}
              >
                <span className="account-glyph" aria-hidden="true">
                  {accountGlyph(holding.kind)}
                </span>
                <span className="account-who">
                  <strong>{accountName(holding)}</strong>
                  {/* Only where it changes what the figure means. A caption under every row would
                      be decoration; under this one it is the difference between what somebody paid
                      and what they have. */}
                  {holding.kind === "ASSET" && <small>lo invertido, no lo que vale hoy</small>}
                </span>
                <span className="account-figure">
                  {money(holding.booked_minor, holding.currency)}
                </span>
              </button>
            ))
          : summary && (
              <button className="account-row" onClick={() => onGo("cuentas")}>
                <span className="account-who">
                  <strong>Cuenta corriente</strong>
                </span>
                <span className="account-figure">
                  {money(summary.booked_minor, summary.currency)}
                </span>
              </button>
            )}
      </section>

      <section className="movements" aria-label="Últimos movimientos">
        <h2 className="section-head">
          Últimos movimientos
          <button className="see-all" onClick={() => onGo("cuentas")}>
            Ver todos
          </button>
        </h2>
        {summary?.movements.length === 0 && <p className="empty">Nada en los últimos días.</p>}
        <ul>
          {summary?.movements.slice(0, 5).map((movement, index) => (
            <li key={`${movement.occurred_at}-${index}`}>
              <span className="glyph" aria-hidden="true">
                {glyph(movement.category)}
              </span>
              <span className="what">
                <strong>{movement.merchant_name ?? movement.description}</strong>
                <small>
                  {day(movement.occurred_at)} · {categoryName(movement.category)}
                </small>
              </span>
              <span className={movement.amount_minor < 0 ? "out" : "in"}>
                {signed(movement.amount_minor, summary.currency)}
              </span>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
