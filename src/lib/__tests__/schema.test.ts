import { describe, it, expect } from "vitest";
import { TimelineDataSchema } from "../schema";

// ── MarketMetaSchema via TimelineDataSchema: partial input fills in nulls ─

describe("MarketMetaSchema defaulting", () => {
  it("fills missing meta keys with null (partial input)", () => {
    const payload = {
      daily: [{ date: "2026-01-01", total: 100, usEquity: 55, nonUsEquity: 15, crypto: 3, safeNet: 27, liabilities: 0 }],
      categories: [{ key: "usEquity", name: "US Equity", displayOrder: 0, targetPct: 55 }],
      market: { indices: [], meta: { fedRate: 5.5 } },
    };
    const parsed = TimelineDataSchema.safeParse(payload);
    expect(parsed.success).toBe(true);
    if (parsed.success) {
      const m = parsed.data.market!.meta;
      expect(m.fedRate).toBe(5.5);
      expect(m.treasury10y).toBeNull();
      expect(m.cpi).toBeNull();
      expect(m.unemployment).toBeNull();
      expect(m.vix).toBeNull();
      expect(m.dxy).toBeNull();
      expect(m.usdCny).toBeNull();
    }
  });

  it("fills every meta key when meta is an empty object", () => {
    const payload = {
      daily: [{ date: "2026-01-01", total: 100, usEquity: 55, nonUsEquity: 15, crypto: 3, safeNet: 27, liabilities: 0 }],
      categories: [{ key: "usEquity", name: "US Equity", displayOrder: 0, targetPct: 55 }],
      market: { indices: [], meta: {} },
    };
    const parsed = TimelineDataSchema.safeParse(payload);
    expect(parsed.success).toBe(true);
    if (parsed.success) {
      const m = parsed.data.market!.meta;
      expect(m.fedRate).toBeNull();
      expect(m.treasury10y).toBeNull();
      expect(m.cpi).toBeNull();
      expect(m.unemployment).toBeNull();
      expect(m.vix).toBeNull();
      expect(m.dxy).toBeNull();
      expect(m.usdCny).toBeNull();
    }
  });
});
