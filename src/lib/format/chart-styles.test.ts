import { describe, it, expect } from "vitest";
import { tooltipStyle, gridStroke, axisProps, brushColors } from "@/lib/format/chart-styles";

describe("tooltipStyle", () => {
  it("uses a dark background for dark mode", () => {
    const s = tooltipStyle(true);
    expect(s.backgroundColor).toMatch(/^rgba\(8/);
  });

  it("uses a white background for light mode", () => {
    const s = tooltipStyle(false);
    expect(s.backgroundColor).toMatch(/^rgba\(255/);
  });

  it("always sets the liquid-glass backdrop filter", () => {
    for (const dark of [true, false]) {
      const s = tooltipStyle(dark);
      expect(s.backdropFilter).toContain("blur");
      expect(s.WebkitBackdropFilter).toContain("blur");
    }
  });

  it("produces a different boxShadow per theme", () => {
    expect(tooltipStyle(true).boxShadow).not.toBe(tooltipStyle(false).boxShadow);
  });
});

describe("gridStroke", () => {
  it("differs between light and dark", () => {
    expect(gridStroke(true)).not.toBe(gridStroke(false));
  });

  it("is a semi-transparent rgba", () => {
    expect(gridStroke(true)).toMatch(/^rgba\(/);
    expect(gridStroke(false)).toMatch(/^rgba\(/);
  });
});

describe("axisProps", () => {
  it("uses a lighter tick fill in dark mode than light mode", () => {
    expect(axisProps(true).tick.fill).toBe("#9ca3af");
    expect(axisProps(false).tick.fill).toBe("#6b7280");
  });

  it("ties axisLine stroke to gridStroke", () => {
    expect(axisProps(true).axisLine.stroke).toBe(gridStroke(true));
    expect(axisProps(false).axisLine.stroke).toBe(gridStroke(false));
  });

  it("disables tick lines on the axis", () => {
    expect(axisProps(true).tickLine).toBe(false);
  });
});

describe("brushColors", () => {
  it("swaps cyan tones between themes", () => {
    const dark = brushColors(true);
    const light = brushColors(false);
    expect(dark.stroke).not.toBe(light.stroke);
    expect(dark.fill).not.toBe(light.fill);
  });

  it("uses semi-transparent fill", () => {
    expect(brushColors(true).fill).toMatch(/^rgba\(/);
    expect(brushColors(false).fill).toMatch(/^rgba\(/);
  });
});
