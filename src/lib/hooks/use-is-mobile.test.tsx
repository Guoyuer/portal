// @vitest-environment jsdom

import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { useIsMobile } from "@/lib/hooks/use-is-mobile";

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
