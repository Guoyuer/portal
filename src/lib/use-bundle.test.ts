// @vitest-environment jsdom

import { describe, it, expect, beforeAll, afterAll, afterEach } from "vitest";
import { renderHook, waitFor, act } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { useBundle } from "./use-bundle";

// ── Helpers ─────────────────────────────────────────────────────────────

const TIMELINE_URL = "http://localhost:8787/timeline";

const VALID_PAYLOAD = {
  daily: [
    { date: "2026-01-02", total: 100000, usEquity: 55000, nonUsEquity: 15000, crypto: 3000, safeNet: 27000, liabilities: -5000 },
    { date: "2026-01-03", total: 101000, usEquity: 55500, nonUsEquity: 15200, crypto: 3100, safeNet: 27200, liabilities: -5000 },
    { date: "2026-01-06", total: 102000, usEquity: 56000, nonUsEquity: 15400, crypto: 3200, safeNet: 27400, liabilities: -5000 },
  ],
  dailyTickers: [],
  fidelityTxns: [],
  qianjiTxns: [],
  market: null,
  holdingsDetail: null,
  syncMeta: null,
};

// ── MSW server ──────────────────────────────────────────────────────────

const server = setupServer(
  http.get(TIMELINE_URL, () => HttpResponse.json(VALID_PAYLOAD)),
);

beforeAll(() => server.listen({ onUnhandledRequest: "bypass" }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

// ── Tests ───────────────────────────────────────────────────────────────

describe("useBundle", () => {
  it("starts in loading state", () => {
    const { result } = renderHook(() => useBundle());
    expect(result.current.loading).toBe(true);
    expect(result.current.error).toBeNull();
    expect(result.current.chartDaily).toEqual([]);
  });

  it("loads data successfully", async () => {
    const { result } = renderHook(() => useBundle());
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.error).toBeNull();
    expect(result.current.chartDaily).toHaveLength(3);
    expect(result.current.defaultEndIndex).toBe(2);
    expect(result.current.defaultStartIndex).toBe(0);
    expect(result.current.snapshot).not.toBeNull();
    expect(result.current.snapshot!.date).toBe("2026-01-06");
    expect(result.current.allocation).not.toBeNull();
    expect(result.current.allocation!.total).toBe(102000);
  });

  it("sets error on HTTP failure", async () => {
    server.use(
      http.get(TIMELINE_URL, () => new HttpResponse(null, { status: 500, statusText: "Internal Server Error" })),
    );
    const { result } = renderHook(() => useBundle());
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.error).toBe("500 Internal Server Error");
    expect(result.current.chartDaily).toEqual([]);
    expect(result.current.snapshot).toBeNull();
  });

  it("sets error on network failure", async () => {
    server.use(
      http.get(TIMELINE_URL, () => HttpResponse.error()),
    );
    const { result } = renderHook(() => useBundle());
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.error).toBeTruthy();
    expect(result.current.chartDaily).toEqual([]);
  });

  it("sets error on invalid schema", async () => {
    server.use(
      http.get(TIMELINE_URL, () => HttpResponse.json({ daily: "not-an-array" })),
    );
    const { result } = renderHook(() => useBundle());
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.error).toBe("Invalid timeline data");
    expect(result.current.chartDaily).toEqual([]);
  });

  it("updates snapshot on brush change", async () => {
    const { result } = renderHook(() => useBundle());
    await waitFor(() => expect(result.current.loading).toBe(false));

    // Initially snapshot is last point
    expect(result.current.snapshot!.date).toBe("2026-01-06");

    // Move brush to first point
    act(() => result.current.onBrushChange({ startIndex: 0, endIndex: 0 }));

    await waitFor(() => expect(result.current.snapshot!.date).toBe("2026-01-02"));
    expect(result.current.allocation!.total).toBe(100000);
  });

  it("computes cashflow and activity for brush range", async () => {
    server.use(
      http.get(TIMELINE_URL, () => HttpResponse.json({
        ...VALID_PAYLOAD,
        qianjiTxns: [
          { date: "2026-01-02", type: "income", category: "Salary", amount: 5000 },
          { date: "2026-01-03", type: "expense", category: "Rent", amount: 2000 },
        ],
        fidelityTxns: [
          { runDate: "2026-01-03", actionType: "buy", symbol: "VTI", amount: -1000 },
        ],
      })),
    );
    const { result } = renderHook(() => useBundle());
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.cashflow).not.toBeNull();
    expect(result.current.cashflow!.totalIncome).toBe(5000);
    expect(result.current.cashflow!.totalExpenses).toBe(2000);
    expect(result.current.activity).not.toBeNull();
    expect(result.current.activity!.buysBySymbol).toHaveLength(1);
    expect(result.current.activity!.buysBySymbol[0].symbol).toBe("VTI");
  });

  it("computes monthlyFlows from qianjiTxns", async () => {
    server.use(
      http.get(TIMELINE_URL, () => HttpResponse.json({
        ...VALID_PAYLOAD,
        qianjiTxns: [
          { date: "2026-01-02", type: "income", category: "Salary", amount: 5000 },
          { date: "2026-01-03", type: "expense", category: "Rent", amount: 2000 },
        ],
      })),
    );
    const { result } = renderHook(() => useBundle());
    await waitFor(() => expect(result.current.loading).toBe(false));

    expect(result.current.monthlyFlows).toHaveLength(1);
    expect(result.current.monthlyFlows[0].month).toBe("2026-01");
    expect(result.current.monthlyFlows[0].income).toBe(5000);
    expect(result.current.monthlyFlows[0].expenses).toBe(2000);
  });
});
