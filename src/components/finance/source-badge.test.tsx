// @vitest-environment jsdom

import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { SourceBadge } from "./source-badge";

describe("SourceBadge", () => {
  it("renders 'FID' for fidelity", () => {
    render(<SourceBadge source="fidelity" />);
    expect(screen.getByText("FID")).toBeTruthy();
  });
  it("renders 'RH' for robinhood", () => {
    render(<SourceBadge source="robinhood" />);
    expect(screen.getByText("RH")).toBeTruthy();
  });
  it("renders '401k' for 401k", () => {
    render(<SourceBadge source="401k" />);
    expect(screen.getByText("401k")).toBeTruthy();
  });
  it("sets aria-label reflecting the source", () => {
    render(<SourceBadge source="fidelity" />);
    expect(screen.getByLabelText("source: fidelity")).toBeTruthy();
  });
});
