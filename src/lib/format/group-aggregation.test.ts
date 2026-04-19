import { describe, it, expect } from "vitest";
import { classifyTxn, type TxnType } from "./group-aggregation";
import type { FidelityTxn } from "@/lib/schemas";

const t = (actionType: string, extra: Partial<FidelityTxn> = {}): FidelityTxn => ({
  runDate: "2026-01-02",
  actionType,
  symbol: "VOO",
  amount: 100,
  quantity: 1,
  price: 100,
  ...extra,
});

describe("classifyTxn", () => {
  it("buy/sell → REAL", () => {
    expect(classifyTxn(t("buy"))).toBe<TxnType>("REAL");
    expect(classifyTxn(t("sell"))).toBe<TxnType>("REAL");
  });

  it("reinvestment → REINVEST", () => {
    expect(classifyTxn(t("reinvestment"))).toBe<TxnType>("REINVEST");
  });

  it("price=0 + qty≠0 → SPLIT (Fidelity DISTRIBUTION encoding)", () => {
    expect(classifyTxn(t("distribution", { price: 0, quantity: 1 }))).toBe<TxnType>("SPLIT");
  });

  it("dividend / interest / other → OTHER", () => {
    expect(classifyTxn(t("dividend"))).toBe<TxnType>("OTHER");
    expect(classifyTxn(t("interest"))).toBe<TxnType>("OTHER");
    expect(classifyTxn(t("deposit"))).toBe<TxnType>("OTHER");
  });
});
