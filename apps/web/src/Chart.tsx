import { useEffect, useRef, useState } from "react";
import type { TopLevelSpec } from "vega-lite";
import type { Visualization } from "./api";
import { money } from "./format";

type Props = {
  visualization: Visualization | null;
  currency: string | null;
};

type Encoding = Record<string, unknown>;

export function spanishNumberLocale(currency: string): Record<string, unknown> {
  return {
    decimal: ",",
    thousands: ".",
    grouping: [3],
    currency: ["", currency === "EUR" ? " €" : ` ${currency}`],
    minus: "−",
    nan: "—",
  };
}

export const spanishTimeLocale: Record<string, unknown> = {
  dateTime: "%A, %e de %B de %Y, %X",
  date: "%d/%m/%Y",
  time: "%H:%M:%S",
  periods: ["a. m.", "p. m."],
  days: ["domingo", "lunes", "martes", "miércoles", "jueves", "viernes", "sábado"],
  shortDays: ["dom", "lun", "mar", "mié", "jue", "vie", "sáb"],
  months: [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
  ],
  shortMonths: ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"],
};

export function visualPresentation(visualization: Extract<Visualization, { kind: "chart" }>, currency: string) {
  const moneyUnit = visualization.unit === "money" || visualization.unit === "ratio";
  const valueField = moneyUnit ? "__value_major" : "value_minor";
  const displayField = "__value_display";
  const hasMonthlyDimension = visualization.data.some(
    (row) => typeof row.dimension === "string" && /^\d{4}-(0[1-9]|1[0-2])$/.test(row.dimension),
  );
  const monthLabel = new Intl.DateTimeFormat("es-ES", { month: "short", year: "numeric" });
  const data = visualization.data.map<Record<string, unknown>>((row) => {
    const value = row.value_minor;
    return {
      ...row,
      ...(hasMonthlyDimension && typeof row.dimension === "string" ? {
        __dimension_display: monthLabel.format(new Date(
          Number(row.dimension.slice(0, 4)),
          Number(row.dimension.slice(5, 7)) - 1,
          1,
        )).replace(".", ""),
      } : {}),
      ...(typeof value === "number" && moneyUnit ? {
        [valueField]: value / 100,
        [displayField]: money(value, currency),
      } : {}),
    };
  });
  const spec = structuredClone(visualization.spec) as Record<string, unknown>;
  const encoding = (spec.encoding ?? {}) as Record<string, unknown>;
  const quantitativeChannels = new Set(["x", "y"]);
  const rewrite = (channel: string, item: Encoding): Encoding => {
    if (item.field === "dimension" && hasMonthlyDimension) {
      if (channel === "tooltip" || item.type === "nominal") {
        return { ...item, field: "__dimension_display", type: "nominal" };
      }
      if (item.type === "temporal" && quantitativeChannels.has(channel)) {
        return {
          ...item,
          axis: { format: "%b %Y", labelAngle: -32, labelOverlap: true, labelLimit: 92 },
        };
      }
    }
    if (item.field !== "value_minor") return item;
    const next: Encoding = { ...item, field: valueField };
    if (moneyUnit && channel === "tooltip") {
      next.field = displayField;
      next.type = "nominal";
    } else if (visualization.unit === "ratio" && quantitativeChannels.has(channel)) {
      next.title = "Porcentaje";
      next.axis = { format: ".0%" };
    } else if (moneyUnit && quantitativeChannels.has(channel)) {
      next.title = `${String(next.title ?? "Importe")} (${currency})`;
      next.axis = { format: "$,.2f" };
    } else if (visualization.unit === "count" && quantitativeChannels.has(channel)) {
      next.axis = { format: "d" };
    }
    return next;
  };
  for (const [channel, value] of Object.entries(encoding)) {
    encoding[channel] = Array.isArray(value)
      ? value.map((item) => item && typeof item === "object" ? rewrite(channel, item as Encoding) : item)
      : value && typeof value === "object" ? rewrite(channel, value as Encoding) : value;
  }
  spec.encoding = encoding;
  return { spec, data };
}

export function Chart({ visualization, currency }: Props): JSX.Element | null {
  const host = useRef<HTMLDivElement>(null);
  const [failed, setFailed] = useState(false);
  const isKpi = visualization?.kind === "kpi";
  useEffect(() => {
    let cancelled = false;
    let finalize: (() => void) | undefined;
    if (!host.current || !visualization || isKpi) return;
    const element = host.current;
    setFailed(false);
    const presented = visualPresentation(visualization, currency ?? "EUR");
    const spec = { ...presented.spec, data: { values: presented.data },
      config: {
        background: "transparent",
        font: "system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
        view: { stroke: null },
        axis: { labelColor: "#52635d", titleColor: "#173d34", gridColor: "#dfe9e4", domainColor: "#b8cbc3", tickColor: "#b8cbc3", labelFontSize: 11, titleFontSize: 12, titleFontWeight: 600, labelOverlap: true, labelLimit: 92 },
        axisX: { labelAngle: -32, labelPadding: 6 },
        legend: { labelColor: "#52635d", titleColor: "#173d34", labelFontSize: 11, titleFontSize: 12 },
        title: { color: "#173d34", fontSize: 15, fontWeight: 700, anchor: "start" },
        range: { category: ["#0f766e", "#2f9e82", "#70c3a8", "#e5a84b", "#d97757", "#64748b"] },
        bar: { color: "#16856f", cornerRadiusTopLeft: 5, cornerRadiusTopRight: 5, cornerRadiusBottomLeft: 5, cornerRadiusBottomRight: 5 },
        line: { color: "#0f766e", strokeWidth: 3 },
        area: { color: "#56b79b", opacity: 0.32, line: { color: "#0f766e", strokeWidth: 2 } },
        point: { color: "#0f766e", filled: true, size: 64 },
      },
    };
    void import("vega-embed")
      .then(({ default: embed }) => embed(element, spec as TopLevelSpec, {
        actions: false,
        renderer: "svg",
        tooltip: true,
        formatLocale: spanishNumberLocale(currency ?? "EUR"),
        timeFormatLocale: spanishTimeLocale,
      }))
      .then((result) => {
        if (cancelled) result.view.finalize();
        else finalize = () => result.view.finalize();
      })
      .catch(() => { if (!cancelled) setFailed(true); });
    return () => {
      cancelled = true;
      finalize?.();
      element.replaceChildren();
    };
  }, [visualization, isKpi]);
  if (!visualization || failed) {
    return visualization
      ? <p className="chart-fallback" role="status">Vista gráfica no disponible; consulta los datos.</p>
      : null;
  }
  if (isKpi) {
    const row = visualization.data[0] ?? {};
    const value = row[visualization.value_field];
    const formatted = typeof value === "number" && visualization.unit === "money"
      ? money(value, currency ?? "EUR")
      : String(value ?? "—");
    return (
      <p className="chart-kpi" role="status">
        <strong>Resultado</strong>
        <span>{formatted}</span>
      </p>
    );
  }
  return (
    <div
      className="visualization-chart"
      ref={host}
      role="img"
      aria-label="Gráfico de los resultados"
    />
  );
}
