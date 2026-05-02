import { describe, expect, it } from "vitest";
import { EconDataSchema } from "./econ";

// ── EconDataSchema: endpoint JSON shape ─────────────────────────────────

describe("EconDataSchema", () => {
  it("accepts series arrays", () => {
    const parsed = EconDataSchema.safeParse({
      generatedAt: "2026-04-12T00:00:00Z",
      snapshot: { fedFundsRate: 4.33 },
      series: {
        fedFundsRate: [
          { date: "2024-01", value: 5.33 },
          { date: "2025-01", value: 4.33 },
        ],
      },
    });

    expect(parsed.success).toBe(true);
    if (parsed.success) {
      expect(parsed.data.series.fedFundsRate).toEqual([
        { date: "2024-01", value: 5.33 },
        { date: "2025-01", value: 4.33 },
      ]);
    }
  });

  it("rejects JSON strings now that the exporter emits arrays", () => {
    const parsed = EconDataSchema.safeParse({
      generatedAt: "2026-04-12T00:00:00Z",
      snapshot: {},
      series: {
        fedFundsRate: JSON.stringify([{ date: "2024-01", value: 5.33 }]),
      },
    });

    expect(parsed.success).toBe(false);
  });

  it("rejects malformed series entries", () => {
    const parsed = EconDataSchema.safeParse({
      generatedAt: "2026-04-12T00:00:00Z",
      snapshot: {},
      series: { bad: [{ date: 123, value: "x" }] },
    });

    expect(parsed.success).toBe(false);
  });

  it("rejects missing series", () => {
    const parsed = EconDataSchema.safeParse({
      generatedAt: "2026-04-12T00:00:00Z",
      snapshot: {},
    });

    expect(parsed.success).toBe(false);
  });
});
