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

import {
  breakdown as fetchBreakdown,
  deleteMetric,
  metrics,
  openMetric,
  renameMetric,
  reorderMetrics,
  type Breakdown,
  type MetricAnswer,
  type SavedMetric,
} from "./api";
import { categoryName, glyph, money, monthName } from "./format";
import { Chart } from "./Chart";

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

export function Finances() {
  const [days, setDays] = useState(30);
  const [categories, setCategories] = useState<Breakdown | null>(null);
  const [months, setMonths] = useState<Breakdown | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<SavedMetric[]>([]);
  const [metricsError, setMetricsError] = useState(false);
  const [opened, setOpened] = useState<MetricAnswer | null>(null);
  const [metricBusy, setMetricBusy] = useState<string | null>(null);
  const [metricActionError, setMetricActionError] = useState<string | null>(null);
  const [editing, setEditing] = useState<{ id: string; title: string } | null>(null);

  const loadMetrics = async () => {
    try {
      setSaved(await metrics());
      setMetricsError(false);
    } catch {
      setMetricsError(true);
    }
  };

  const open = async (id: string) => {
    setMetricBusy(id);
    setMetricActionError(null);
    try {
      setOpened(await openMetric(id));
    } catch {
      setMetricActionError("No he podido abrir la métrica.");
    } finally {
      setMetricBusy(null);
    }
  };

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
        <div className="card-heading"><h2>Mis métricas</h2><button className="chip" onClick={() => void loadMetrics()}>Actualizar</button></div>
        {metricsError && <p className="empty">No he podido cargar tus métricas. <button className="link" onClick={() => void loadMetrics()}>Reintentar</button></p>}
        {!metricsError && saved.length === 0 && <p className="empty">Aún no tienes métricas guardadas.</p>}
        <ul className="metric-list">
          {saved.map((metric, index) => (
            <li key={metric.id}>
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
              ) : <span>{metric.title}</span>}
              <span className="metric-actions">
                <button className="link" disabled={metricBusy === metric.id} onClick={() => void open(metric.id)}>Abrir</button>
                <button className="link" onClick={() => setEditing({ id: metric.id, title: metric.title })}>Editar</button>
                <button aria-label={`Subir ${metric.title}`} className="link" disabled={index === 0 || metricBusy !== null} onClick={() => void moveUp(metric, index)}>↑</button>
                <button aria-label={`Bajar ${metric.title}`} className="link" disabled={index === saved.length - 1 || metricBusy !== null} onClick={() => void moveDown(metric, index)}>↓</button>
                <button className="link" onClick={() => {
                  if (!window.confirm(`¿Eliminar «${metric.title}»?`)) return;
                  setMetricBusy(metric.id);
                  setMetricActionError(null);
                  void deleteMetric(metric.id)
                    .then(() => { if (opened?.id === metric.id) setOpened(null); return loadMetrics(); })
                    .catch(() => setMetricActionError("No he podido borrar la métrica."))
                    .finally(() => setMetricBusy(null));
                }}>Eliminar</button>
              </span>
            </li>
          ))}
        </ul>
        {metricActionError && <p className="empty" role="alert">{metricActionError}</p>}
        {opened && <div className="metric-open"><button className="link" onClick={() => void open(opened.id)}>Actualizar</button><p>{opened.answer.text}</p><Chart visualization={opened.answer.visualization ?? null} currency={opened.answer.currency} /></div>}
      </section>

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
