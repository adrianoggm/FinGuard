import { afterEach, describe, expect, it, vi } from "vitest";

import { renameMetric, reorderMetrics, saveMetric, type SavedMetric } from "./api";
import { moveMetricDown, moveMetricUp } from "./Finances";

const rows: SavedMetric[] = [
  { id: "one", title: "Uno", position: 0, version: 1 },
  { id: "two", title: "Dos", position: 1, version: 2 },
  { id: "three", title: "Tres", position: 2, version: 1 },
];

afterEach(() => vi.restoreAllMocks());

describe("saved metrics", () => {
  it("moves only the selected metric one position up", () => {
    expect(moveMetricUp(rows, 2)).toEqual(["one", "three", "two"]);
    expect(moveMetricUp(rows, 0)).toEqual(["one", "two", "three"]);
  });

  it("moves only the selected metric one position down", () => {
    expect(moveMetricDown(rows, 0)).toEqual(["two", "one", "three"]);
    expect(moveMetricDown(rows, 2)).toEqual(["one", "two", "three"]);
  });

  it("uses optimistic locking when renaming", async () => {
    const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(rows[1]), { status: 200 }),
    );

    await renameMetric("two", "Nuevo", 2);

    const [, init] = fetch.mock.calls[0] ?? [];
    expect(init?.method).toBe("PATCH");
    expect(init?.headers).toMatchObject({ "If-Match": "2" });
    expect(init?.body).toBe(JSON.stringify({ title: "Nuevo" }));
  });

  it("sends the complete order atomically", async () => {
    const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(null, { status: 204 }),
    );

    await reorderMetrics(["two", "one", "three"]);

    const [, init] = fetch.mock.calls[0] ?? [];
    expect(init?.method).toBe("PUT");
    expect(init?.body).toBe('["two","one","three"]');
  });

  it("gives every save attempt an idempotency key", async () => {
    const fetch = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify(rows[0]), { status: 201 }),
    );

    await saveMetric("Uno", "signed-definition");

    const [, init] = fetch.mock.calls[0] ?? [];
    expect(init?.method).toBe("POST");
    expect(init?.headers).toMatchObject({ "Idempotency-Key": expect.any(String) });
  });
});
