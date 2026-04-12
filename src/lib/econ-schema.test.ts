import { describe, it, expect } from "vitest";
import { EconDataSchema } from "./econ-schema";

// ── EconDataSchema: series JSON string transform ─────────────────────────

describe("EconDataSchema series parsing", () => {
  it("accepts a JSON string per key (D1 storage format) and parses it", () => {
    const payload = {
      generatedAt: "2026-04-12T00:00:00Z",
      snapshot: { fedFundsRate: 4.33 },
      series: {
        fedFundsRate: JSON.stringify([
          { date: "2024-01", value: 5.33 },
          { date: "2025-01", value: 4.33 },
        ]),
      },
    };
    const parsed = EconDataSchema.safeParse(payload);
    expect(parsed.success).toBe(true);
    if (parsed.success) {
      expect(parsed.data.series.fedFundsRate).toEqual([
        { date: "2024-01", value: 5.33 },
        { date: "2025-01", value: 4.33 },
      ]);
    }
  });

  it("still accepts already-parsed arrays (mock API shape)", () => {
    const payload = {
      generatedAt: "2026-04-12T00:00:00Z",
      snapshot: {},
      series: {
        cpiYoy: [
          { date: "2024-01", value: 3.1 },
          { date: "2024-06", value: 3.0 },
        ],
      },
    };
    const parsed = EconDataSchema.safeParse(payload);
    expect(parsed.success).toBe(true);
    if (parsed.success) {
      expect(parsed.data.series.cpiYoy).toHaveLength(2);
    }
  });

  it("rejects malformed JSON in a series string", () => {
    const payload = {
      generatedAt: "2026-04-12T00:00:00Z",
      snapshot: {},
      series: { bad: "not valid json [" },
    };
    const parsed = EconDataSchema.safeParse(payload);
    expect(parsed.success).toBe(false);
  });

  it("rejects a JSON string whose array entries are malformed", () => {
    const payload = {
      generatedAt: "2026-04-12T00:00:00Z",
      snapshot: {},
      series: { bad: JSON.stringify([{ date: 123, value: "x" }]) },
    };
    const parsed = EconDataSchema.safeParse(payload);
    expect(parsed.success).toBe(false);
  });

  it("defaults missing series to empty record", () => {
    const payload = {
      generatedAt: "2026-04-12T00:00:00Z",
      snapshot: {},
    };
    const parsed = EconDataSchema.safeParse(payload);
    expect(parsed.success).toBe(true);
    if (parsed.success) {
      expect(parsed.data.series).toEqual({});
    }
  });
});
