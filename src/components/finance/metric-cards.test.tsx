// @vitest-environment jsdom

import { describe, it, expect, vi, beforeAll } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { afterEach } from "vitest";
import { MetricCards } from "./metric-cards";
import type { ApiCategory, ApiTicker } from "@/lib/computed-types";

// ── Mock ResizeObserver (not available in jsdom) ────────────────────────

beforeAll(() => {
  global.ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
});

afterEach(cleanup);

// ── Helpers ─────────────────────────────────────────────────────────────

const CATEGORIES: ApiCategory[] = [
  { name: "US Equity", value: 55000, pct: 55, target: 55, deviation: 0 },
  { name: "Non-US Equity", value: 15000, pct: 15, target: 15, deviation: 0 },
  { name: "Crypto", value: 3000, pct: 3, target: 3, deviation: 0 },
  { name: "Safe Net", value: 27000, pct: 27, target: 27, deviation: 0 },
];

const ALLOCATION = {
  total: 100000,
  netWorth: 95000,
  categories: CATEGORIES,
  tickers: [] as ApiTicker[],
};

const COLOR_BY_NAME: Record<string, string> = {
  "US Equity": "#0072B2",
  "Non-US Equity": "#009E73",
  "Crypto": "#E69F00",
  "Safe Net": "#56B4E9",
};

const BASE_PROPS = {
  allocation: ALLOCATION,
  savingsRate: 42 as number | null,
  takehomeSavingsRate: 35 as number | null,
  goal: 2_000_000,
  allocationOpen: false,
  onAllocationToggle: vi.fn(),
  colorByName: COLOR_BY_NAME,
};

// ── Tests ───────────────────────────────────────────────────────────────

describe("MetricCards", () => {
  it("renders net worth, savings rate, and goal", () => {
    render(<MetricCards {...BASE_PROPS} />);
    expect(screen.getByText("Net Worth")).toBeTruthy();
    expect(screen.getByText("$95,000")).toBeTruthy();
    expect(screen.getByText("Savings Rate")).toBeTruthy();
    expect(screen.getByText("Goal")).toBeTruthy();
    expect(screen.getByText("5%")).toBeTruthy();
  });

  it("shows N/A when savings rates are null", () => {
    render(<MetricCards {...BASE_PROPS} savingsRate={null} takehomeSavingsRate={null} />);
    const nas = screen.getAllByText("N/A");
    expect(nas.length).toBe(1);
  });

  it("calls onAllocationToggle when net worth tile is clicked", () => {
    const toggle = vi.fn();
    render(<MetricCards {...BASE_PROPS} onAllocationToggle={toggle} />);
    fireEvent.click(screen.getByText("Net Worth").closest("button")!);
    expect(toggle).toHaveBeenCalledOnce();
  });

  it("displays safe net and investment breakdown", () => {
    render(<MetricCards {...BASE_PROPS} />);
    expect(screen.getByText("$27k")).toBeTruthy();
    expect(screen.getByText("$73k")).toBeTruthy();
  });

  it("shows take-home savings rate as primary and exposes gross via tooltip", () => {
    render(<MetricCards {...BASE_PROPS} />);
    expect(screen.getByText("35%")).toBeTruthy();
    expect(screen.getAllByText(/take-home/).length).toBeGreaterThanOrEqual(1);
    // Gross (42%) only appears in the SVG <title> tooltip, not as visible text
    const tooltip = document.querySelector("title");
    expect(tooltip?.textContent).toContain("42%");
    expect(tooltip?.textContent).toContain("35%");
  });
});
