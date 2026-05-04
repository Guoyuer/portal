import { describe, it, expect } from "vitest";
import { buildCategorySummaryModel } from "./category-summary-model";
import type { ApiTicker } from "@/lib/compute/computed-types";
import { mkApiTicker as mkTicker, mkApiCategory as mkCategory } from "@/test/factories";

const groupTickers = (...args: Parameters<typeof buildCategorySummaryModel>) => buildCategorySummaryModel(...args).grouped;

describe("groupTickers", () => {
  it("groups tickers by category and subtype, summing values into subtypes", () => {
    const categories = [mkCategory("US Equity", 500, { pct: 50, target: 40 })];
    const tickers: ApiTicker[] = [
      mkTicker({ ticker: "VOO", value: 300, category: "US Equity", subtype: "Broad" }),
      mkTicker({ ticker: "VTI", value: 100, category: "US Equity", subtype: "Broad" }),
      mkTicker({ ticker: "QQQM", value: 100, category: "US Equity", subtype: "Growth" }),
    ];
    const out = groupTickers(categories, tickers, 1000);
    expect(out).toHaveLength(1);
    const us = out[0];
    expect(us.name).toBe("US Equity");
    expect(us.subtypes).toHaveLength(2);
    const broad = us.subtypes.find((s) => s.name === "Broad")!;
    expect(broad.value).toBe(400);
    expect(broad.pct).toBeCloseTo(40);
    const growth = us.subtypes.find((s) => s.name === "Growth")!;
    expect(growth.value).toBe(100);
    expect(growth.pct).toBeCloseTo(10);
  });

  it("tags equity categories correctly (US Equity / Non-US Equity / Crypto)", () => {
    const categories = [
      mkCategory("US Equity", 100),
      mkCategory("Non-US Equity", 100),
      mkCategory("Crypto", 50),
      mkCategory("Safe Net", 200),
    ];
    const out = groupTickers(categories, [], 450);
    expect(out.find((c) => c.name === "US Equity")!.isEquity).toBe(true);
    expect(out.find((c) => c.name === "Non-US Equity")!.isEquity).toBe(true);
    expect(out.find((c) => c.name === "Crypto")!.isEquity).toBe(true);
    expect(out.find((c) => c.name === "Safe Net")!.isEquity).toBe(false);
  });

  it("substitutes '(other)' for empty subtype string", () => {
    const categories = [mkCategory("Crypto", 50)];
    const tickers: ApiTicker[] = [mkTicker({ ticker: "BTC", value: 50, category: "Crypto", subtype: "" })];
    const out = groupTickers(categories, tickers, 50);
    expect(out[0].subtypes[0].name).toBe("(other)");
  });

  it("returns 0 pct when total is 0 (avoids divide-by-zero)", () => {
    const categories = [mkCategory("US Equity", 0)];
    const tickers: ApiTicker[] = [mkTicker({ ticker: "VOO", value: 100, category: "US Equity", subtype: "Broad" })];
    const out = groupTickers(categories, tickers, 0);
    expect(out[0].subtypes[0].pct).toBe(0);
  });

  it("keeps category order from input", () => {
    const categories = [
      mkCategory("Safe Net", 200),
      mkCategory("US Equity", 100),
      mkCategory("Crypto", 50),
    ];
    const out = groupTickers(categories, [], 350);
    expect(out.map((c) => c.name)).toEqual(["Safe Net", "US Equity", "Crypto"]);
  });

  it("produces empty subtypes array for a category with no tickers", () => {
    const categories = [mkCategory("Safe Net", 200)];
    const out = groupTickers(categories, [], 200);
    expect(out[0].subtypes).toEqual([]);
  });

  it("ignores tickers whose category isn't in the categories array", () => {
    const categories = [mkCategory("US Equity", 100)];
    const tickers: ApiTicker[] = [
      mkTicker({ ticker: "VOO", value: 100, category: "US Equity", subtype: "Broad" }),
      mkTicker({ ticker: "ROGUE", value: 999, category: "Mystery", subtype: "Broad" }),
    ];
    const out = groupTickers(categories, tickers, 100);
    expect(out).toHaveLength(1);
    expect(out[0].subtypes).toHaveLength(1);
    expect(out[0].subtypes[0].tickers.map((t) => t.ticker)).toEqual(["VOO"]);
  });
});
