// @vitest-environment jsdom

import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

// TickerChart fetches /prices — stub so the test stays purely about table UX.
vi.mock("./ticker/ticker-chart", () => ({
  TickerChart: ({ symbol }: { symbol: string }) => <div data-testid={`chart-${symbol}`} />,
  TickerDialogOnly: () => null,
}));

import { TickerTable, DeviationCell } from "./ticker-table";

describe("TickerTable", () => {
  const mk = (n: number) =>
    Array.from({ length: n }, (_, i) => ({ ticker: `SYM${i + 1}`, count: i + 1, total: (i + 1) * 100 }));

  it("renders all rows when there are 5 or fewer symbols", () => {
    const { container } = render(<TickerTable title="Buys" data={mk(4)} />);
    expect(screen.queryByText(/and \d+ more/)).toBeNull();
    // 4 top rows, no "... and N more" row
    expect(container.querySelectorAll("tbody > tr.group").length).toBe(4);
  });

  it("slices to top 5 and shows '... and N more' for the rest", () => {
    render(<TickerTable title="Buys" data={mk(8)} />);
    expect(screen.getByText(/and 3 more/)).toBeTruthy();
    // The overflow summary amount: 3 rest rows × 600/700/800 = $2,100
    expect(screen.getByText(/\$2,100/)).toBeTruthy();
  });

  it("toggles chart expansion on row click (and clicking the same row collapses it)", () => {
    render(<TickerTable title="Buys" data={mk(2)} />);
    const row = screen.getByText("SYM1").closest("tr")!;
    expect(screen.queryByTestId("chart-SYM1")).toBeNull();

    fireEvent.click(row);
    expect(screen.getByTestId("chart-SYM1")).toBeTruthy();

    fireEvent.click(row);
    expect(screen.queryByTestId("chart-SYM1")).toBeNull();
  });

  it("clicking a different row collapses the previous one and expands the new row", () => {
    render(<TickerTable title="Buys" data={mk(3)} />);
    fireEvent.click(screen.getByText("SYM1").closest("tr")!);
    expect(screen.getByTestId("chart-SYM1")).toBeTruthy();

    fireEvent.click(screen.getByText("SYM2").closest("tr")!);
    expect(screen.queryByTestId("chart-SYM1")).toBeNull();
    expect(screen.getByTestId("chart-SYM2")).toBeTruthy();
  });

  it("renders an empty table when data is empty", () => {
    render(<TickerTable title="Buys" data={[]} />);
    expect(screen.queryByText(/and \d+ more/)).toBeNull();
    expect(screen.getByText("Buys")).toBeTruthy();
  });

  it("renders a group row with display name", () => {
    render(<TickerTable title="Test" data={[
      { ticker: "NASDAQ 100", count: 2, total: 3000, isGroup: true, groupKey: "nasdaq_100" },
      { ticker: "NVDA", count: 1, total: 500 },
    ]} />);
    expect(screen.getByText("NASDAQ 100")).toBeTruthy();
    expect(screen.getByText("NVDA")).toBeTruthy();
  });

  it("renders SourceBadge for each source on a row", () => {
    const data: import("@/lib/compute/compute").ActivityTicker[] = [
      { ticker: "S&P 500", count: 3, total: 1050, isGroup: true, sources: ["fidelity", "401k"], groupKey: "sp500" },
    ];
    render(<TickerTable title="Buys by Symbol" data={data} />);
    expect(screen.getByText("FID")).toBeTruthy();
    expect(screen.getByText("401k")).toBeTruthy();
  });
});

describe("DeviationCell", () => {
  it("uses a green class family for non-negative deviation", () => {
    const { container } = render(
      <table><tbody><tr><DeviationCell value={2.5} /></tr></tbody></table>,
    );
    const cell = container.querySelector("td")!;
    expect(cell.className).toContain("emerald");
    expect(cell.textContent).toBe("+2.5%");
  });

  it("uses a red class family for negative deviation", () => {
    const { container } = render(
      <table><tbody><tr><DeviationCell value={-3} /></tr></tbody></table>,
    );
    const cell = container.querySelector("td")!;
    expect(cell.className).toContain("red");
    expect(cell.textContent).toBe("-3.0%");
  });
});
