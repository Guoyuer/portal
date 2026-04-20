import { describe, it, expect } from "vitest";
import { EQUIVALENT_GROUPS, GROUP_BY_TICKER, groupOfTicker } from "./equivalent-groups";

describe("equivalent groups", () => {
  it("indexes every listed ticker back to its group", () => {
    for (const [key, group] of Object.entries(EQUIVALENT_GROUPS)) {
      for (const t of group.tickers) {
        expect(GROUP_BY_TICKER.get(t)).toBe(key);
      }
    }
  });

  it("returns null for tickers not in any group", () => {
    expect(groupOfTicker("SOLO_TICKER_NOT_IN_ANY_GROUP")).toBeNull();
  });

  it("finds QQQ in nasdaq_100", () => {
    expect(groupOfTicker("QQQ")).toBe("nasdaq_100");
    expect(groupOfTicker("QQQM")).toBe("nasdaq_100");
  });
});
