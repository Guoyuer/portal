import { describe, it, expect } from "vitest";
import { mergeSeriesByDate, type LineConfig } from "./time-series-chart";
import type { EconPoint } from "@/lib/schemas";

const lineA: LineConfig = { dataKey: "a", label: "A", color: "#000" };
const lineB: LineConfig = { dataKey: "b", label: "B", color: "#fff" };

const pt = (date: string, value: number): EconPoint => ({ date, value });

describe("mergeSeriesByDate", () => {
  it("returns an empty array when no series have points", () => {
    expect(mergeSeriesByDate([lineA, lineB], { a: [], b: [] })).toEqual([]);
  });

  it("merges two series that overlap on every date", () => {
    const rows = mergeSeriesByDate([lineA, lineB], {
      a: [pt("2025-01-01", 1), pt("2025-02-01", 2)],
      b: [pt("2025-01-01", 10), pt("2025-02-01", 20)],
    });
    expect(rows).toEqual([
      { date: "2025-01-01", a: 1, b: 10 },
      { date: "2025-02-01", a: 2, b: 20 },
    ]);
  });

  it("produces rows with only the keys that exist at each date (gaps left as missing)", () => {
    const rows = mergeSeriesByDate([lineA, lineB], {
      a: [pt("2025-01-01", 1)],
      b: [pt("2025-02-01", 20)],
    });
    expect(rows).toEqual([
      { date: "2025-01-01", a: 1 },
      { date: "2025-02-01", b: 20 },
    ]);
    // Neither row has the opposite series key
    expect(rows[0]).not.toHaveProperty("b");
    expect(rows[1]).not.toHaveProperty("a");
  });

  it("sorts rows chronologically regardless of input order", () => {
    const rows = mergeSeriesByDate([lineA], {
      a: [pt("2025-03-01", 3), pt("2024-12-01", 1), pt("2025-01-01", 2)],
    });
    expect(rows.map((r) => r.date)).toEqual(["2024-12-01", "2025-01-01", "2025-03-01"]);
  });

  it("ignores lines whose dataKey has no entry in data (defaults to [])", () => {
    const rows = mergeSeriesByDate([lineA, lineB], {
      a: [pt("2025-01-01", 1)],
      // b: missing entirely
    });
    expect(rows).toEqual([{ date: "2025-01-01", a: 1 }]);
  });

  it("handles a later-same-date value by overwriting (last write wins within a dataKey)", () => {
    const rows = mergeSeriesByDate([lineA], {
      a: [pt("2025-01-01", 1), pt("2025-01-01", 9)],
    });
    expect(rows).toEqual([{ date: "2025-01-01", a: 9 }]);
  });
});
