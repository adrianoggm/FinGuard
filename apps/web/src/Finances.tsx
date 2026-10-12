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

import { useCallback, useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";

import {
  breakdown as fetchBreakdown,
  deleteMetric,
  metrics,
  openMetric,
  renameMetric,
  reorderMetrics,
  type Answer,
  type Breakdown,
  type SavedMetric,
} from "./api";
import { categoryName, compactMoney, money, monthName, time } from "./format";
import { categoryIcon, Icon } from "./Icon";
import { Chart, chartTitle } from "./Chart";
import { AnswerEvidence } from "./Conversation";

const WINDOWS = [
  [30, "Este mes"],
  [90, "3 meses"],
  [365, "1 año"],
] as const;

export function moveMetricUp(items: SavedMetric[], index: number): string[] {
  const ids = items.map((item) => item.id);
  if (index <= 0 || index >= ids.length) return ids;
  const [moving] = ids.splice(index, 1);
  if (moving) ids.splice(index - 1, 0, moving);
  return ids;
}

export function moveMetricDown(items: SavedMetric[], index: number): string[] {
  const ids = items.map((item) => item.id);
  if (index < 0 || index >= ids.length - 1) return ids;
  const [moving] = ids.splice(index, 1);
  if (moving) ids.splice(index + 1, 0, moving);
  return ids;
}

/**
 * One saved metric as it was last re-executed, and when.
 *
 * The moment is the clock time rather than "hace 5 minutos": a relative label is stamped once and
 * then rots between renders, and "hace 0 minutos" on a figure from an hour ago is exactly the kind
 * of quiet lie this screen exists not to tell.
 */
export type MetricView = { answer: Answer; at: string };

/**
 * What the cards show after one of them has been re-executed.
 *
 * `null` means the run failed, and then the previous answer is **dropped** rather than kept. The
 * card still has its title and its configuration -- that is what was saved -- but a figure from the
 * last successful run, sitting under a card that says it could not be updated, is a stale number
 * presented as current. Extracted from the handler so the invariant has somewhere to be tested.
 */
export function afterRefresh(
  views: Record<string, MetricView>,
  id: string,
  view: MetricView | null,
): Record<string, MetricView> {
  const next = { ...views };
  if (view) next[id] = view;
  else delete next[id];
  return next;
}

/**
 * Runs `onVisible` the first time this row reaches the viewport.
 *
 * `IntersectionObserver` is the platform's own answer to "is this on screen", so the agreed
 * behaviour — the visible cards recalculate on open, the rest as you scroll — is a property of the
 * browser rather than a guess about how tall a phone is. No library, and nothing to keep in sync
 * with the layout.
 */
function WhenVisible({ onVisible, children }: { onVisible: () => void; children: ReactNode }) {
  const host = useRef<HTMLLIElement>(null);
  // The latest callback in a ref, so a re-render does not tear down and rebuild the observer.
  const latest = useRef(onVisible);
  latest.current = onVisible;
  useEffect(() => {
    const element = host.current;
    if (!element) return;
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) latest.current();
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  return <li ref={host}>{children}</li>;
}

export function Finances() {
  const [days, setDays] = useState(30);
  const [categories, setCategories] = useState<Breakdown | null>(null);
  const [months, setMonths] = useState<Breakdown | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<SavedMetric[]>([]);
  const [metricsError, setMetricsError] = useState(false);
  const [views, setViews] = useState<Record<string, MetricView>>({});
  const [stale, setStale] = useState<Record<string, true>>({});
  const [expanded, setExpanded] = useState<string | null>(null);
  const [metricBusy, setMetricBusy] = useState<string | null>(null);
  const [metricActionError, setMetricActionError] = useState<string | null>(null);
  const [editing, setEditing] = useState<{ id: string; title: string } | null>(null);
  // One re-execution at a time, chained. Cards become visible in bursts when the section opens, and
  // the agreed reason for loading progressively was not to fire many queries at once -- each one is
  // a plan going through banking. A queue is that reason enforced rather than restated.
  const queue = useRef<Promise<unknown>>(Promise.resolve());
  const requested = useRef<Set<string>>(new Set());

  const loadMetrics = async () => {
    try {
      setSaved(await metrics());
      setMetricsError(false);
    } catch {
      setMetricsError(true);
    }
  };

  /** The section's own "Actualizar": the list, and every card that has already been drawn. */
  const reloadEverything = async () => {
    const drawn = Object.keys(views);
    requested.current = new Set();
    await loadMetrics();
    for (const id of drawn) request(id);
  };

  /**
   * Re-execute one saved metric and show what came back.
   *
   * A failure drops the previous answer rather than leaving it on screen. The card keeps its title
   * and its configuration -- that is what was saved -- but a figure from the last successful run,
   * sitting under a card that says it could not be updated, is a stale number presented as current.
   * That is the one thing a metric that "keeps answering itself" must never do.
   */
  const refresh = useCallback(async (id: string) => {
    try {
      const fresh = await openMetric(id);
      setViews((was) => afterRefresh(was, id, { answer: fresh.answer, at: new Date().toISOString() }));
      setStale((was) => {
        const next = { ...was };
        delete next[id];
        return next;
      });
    } catch {
      setViews((was) => afterRefresh(was, id, null));
      setStale((was) => ({ ...was, [id]: true }));
    }
  }, []);

  /** Ask for a metric once, behind the queue. Re-asking on every scroll would re-plan on every scroll. */
  const request = useCallback((id: string) => {
    if (requested.current.has(id)) return;
    requested.current.add(id);
    queue.current = queue.current.then(() => refresh(id), () => refresh(id));
  }, [refresh]);

  /** The explicit "Actualizar", which unlike the automatic pass always re-runs. */
  const again = useCallback((id: string) => {
    requested.current.add(id);
    setMetricBusy(id);
    queue.current = queue.current
      .then(() => refresh(id), () => refresh(id))
      .finally(() => setMetricBusy(null));
  }, [refresh]);

  const rename = async (metric: SavedMetric) => {
    const title = editing?.title.trim();
    if (!title || title === metric.title) {
      setEditing(null);
      return;
    }
    setMetricBusy(metric.id);
    setMetricActionError(null);
    try {
      await renameMetric(metric.id, title, metric.version);
      setEditing(null);
      await loadMetrics();
    } catch {
      setMetricActionError("La métrica ha cambiado; actualiza la lista e inténtalo de nuevo.");
    } finally {
      setMetricBusy(null);
    }
  };

  const moveUp = async (metric: SavedMetric, index: number) => {
    setMetricBusy(metric.id);
    setMetricActionError(null);
    try {
      await reorderMetrics(moveMetricUp(saved, index));
      await loadMetrics();
    } catch {
      setMetricActionError("No he podido ordenar las métricas.");
    } finally {
      setMetricBusy(null);
    }
  };

  const moveDown = async (metric: SavedMetric, index: number) => {
    setMetricBusy(metric.id);
    setMetricActionError(null);
    try {
      await reorderMetrics(moveMetricDown(saved, index));
      await loadMetrics();
    } catch {
      setMetricActionError("No he podido ordenar las métricas.");
    } finally {
      setMetricBusy(null);
    }
  };

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

  useEffect(() => { void loadMetrics(); }, []);

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

      <section className="card" aria-label="Mis métricas">
        <div className="card-heading"><h2>Mis métricas</h2><button className="chip" onClick={() => void reloadEverything()}>Actualizar</button></div>
        {metricsError && <p className="empty">No he podido cargar tus métricas. <button className="link" onClick={() => void reloadEverything()}>Reintentar</button></p>}
        {!metricsError && saved.length === 0 && <p className="empty">Aún no tienes métricas guardadas.</p>}
        <ul className="metric-list">
          {saved.map((metric, index) => {
            const view = views[metric.id];
            // Derived by the service from the plan it verified, so the subtitle can never name a
            // period the figures are not from. Absent for a metric saved as a figure rather than a
            // chart, and absent is right: an invented period would be worse than none.
            const period = chartTitle(view?.answer.visualization ?? null);
            return (
              <WhenVisible key={metric.id} onVisible={() => request(metric.id)}>
                <div className="metric-head">
                  {editing?.id === metric.id ? (
                    <span className="metric-edit">
                      <input
                        aria-label="Nuevo nombre de la métrica"
                        maxLength={80}
                        value={editing.title}
                        onChange={(event) => setEditing({ id: metric.id, title: event.target.value })}
                      />
                      <button className="link" onClick={() => void rename(metric)}>Guardar</button>
                      <button className="link" onClick={() => setEditing(null)}>Cancelar</button>
                    </span>
                  ) : (
                    <span className="metric-what">
                      <strong>{metric.title}</strong>
                      {period && <small className="metric-period">{period}</small>}
                    </span>
                  )}
                </div>
                {/*
                  The figure is the sentence the service wrote for it, not a number this screen
                  picks out of the payload. A count read as euros, or a ratio read as a total, is
                  the whole failure mode -- and the service already says what its own figure means.
                */}
                {view && <p className="metric-headline">{view.answer.text}</p>}
                {view && <Chart visualization={view.answer.visualization ?? null} currency={view.answer.currency} />}
                {stale[metric.id] && (
                  <p className="empty" role="status">
                    No he podido actualizarla.{" "}
                    <button className="link" onClick={() => again(metric.id)}>Reintentar</button>
                  </p>
                )}
                <span className="metric-actions">
                  <button
                    className="link"
                    aria-expanded={expanded === metric.id}
                    disabled={!view}
                    onClick={() => setExpanded(expanded === metric.id ? null : metric.id)}
                  >
                    {expanded === metric.id ? "Ocultar detalles" : "Ver detalles"}
                  </button>
                  <button className="link" disabled={metricBusy === metric.id} onClick={() => again(metric.id)}>Actualizar</button>
                  <button className="link" onClick={() => setEditing({ id: metric.id, title: metric.title })}>Editar</button>
                  <button aria-label={`Subir ${metric.title}`} className="link" disabled={index === 0 || metricBusy !== null} onClick={() => void moveUp(metric, index)}>↑</button>
                  <button aria-label={`Bajar ${metric.title}`} className="link" disabled={index === saved.length - 1 || metricBusy !== null} onClick={() => void moveDown(metric, index)}>↓</button>
                  <button className="link" onClick={() => {
                    if (!window.confirm(`¿Eliminar «${metric.title}»?`)) return;
                    setMetricBusy(metric.id);
                    setMetricActionError(null);
                    void deleteMetric(metric.id)
                      .then(() => {
                        if (expanded === metric.id) setExpanded(null);
                        requested.current.delete(metric.id);
                        setViews((was) => {
                          const next = { ...was };
                          delete next[metric.id];
                          return next;
                        });
                        return loadMetrics();
                      })
                      .catch(() => setMetricActionError("No he podido borrar la métrica."))
                      .finally(() => setMetricBusy(null));
                  }}>Eliminar</button>
                </span>
                {view && <p className="metric-when">Actualizada a las {time(view.at)}</p>}
                {expanded === metric.id && view && (
                  <div className="metric-detail"><AnswerEvidence answer={view.answer} /></div>
                )}
              </WhenVisible>
            );
          })}
        </ul>
        {metricActionError && <p className="empty" role="alert">{metricActionError}</p>}
      </section>

      {months && months.groups.length > 1 && (
        <section className="card" aria-label="Gasto por mes">
          <h2>Mes a mes</h2>
          <div className="bars">
            {months.groups.slice(-6).map((group) => {
              // The same bars as the home screen: the amount over each, the running month apart.
              const today = new Date();
              const running =
                group.key === `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}`;
              return (
                <div className={running ? "bar-slot running" : "bar-slot"} key={group.key}>
                  <span className="bar-amount">{compactMoney(group.total_minor)}</span>
                  <div
                    className="bar"
                    style={{ height: `${Math.round((group.total_minor / biggest) * 100)}%` }}
                    title={money(group.total_minor, months.currency)}
                  />
                  <small>{running ? `${monthName(group.key)} · en curso` : monthName(group.key)}</small>
                </div>
              );
            })}
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
                  <Icon name={categoryIcon(group.key)} />
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
