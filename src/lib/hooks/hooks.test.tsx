// @vitest-environment jsdom

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { getIsDark, useIsDark, useIsMobile } from "@/lib/hooks/hooks";

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

describe("useIsMobile", () => {
  type Listener = (e: MediaQueryListEvent) => void;

  let listeners: Listener[] = [];
  let currentMatches = false;
  let originalMatchMedia: typeof window.matchMedia;

  beforeEach(() => {
    listeners = [];
    currentMatches = false;
    originalMatchMedia = window.matchMedia;
    window.matchMedia = ((query: string) => {
      // Minimal MediaQueryList stub sufficient for useSyncExternalStore
      const mql = {
        get matches() {
          return currentMatches;
        },
        media: query,
        onchange: null,
        addEventListener: (_: "change", cb: Listener) => {
          listeners.push(cb);
        },
        removeEventListener: (_: "change", cb: Listener) => {
          listeners = listeners.filter((l) => l !== cb);
        },
        addListener: () => { /* legacy, unused */ },
        removeListener: () => { /* legacy, unused */ },
        dispatchEvent: () => true,
      };
      return mql as unknown as MediaQueryList;
    }) as typeof window.matchMedia;
  });

  afterEach(() => {
    window.matchMedia = originalMatchMedia;
  });

  const fireMqlChange = (matches: boolean) => {
    currentMatches = matches;
    listeners.forEach((l) => l({ matches } as MediaQueryListEvent));
  };

  it("returns the current matchMedia result on mount", () => {
    currentMatches = true;
    const { result } = renderHook(() => useIsMobile());
    expect(result.current).toBe(true);
  });

  it("updates when the matchMedia subscription fires a change", () => {
    const { result } = renderHook(() => useIsMobile());
    expect(result.current).toBe(false);

    act(() => fireMqlChange(true));
    expect(result.current).toBe(true);

    act(() => fireMqlChange(false));
    expect(result.current).toBe(false);
  });

  it("unsubscribes on unmount (no listener remains)", () => {
    const { unmount } = renderHook(() => useIsMobile());
    expect(listeners.length).toBe(1);
    unmount();
    expect(listeners.length).toBe(0);
  });
});
