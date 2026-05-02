import { describe, expect, it } from "vitest";
import { mkTimelinePayload } from "@/test/factories";
import { TimelineDataSchema } from "./timeline";

describe("TimelineDataSchema", () => {
  it("accepts the full exported timeline shape", () => {
    expect(TimelineDataSchema.safeParse(mkTimelinePayload()).success).toBe(true);
  });

  it("rejects missing exporter-guaranteed arrays", () => {
    const payload: Record<string, unknown> = { ...mkTimelinePayload() };
    delete payload.dailyTickers;

    expect(TimelineDataSchema.safeParse(payload).success).toBe(false);
  });

  it("rejects missing exporter-guaranteed market fields", () => {
    const payload = mkTimelinePayload({
      market: {
        indices: [
          {
            ticker: "^GSPC",
            name: "S&P 500",
            monthReturn: 2.5,
            ytdReturn: 12.3,
            current: 5500,
            high52w: 5800,
            low52w: 4200,
          },
        ],
      },
    });

    expect(TimelineDataSchema.safeParse(payload).success).toBe(false);
  });

  it("rejects missing sync metadata", () => {
    const payload: Record<string, unknown> = { ...mkTimelinePayload() };
    delete payload.syncMeta;

    expect(TimelineDataSchema.safeParse(payload).success).toBe(false);
  });

  it("rejects numeric Qianji booleans now that the exporter emits JSON booleans", () => {
    const payload = mkTimelinePayload({
      qianjiTxns: [{
        date: "2026-01-15",
        type: "income",
        category: "401K",
        amount: 100,
        accountTo: "",
        isRetirement: 1,
      }],
    });

    expect(TimelineDataSchema.safeParse(payload).success).toBe(false);
  });
});
