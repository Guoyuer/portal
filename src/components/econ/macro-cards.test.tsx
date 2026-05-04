// @vitest-environment jsdom

import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { MacroCards } from "./macro-cards";
import type { EconSnapshot } from "@/lib/schemas/econ";

describe("MacroCards", () => {
  it("renders only the indicators present in the snapshot", () => {
    const snap: EconSnapshot = { fedFundsRate: 4.5, cpiYoy: 3.1 };
    render(<MacroCards snapshot={snap} />);
    expect(screen.getByText("Fed Rate")).toBeTruthy();
    expect(screen.getByText("CPI (YoY)")).toBeTruthy();
    expect(screen.queryByText("VIX")).toBeNull();
    expect(screen.queryByText("Unemployment")).toBeNull();
  });

  it("renders nothing when snapshot is entirely empty", () => {
    const { container } = render(<MacroCards snapshot={{}} />);
    // The grid wrapper still exists but contains no indicator cards
    const cards = container.querySelectorAll(".liquid-glass-thin");
    expect(cards.length).toBe(0);
  });

  it("formats percentages with 2 decimals for rates", () => {
    render(<MacroCards snapshot={{ fedFundsRate: 4.333, treasury10y: 4.27 }} />);
    expect(screen.getByText("4.33%")).toBeTruthy();
    expect(screen.getByText("4.27%")).toBeTruthy();
  });

  it("renders 2s10s spread as signed bps", () => {
    render(<MacroCards snapshot={{ spread2s10s: 0.15 }} />);
    // 0.15 * 100 = 15, sign + for non-negative
    expect(screen.getByText("+15 bps")).toBeTruthy();
  });

  it("renders oil with dollar sign and no decimal", () => {
    render(<MacroCards snapshot={{ oilWti: 82.7 }} />);
    expect(screen.getByText("$83")).toBeTruthy();
  });

  it("renders USD/CNY with 4 decimals", () => {
    render(<MacroCards snapshot={{ usdCny: 7.2345 }} />);
    expect(screen.getByText("7.2345")).toBeTruthy();
  });

  it("distinguishes 0 from null (0 renders, undefined does not)", () => {
    render(<MacroCards snapshot={{ spread2s10s: 0 }} />);
    // 0 → "+0 bps" (non-negative branch)
    expect(screen.getByText("+0 bps")).toBeTruthy();
  });
});
