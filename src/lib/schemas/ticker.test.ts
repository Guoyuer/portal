import { describe, expect, it } from "vitest";
import { TickerPriceResponseSchema } from "./ticker";

describe("TickerPriceResponseSchema", () => {
  it("accepts complete ticker payloads", () => {
    const parsed = TickerPriceResponseSchema.safeParse({
      prices: [{ date: "2026-01-02", close: 500 }],
      transactions: [],
    });

    expect(parsed.success).toBe(true);
  });

  it("rejects missing exporter-guaranteed arrays", () => {
    expect(TickerPriceResponseSchema.safeParse({}).success).toBe(false);
  });
});
