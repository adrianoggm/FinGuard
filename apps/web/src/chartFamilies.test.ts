import { describe, expect, it } from "vitest";
import { compile } from "vega-lite";
import type { TopLevelSpec } from "vega-lite";
import families from "./__fixtures__/chart-families.json";

// Two separate properties, and the separation is the point: `compile()` need not consult
// `$schema` to choose a version, so a single "it compiles" test would pass over a stale schema
// URL forever. One assertion on what we declare, one on what actually compiles.
describe("every chart family the backend emits", () => {
  it("declares the Vega-Lite version the client has installed", () => {
    for (const family of families) {
      expect(family.spec.$schema).toBe(
        "https://vega.github.io/schema/vega-lite/v6.json",
      );
    }
  });

  it("compiles under Vega-Lite 6", () => {
    for (const family of families) {
      expect(() =>
        compile({ ...family.spec, data: { values: family.data } } as TopLevelSpec),
      ).not.toThrow();
    }
  });

  it("covers every family, so a new mark cannot arrive unmeasured", () => {
    const marks = new Set(
      families.map((family) =>
        typeof family.spec.mark === "string" ? family.spec.mark : family.spec.mark.type,
      ),
    );
    expect(marks).toEqual(new Set(["bar", "line", "area", "point", "arc"]));

    // The mark set alone cannot catch a deleted family: four of the eight share "bar", so
    // dropping one of them from the exporter still leaves the same five marks behind. Assert
    // the family names themselves, not just what they render as.
    const names = new Set(families.map((family) => family.name));
    expect(names).toEqual(
      new Set([
        "breakdown",
        "temporal",
        "cumulative",
        "frequency",
        "recurrence",
        "ratio_share",
        "categorical_comparison",
        "donut",
      ]),
    );
  });
});
