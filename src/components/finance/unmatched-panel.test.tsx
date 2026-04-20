// @vitest-environment jsdom

import { describe, it, expect, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { UnmatchedPanel } from "./unmatched-panel";
import type { UnmatchedItem } from "@/lib/compute/compute";

describe("UnmatchedPanel", () => {
  afterEach(() => {
    cleanup();
  });

  it("groups items by source with per-group count", () => {
    const items: UnmatchedItem[] = [
      { source: "fidelity",  date: "2024-10-01", amount: 500 },
      { source: "fidelity",  date: "2024-11-15", amount: 400 },
      { source: "robinhood", date: "2024-12-10", amount: 500 },
    ];
    render(<UnmatchedPanel items={items} />);
    expect(screen.getByText(/Fidelity \(2\)/)).toBeTruthy();
    expect(screen.getByText(/Robinhood \(1\)/)).toBeTruthy();
    expect(screen.getByText(/2024-10-01/)).toBeTruthy();
    expect(screen.getAllByText(/500\.00/).length).toBeGreaterThan(0);
  });

  it("renders nothing when items is empty", () => {
    const { container } = render(<UnmatchedPanel items={[]} />);
    expect(container.firstChild).toBeNull();
  });
});
