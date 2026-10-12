import { useEffect, useRef, useState } from "react";
import type { TopLevelSpec } from "vega-lite";
import type { Visualization } from "./api";
import { compactMoney, money } from "./format";

type Props = {
  visualization: Visualization | null;
  currency: string | null;
};

type Encoding = Record<string, unknown>;

/**
 * Chart marks, mirrored from the `--chart-*` tokens in `styles.css` so a chart is painted from the
 * same palette as the screen around it. Six slots in a fixed order and never cycled: a seventh
 * series is a chart that should have been a table.
 *
 * The literals are the fallback for a host that has not loaded the stylesheet. The tokens are the
 * source of truth, and they are validated: every slot sits in the same lightness band, clears the
 * chroma floor, keeps ΔE ≥ 8 from its neighbour under simulated colour-vision deficiency, and
 * reaches 3:1 against the card. The palette this replaced was three lightness steps of one teal,
 * which is a magnitude ramp — two adjacent slices of a share chart were the same colour to
 * anybody, and the same colour twice over to a deutan reader.
 */
const CATEGORY = ["#0f8a6d", "#b45309", "#3b5bdb", "#cc79a7", "#4d7c0f", "#6d28d9"];

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

/** The two greens of a single-series bar chart: the leader, and every other bar. */
export type Emphasis = { leader: string; rest: string };

const EMPHASIS: Emphasis = { leader: "#1e5a48", rest: "#a9c9bd" };

// A label over every bar reads; over thirty it is a texture.
const MAX_LABELLED_BARS = 12;

/**
 * A lower-case name with a capital, the way a category is written on an axis. A name whose own
 * capitals are inside it ("iPhone", "eBay") is a brand spelled on purpose and stays as it is.
 */
function capitalised(text: string): string {
  if (!text || /^\p{Ll}\p{Lu}/u.test(text)) return text;
  return text[0]!.toUpperCase() + text.slice(1);
}

const MONTH_KEY = /^\d{4}-(0[1-9]|1[0-2])$/;
const DAY_KEY = /^\d{4}-\d{2}-\d{2}$/;
const MINUS = "\u2212";

const WHOLE = new Intl.NumberFormat("es-ES", {
  maximumFractionDigits: 0,
  useGrouping: "always",
} as unknown as Intl.NumberFormatOptions); // "always": es-ES leaves 4 digits ungrouped otherwise
const CENTS = new Intl.NumberFormat("es-ES", {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

/** A count as the axis writes it: grouped from a thousand, "−" for a negative. */
export function countLabel(value: number): string {
  return WHOLE.format(value).replace("-", MINUS);
}

/**
 * The amount over a bar, in the same voice as the axis beside it: "9,2 mil" from 99,95 €
 * up (where rounding would already read a thousand), whole euros below that, and cents only
 * when whole euros would round a real amount to 0.
 */
export function moneyLabel(minor: number): string {
  const label =
    Math.abs(minor) >= 99_950
      ? compactMoney(minor)
      : Math.abs(minor) < 100 && minor !== 0
        ? CENTS.format(minor / 100)
        : WHOLE.format(Math.round(minor / 100));
  return label.replace("-", MINUS);
}

function markType(mark: unknown): string | undefined {
  if (typeof mark === "string") return mark;
  if (mark && typeof mark === "object") return (mark as { type?: string }).type;
  return undefined;
}

export function visualPresentation(
  visualization: Extract<Visualization, { kind: "chart" }>,
  currency: string,
  emphasis: Emphasis = EMPHASIS,
) {
  const moneyUnit = visualization.unit === "money" || visualization.unit === "ratio";
  const valueField = moneyUnit ? "__value_major" : "value_minor";
  const displayField = "__value_display";
  // Each row is named by what its own key is -- a month, a day, a name -- and never by what the
  // other rows are: one non-month row among months ("total") used to be parsed as a date, threw,
  // and took the whole page down with it.
  const keys = visualization.data
    .map((row) => row.dimension)
    .filter((key): key is string => typeof key === "string");
  const hasNamedDimension = keys.length > 0;
  const allMonths = hasNamedDimension && keys.every((key) => MONTH_KEY.test(key));
  const allDays = hasNamedDimension && keys.every((key) => DAY_KEY.test(key));
  const yearsShown = new Set(keys.filter((key) => DAY_KEY.test(key)).map((key) => key.slice(0, 4)));
  const monthLabel = new Intl.DateTimeFormat("es-ES", { month: "short", year: "numeric" });
  const dayLabel = new Intl.DateTimeFormat("es-ES", {
    day: "numeric",
    month: "short",
    ...(yearsShown.size > 1 ? { year: "numeric" } : {}),
  });
  const displayOf = (key: string): string => {
    if (MONTH_KEY.test(key)) {
      return monthLabel
        .format(new Date(Number(key.slice(0, 4)), Number(key.slice(5, 7)) - 1, 1))
        .replace(".", "");
    }
    if (DAY_KEY.test(key)) {
      const [year, month, day] = key.split("-").map(Number);
      return dayLabel.format(new Date(year!, month! - 1, day!)).replace(".", "");
    }
    // "alquiler" on an axis reads as a value leaking through; a name starts with a capital.
    return capitalised(key);
  };
  const values = visualization.data
    .map((row) => row.value_minor)
    .filter((value): value is number => typeof value === "number");
  // One bar to look at, or none: the largest of amounts that are all spending of one sign, and
  // only when it is the largest alone. Negatives made the smallest expense the "leader", a tie
  // led with whichever came first, and all zeros led with every bar.
  const top = values.length > 1 ? Math.max(...values) : null;
  const leads =
    top !== null &&
    top > 0 &&
    values.every((value) => value >= 0) &&
    values.filter((value) => value === top).length === 1;
  const data = visualization.data.map<Record<string, unknown>>((row) => {
    const value = row.value_minor;
    return {
      ...row,
      ...(typeof row.dimension === "string" ? { __dimension_display: displayOf(row.dimension) } : {}),
      ...(typeof value === "number" && moneyUnit ? {
        [valueField]: value / 100,
        [displayField]: money(value, currency),
      } : {}),
      ...(typeof value === "number" ? {
        __value_short: visualization.unit === "money" ? moneyLabel(value) : countLabel(value),
        __emphasis: leads && value === top ? "leader" : "rest",
      } : {}),
    };
  });
  // What the plot draws, not what it was given: a top-8 of 35 rows is eight bars to label.
  const drawn =
    typeof visualization.limit === "number"
      ? Math.min(visualization.limit, data.length)
      : data.length;
  const spec = structuredClone(visualization.spec) as Record<string, unknown>;
  const encoding = (spec.encoding ?? {}) as Record<string, unknown>;
  const quantitativeChannels = new Set(["x", "y"]);
  /**
   * Axis chrome for the dimension, and only for the dimension.
   *
   * The rotation used to be a global `axisX.labelAngle`, which tipped every label on the bottom
   * axis. Most of these charts are horizontal bars — the service puts the group on `y` and the
   * measure on `x` — so what it actually rotated was the money.
   */
  const dimensionAxis = (channel: string, item: Encoding, extra: Encoding = {}): Encoding => {
    if (channel === "tooltip") {
      // The bars of a comparison are its periods, not groups of one.
      const title = visualization.comparison ? "Periodo" : allMonths ? "Mes" : allDays ? "Día" : "Grupo";
      return { ...item, title };
    }
    if (!quantitativeChannels.has(channel)) return item;
    // No axis title: the chart's own title, right above, already says what the groups are, and the
    // field name it fell back to ("dimension") was the one word on screen nobody could read. Every
    // label is shown while they fit: Vega's overlap pass hid five of eight categories.
    const axis: Encoding = {
      title: null,
      grid: false,
      labelOverlap: drawn > MAX_LABELLED_BARS,
      labelLimit: 92,
      ...extra,
    };
    if (channel === "x") axis.labelAngle = -32;
    return { ...item, axis };
  };
  // The measure's axis: a few dashed gridlines to read against, and nothing else.
  const measureAxis = (extra: Encoding): Encoding => ({
    title: null,
    grid: true,
    gridDash: [3, 3],
    domain: false,
    ticks: false,
    tickCount: 4,
    ...extra,
  });
  const rewrite = (channel: string, item: Encoding): Encoding => {
    if (item.field === "dimension") {
      if (hasNamedDimension && (channel === "tooltip" || item.type === "nominal")) {
        return dimensionAxis(channel, { ...item, field: "__dimension_display", type: "nominal" });
      }
      if (allMonths && item.type === "temporal") {
        return dimensionAxis(channel, item, { format: "%b %Y" });
      }
      if (allDays && item.type === "temporal") {
        return dimensionAxis(channel, item, { format: "%e %b" });
      }
      return dimensionAxis(channel, item);
    }
    if (item.field === "series") {
      // The periods of a comparison, or a recurrence's cadences: the legend's own entries name
      // them, so its title -- the field name "series" when nobody set one -- goes.
      if (channel === "tooltip") return { ...item, title: item.title ?? "Periodo" };
      return channel === "color" ? { ...item, title: item.title ?? null } : item;
    }
    if (item.field === "occurred_at") {
      // A date a person reads: "1 abr 2026", under "Fecha", not "occurred_at: Apr 01, 2026".
      if (channel === "tooltip") return { ...item, title: "Fecha", format: "%e %b %Y" };
      return { ...item, title: null, axis: { ...(item.axis as Encoding | undefined), title: null } };
    }
    if (item.field !== "value_minor") return item;
    const next: Encoding = { ...item, field: valueField };
    if (moneyUnit && channel === "tooltip") {
      next.field = displayField;
      next.type = "nominal";
      next.title = "Importe";
    } else if (channel === "tooltip") {
      next.title = visualization.unit === "count" ? "Movimientos" : "Valor";
    } else if (visualization.unit === "ratio" && quantitativeChannels.has(channel)) {
      next.title = "Porcentaje";
      next.axis = measureAxis({ format: ".0%" });
    } else if (moneyUnit && quantitativeChannels.has(channel)) {
      next.title = `${String(next.title ?? "Importe")} (${currency})`;
      // Whole euros on the ticks. The cents were two digits of noise on every gridline of a
      // phone-width axis, and the exact figure is a hover and a tap away in "Ver los datos".
      next.axis = measureAxis({ format: "$,.0f" });
    } else if (visualization.unit === "count" && quantitativeChannels.has(channel)) {
      next.axis = measureAxis({ format: "d" });
    }
    return next;
  };
  for (const [channel, value] of Object.entries(encoding)) {
    encoding[channel] = Array.isArray(value)
      ? value.map((item) => item && typeof item === "object" ? rewrite(channel, item as Encoding) : item)
      : value && typeof value === "object" ? rewrite(channel, value as Encoding) : value;
  }
  // Hovering a bar of a comparison has to say which period it is, and the tooltip the service
  // sends names only the group and the amount.
  const color = encoding.color as Encoding | undefined;
  if (color?.field === "series") {
    const tooltip = encoding.tooltip;
    const listed = Array.isArray(tooltip) ? tooltip : tooltip ? [tooltip] : [];
    if (!listed.some((item) => (item as Encoding)?.field === "series")) {
      encoding.tooltip = [...listed, { field: "series", type: "nominal", title: color.title ?? "Periodo" }];
    }
  }
  spec.encoding = encoding;
  /*
   * The title leaves the spec and is typeset by the app.
   *
   * Vega set it in its own size, weight and spacing, so a thread with four answers in it had four
   * headings that agreed with nothing around them — not with each other, and not with the sentence
   * directly above. `chartTitle` renders the same string in the app's type.
   */
  delete spec.title;
  /*
   * A discrete dimension sizes the plot from its own rows: one band per group, always the same
   * thickness. The fixed 220px it replaces divided that height by however many groups the answer
   * had, so three categories came out as three slabs and thirty as thirty threads.
   */
  const dimensionRow = encoding.y as Encoding | undefined;
  if (dimensionRow?.type === "nominal" || dimensionRow?.type === "temporal") {
    spec.height = { step: 26 };
  } else if (!encoding.y && !encoding.theta) {
    // A normalised share is a single stacked bar. At the default height it is a slab.
    spec.height = 56;
  }
  // The plot takes the card's width, so a chart never scrolls sideways inside a bubble -- whatever
  // width the spec arrived with was chosen without knowing the screen.
  spec.width = "container";
  spec.autosize = { type: "fit-x", contains: "padding" };
  labelled(spec, encoding, drawn, visualization.unit, emphasis, leads);
  return { spec, data };
}

/**
 * A single-series bar chart, labelled and led.
 *
 * Its amount over every bar, so the figure is read rather than estimated against a gridline, and
 * the largest bar in the brand green with the rest a step lighter: the chart says where to look
 * without a second colour. Only the drawing changes -- the mark, the orientation, the order and
 * the rows the service chose stay as they are, so a later "ponlo vertical" edits the same chart.
 */
function labelled(
  spec: Record<string, unknown>,
  encoding: Record<string, unknown>,
  rows: number,
  unit: string,
  emphasis: Emphasis,
  leads: boolean,
): void {
  if (markType(spec.mark) !== "bar" || encoding.color) return;
  if (rows < 2 || rows > MAX_LABELLED_BARS || (unit !== "money" && unit !== "count")) return;
  const x = encoding.x as Encoding | undefined;
  const y = encoding.y as Encoding | undefined;
  const horizontal = x?.type === "quantitative";
  const measure = horizontal ? x : y;
  if (!measure || measure.type !== "quantitative") return;
  // Headroom past the longest bar, or its own label runs off the card.
  encoding[horizontal ? "x" : "y"] = {
    ...measure,
    scale: { ...(measure.scale as Encoding | undefined), padding: horizontal ? 36 : 14 },
  };
  // Two layers share the dimension's scale, and Vega only orders a shared domain by count, min or
  // max. One row per group makes max the same order as the sum the service asked for.
  const dimension = (horizontal ? y : x) as Encoding | undefined;
  const sort = dimension?.sort as Encoding | undefined;
  if (dimension && sort && typeof sort === "object" && "field" in sort) {
    encoding[horizontal ? "y" : "x"] = { ...dimension, sort: { ...sort, op: "max" } };
  }
  const bar = typeof spec.mark === "string" ? { type: "bar" } : (spec.mark as Encoding);
  delete spec.mark;
  spec.layer = [
    {
      mark: bar,
      // Without a single largest bar there is nothing to point at: every bar in the brand green.
      encoding: leads
        ? {
            color: {
              field: "__emphasis",
              type: "nominal",
              scale: { domain: ["leader", "rest"], range: [emphasis.leader, emphasis.rest] },
              legend: null,
            },
          }
        : {},
    },
    {
      mark: horizontal
        ? { type: "text", align: "left", baseline: "middle", dx: 4, fontSize: 10, fontWeight: 600 }
        : { type: "text", align: "center", baseline: "bottom", dy: -3, fontSize: 10, fontWeight: 600 },
      encoding: { text: { field: "__value_short", type: "nominal" } },
    },
  ];
}

/**
 * One line under the title saying where most of it went -- "Alquiler es el 47 % del total." --
 * when a share of a whole is something the chart shows: money, one series, named groups, none
 * negative. A month-by-month chart or a comparison has no share worth a sentence.
 */
export function chartLead(visualization: Visualization | null): string | null {
  if (visualization?.kind !== "chart" || visualization.unit !== "money") return null;
  // Two periods side by side add up to nothing anybody spent: «abril es el 69 % del total» was a
  // share of a sum that does not exist.
  if (visualization.comparison) return null;
  const encoding = (visualization.spec.encoding ?? {}) as Record<string, unknown>;
  if (encoding.color) return null;
  // Parts of a whole are bars, or slices: a point per date is a series over time, and the same
  // subscription on two dates is one thing twice, not two halves.
  if (!["bar", "arc"].includes(markType(visualization.spec.mark) ?? "")) return null;
  const rows = visualization.data.filter(
    (row) => typeof row.dimension === "string" && typeof row.value_minor === "number",
  );
  if (rows.length < 2 || rows.length !== visualization.data.length) return null;
  if (new Set(rows.map((row) => row.dimension)).size !== rows.length) return null;
  if (rows.some((row) => /^\d{4}-\d{2}/.test(String(row.dimension)))) return null;
  const values = rows.map((row) => row.value_minor as number);
  if (values.some((value) => value < 0)) return null;
  const total = values.reduce((sum, value) => sum + value, 0);
  if (total <= 0) return null;
  const top = Math.max(...values);
  // A tie at the top has no single leader to name.
  if (values.filter((value) => value === top).length > 1) return null;
  const leader = rows[values.indexOf(top)]!;
  const share = Math.round((Number(leader.value_minor) / total) * 100);
  return `${capitalised(String(leader.dimension))} es el ${share} % del total.`;
}

/** The title the service wrote for a chart, rendered by the app rather than by Vega. */
export function chartTitle(visualization: Visualization | null): string | null {
  if (visualization?.kind !== "chart") return null;
  const title = visualization.spec.title;
  return typeof title === "string" && title.trim() ? title : null;
}

/**
 * What the chart is leaving out, in words, and only when it is leaving something out.
 *
 * Same rule as the caveat under the movements: a chart drawing every row it was given says
 * nothing, because a caveat that is false makes the true ones unbelievable. A plot cut to the rows
 * that fit is a sample of a total the sentence above it states in full, and that has to be said —
 * the rows themselves are all still one tap away under "Ver los datos".
 */
export function chartSample(visualization: Visualization | null): string | null {
  if (visualization?.kind !== "chart") return null;
  const limit = visualization.limit;
  if (typeof limit !== "number" || limit >= visualization.data.length) return null;
  return `Se muestran los ${limit} mayores de ${visualization.data.length}.`;
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
    const palette = getComputedStyle(document.documentElement);
    let presented: ReturnType<typeof visualPresentation>;
    try {
      presented = visualPresentation(visualization, currency ?? "EUR", {
        leader: palette.getPropertyValue("--brand").trim() || EMPHASIS.leader,
        rest: palette.getPropertyValue("--brand-soft").trim() || EMPHASIS.rest,
      });
    } catch {
      // A chart that cannot be drawn says so in its place; it must never unmount the thread.
      setFailed(true);
      return;
    }
    // Vega needs literal colours, and the app keeps its palette in `:root`. Reading the tokens
    // here is what stops the two from drifting: the teal the bars used was a shade off the teal
    // every button in the app is painted with, which reads as a mistake rather than a choice.
    const style = getComputedStyle(document.documentElement);
    const token = (name: string, fallback: string) =>
      style.getPropertyValue(name).trim() || fallback;
    const ink = token("--ink", "#0d1b18");
    const inkSoft = token("--ink-soft", "#5c6b66");
    const line = token("--line", "#e3e8e6");
    const card = token("--card", "#ffffff");
    const accent = token("--accent", "#14806a");
    const spec = { ...presented.spec, data: { values: presented.data },
      config: {
        background: "transparent",
        font: token("font-family", "system-ui, sans-serif"),
        view: { stroke: null },
        // Hairline and recessive: the grid is one shade off the card, and the axis rule is the same
        // line every other card in the app is drawn with.
        axis: { labelColor: inkSoft, titleColor: ink, gridColor: line, domainColor: line, tickColor: line, labelFontSize: 11, titleFontSize: 11, titleFontWeight: 600, labelPadding: 6, tickSize: 4, labelOverlap: true, labelLimit: 92 },
        legend: { labelColor: inkSoft, titleColor: ink, labelFontSize: 11, titleFontSize: 11, symbolType: "square", orient: "top", direction: "horizontal", offset: 8 },
        range: { category: CATEGORY.map((fallback, index) => token(`--chart-${index + 1}`, fallback)) },
        // `cornerRadiusEnd` rounds the end the data reaches, whichever way the bar runs. The four
        // explicit corners it replaces rounded a horizontal bar at the baseline too, which lifts
        // every bar off the axis it is measured against. The card-coloured hairline is the gap
        // between the segments of a stacked share, which otherwise touch.
        bar: { color: accent, cornerRadiusEnd: 4, stroke: card, strokeWidth: 1 },
        line: { color: accent, strokeWidth: 2 },
        area: { color: accent, opacity: 0.16, line: { color: accent, strokeWidth: 2 } },
        point: { color: accent, filled: true, size: 70, stroke: card, strokeWidth: 1.5 },
        text: { color: ink },
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
  }, [visualization, isKpi, currency]);
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
      : typeof value === "number"
        ? countLabel(value)
        : String(value ?? "—");
    return (
      <p className="chart-kpi" role="status">
        <strong>Resultado</strong>
        <span>{formatted}</span>
      </p>
    );
  }
  // The label used to be the same six words on every chart in the thread, which tells a screen
  // reader that there is a chart and nothing about which one.
  const title = chartTitle(visualization);
  const sample = chartSample(visualization);
  const lead = chartLead(visualization);
  return (
    <figure className="chart-figure">
      {title && <figcaption className="chart-title">{title}</figcaption>}
      {lead && <p className="chart-lead">{lead}</p>}
      <div
        className="visualization-chart"
        ref={host}
        role="img"
        aria-label={title ?? "Gráfico de los resultados"}
      />
      {sample && <p className="chart-sample">{sample}</p>}
    </figure>
  );
}
