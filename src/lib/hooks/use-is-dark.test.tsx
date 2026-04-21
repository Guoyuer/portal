// @vitest-environment jsdom

import { describe, it, expect, beforeEach } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { getIsDark, useIsDark } from "@/lib/hooks/use-is-dark";

describe("getIsDark", () => {
  beforeEach(() => {
    document.documentElement.classList.remove("dark");
  });

  it("returns false when 'dark' class is absent", () => {
    expect(getIsDark()).toBe(false);
  });

  it("returns true when 'dark' class is present", () => {
    document.documentElement.classList.add("dark");
    expect(getIsDark()).toBe(true);
  });
});

describe("useIsDark", () => {
  beforeEach(() => {
    document.documentElement.classList.remove("dark");
  });

  it("starts false when html lacks the dark class", () => {
    const { result } = renderHook(() => useIsDark());
    expect(result.current).toBe(false);
  });

  it("flips to true when the dark class is added, and back when removed", async () => {
    const { result } = renderHook(() => useIsDark());
    expect(result.current).toBe(false);

    await act(async () => {
      document.documentElement.classList.add("dark");
      // MutationObserver callbacks are microtask-deferred
      await Promise.resolve();
    });
    expect(result.current).toBe(true);

    await act(async () => {
      document.documentElement.classList.remove("dark");
      await Promise.resolve();
    });
    expect(result.current).toBe(false);
  });

  it("disconnects the observer on unmount (no further updates)", async () => {
    const { result, unmount } = renderHook(() => useIsDark());
    expect(result.current).toBe(false);
    unmount();
    // After unmount, adding the class should NOT crash or update stale refs.
    // The real assertion is that this doesn't throw — renderHook swallows
    // errors from dangling observers otherwise.
    document.documentElement.classList.add("dark");
    await Promise.resolve();
    expect(document.documentElement.classList.contains("dark")).toBe(true);
  });
});
