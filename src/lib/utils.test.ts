import { describe, it, expect } from "vitest";
import { cn } from "./utils";

describe("cn", () => {
  it("joins multiple class strings", () => {
    expect(cn("foo", "bar")).toBe("foo bar");
  });

  it("filters falsy values (null/undefined/false)", () => {
    expect(cn("foo", null, undefined, false, "bar")).toBe("foo bar");
  });

  it("supports conditional object syntax (clsx)", () => {
    expect(cn("base", { active: true, disabled: false })).toBe("base active");
  });

  it("supports arrays", () => {
    expect(cn(["foo", "bar"], "baz")).toBe("foo bar baz");
  });

  it("lets later tailwind classes override earlier ones (twMerge)", () => {
    // px-4 should be dropped in favor of px-6
    expect(cn("px-4 py-2", "px-6")).toBe("py-2 px-6");
  });

  it("resolves conflicting color utilities to the last one", () => {
    expect(cn("text-red-500", "text-blue-500")).toBe("text-blue-500");
  });

  it("returns empty string for no args", () => {
    expect(cn()).toBe("");
  });
});
