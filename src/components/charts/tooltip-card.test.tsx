// @vitest-environment jsdom

import { describe, it, expect, afterEach } from "vitest";
import { render, screen, cleanup } from "@testing-library/react";
import { TooltipCard, TooltipRow } from "./tooltip-card";

afterEach(cleanup);

describe("TooltipCard", () => {
  it("returns null when inactive (Recharts convention)", () => {
    const { container } = render(
      <TooltipCard active={false} payload={[]}>
        <p>hidden</p>
      </TooltipCard>,
    );
    expect(container.textContent).toBe("");
  });

  it("returns null when payload is empty", () => {
    const { container } = render(
      <TooltipCard active={true} payload={[]}>
        <p>hidden</p>
      </TooltipCard>,
    );
    expect(container.textContent).toBe("");
  });

  it("renders a bold title when provided", () => {
    render(
      <TooltipCard active={true} payload={[{ value: 1 } as never]} title="Apr 15, 2026">
        <p>row</p>
      </TooltipCard>,
    );
    const title = screen.getByText("Apr 15, 2026");
    expect(title.tagName).toBe("P");
    expect((title as HTMLElement).style.fontWeight).toBe("600");
  });

  it("supports a render-prop child that receives isDark", () => {
    render(
      <TooltipCard active={true} payload={[{ value: 1 } as never]}>
        {(isDark) => <p data-testid="row">{String(isDark)}</p>}
      </TooltipCard>,
    );
    // getIsDark defaults to false in jsdom (no dark class on html)
    expect(screen.getByTestId("row").textContent).toBe("false");
  });

  it("renders the liquid-glass container style (position-agnostic marker)", () => {
    const { container } = render(
      <TooltipCard active={true} payload={[{ value: 1 } as never]}>
        <p>row</p>
      </TooltipCard>,
    );
    const shell = container.firstElementChild as HTMLElement;
    expect(shell.style.backdropFilter).toContain("blur");
  });
});

describe("TooltipRow", () => {
  it("renders label : value with optional color", () => {
    const { container } = render(<TooltipRow label="Income" value="$1,000" color="#00aa00" />);
    expect(container.textContent).toBe("Income: $1,000");
    expect((container.firstElementChild as HTMLElement).style.color).toBeTruthy();
  });

  it("omits color attribute when none given", () => {
    const { container } = render(<TooltipRow label="Close" value="$100.40" />);
    expect(container.textContent).toBe("Close: $100.40");
  });
});
