/**
 * Where the money went: by category, and month by month.
 *
 * The bars are CSS, not Vega-Lite. Phase 4 is the visualisation phase and it brings a validated spec
 * from the service; installing a charting library now to draw six divs would be paying for that
 * decision ahead of making it. What is here reads the same breakdown endpoint that spec will.
 *
 * Every figure is a share of a total the ledger computed. This screen never adds anything up: a
 * percentage calculated here from rounded numbers is how a chart stops matching its own labels.
 */

import { useEffect, useState } from "react";

import { breakdown as fetchBreakdown, type Breakdown } from "./api";
import { categoryName, glyph, money, monthName } from "./format";

const WINDOWS = [
  [30, "Este mes"],
  [90, "3 meses"],
  [365, "1 año"],
] as const;

export function Finances() {
  const [days, setDays] = useState(30);
  const [categories, setCategories] = useState<Breakdown | null>(null);
  const [months, setMonths] = useState<Breakdown | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let current = true;
    (async () => {
      try {
        const [byCategory, byMonth] = await Promise.all([
          fetchBreakdown("CATEGORY", days),
          // Always a year for the trend: a month-by-month chart of one month is one bar.
          fetchBreakdown("MONTH", 365),
        ]);
        if (current) {
          setCategories(byCategory);
          setMonths(byMonth);
          setError(null);
        }
      } catch {
        if (current) setError("No he podido cargar tus finanzas.");
      }
    })();
    return () => {
      current = false;
    };
  }, [days]);

  const biggest = Math.max(1, ...(months?.groups.map((group) => group.total_minor) ?? [1]));

  return (
    <div className="screen">
      <h1 className="screen-title">Finanzas</h1>

      <div className="chips" role="group" aria-label="Periodo">
        {WINDOWS.map(([value, label]) => (
          <button
            key={value}
            className={days === value ? "chip on" : "chip"}
            onClick={() => setDays(value)}
          >
            {label}
          </button>
        ))}
      </div>

      {error && <p className="empty">{error}</p>}

      {categories && (
        <section className="spent">
          <p className="spent-label">Gastado</p>
          <p className="spent-total">{money(categories.total_minor, categories.currency)}</p>
          <p className="spent-count">en {categories.movement_count} movimientos</p>
        </section>
      )}

      {months && months.groups.length > 1 && (
        <section className="card" aria-label="Gasto por mes">
          <h2>Mes a mes</h2>
          <div className="bars">
            {months.groups.slice(-6).map((group) => (
              <div className="bar-slot" key={group.key}>
                <div
                  className="bar"
                  style={{ height: `${Math.round((group.total_minor / biggest) * 100)}%` }}
                  title={money(group.total_minor, months.currency)}
                />
                <small>{monthName(group.key)}</small>
              </div>
            ))}
          </div>
        </section>
      )}

      {categories && (
        <section className="card" aria-label="Gasto por categoría">
          <h2>Por categoría</h2>
          <ul className="shares">
            {categories.groups.map((group) => (
              <li key={group.key}>
                <span className="glyph" aria-hidden="true">
                  {glyph(group.key)}
                </span>
                <span className="share-what">
                  <strong>{categoryName(group.key)}</strong>
                  <span className="track">
                    <span
                      className="fill"
                      style={{
                        width: `${Math.round(
                          (group.total_minor / Math.max(1, categories.total_minor)) * 100,
                        )}%`,
                      }}
                    />
                  </span>
                </span>
                <span className="share-figure">
                  {money(group.total_minor, categories.currency)}
                  <small>{group.movement_count}</small>
                </span>
              </li>
            ))}
          </ul>
          {categories.groups.length === 0 && <p className="empty">Sin gastos en este periodo.</p>}
        </section>
      )}
    </div>
  );
}
