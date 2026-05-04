// @vitest-environment jsdom

import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { render, screen, fireEvent, act } from "@testing-library/react";
import { ThemeToggle } from "./theme-toggle";

describe("ThemeToggle", () => {
  const store = new Map<string, string>();

  beforeEach(() => {
    document.documentElement.classList.remove("dark");
    store.clear();
    vi.stubGlobal("localStorage", {
      getItem: (k: string) => (store.has(k) ? store.get(k)! : null),
      setItem: (k: string, v: string) => { store.set(k, v); },
      removeItem: (k: string) => { store.delete(k); },
      clear: () => store.clear(),
      key: () => null,
      length: 0,
    });
    vi.stubGlobal("matchMedia", (q: string) => ({
      matches: false,
      media: q,
    }));
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders the moon icon when no stored theme (light mode default)", () => {
    render(<ThemeToggle />);
    const btn = screen.getByRole("button", { name: /switch to dark mode/i });
    expect(btn).toBeTruthy();
  });

  it("reads stored 'dark' theme on mount and shows the sun icon", () => {
    store.set("theme", "dark");
    render(<ThemeToggle />);
    const btn = screen.getByRole("button", { name: /switch to light mode/i });
    expect(btn).toBeTruthy();
  });

  it("toggles the 'dark' class on <html> and persists the new theme", () => {
    render(<ThemeToggle />);
    const btn = screen.getByRole("button");
    fireEvent.click(btn);
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    expect(store.get("theme")).toBe("dark");

    fireEvent.click(btn);
    expect(document.documentElement.classList.contains("dark")).toBe(false);
    expect(store.get("theme")).toBe("light");
  });

  it("updates the aria-label after toggle via the theme-change event", () => {
    render(<ThemeToggle />);
    const btn = screen.getByRole("button");
    expect(btn.getAttribute("aria-label")).toMatch(/dark mode/);
    fireEvent.click(btn);
    expect(btn.getAttribute("aria-label")).toMatch(/light mode/);
  });

  it("reacts to an external theme-change event (html class mutated elsewhere)", () => {
    render(<ThemeToggle />);
    expect(screen.getByRole("button").getAttribute("aria-label")).toMatch(/dark mode/);

    act(() => {
      document.documentElement.classList.add("dark");
      store.set("theme", "dark");
      window.dispatchEvent(new Event("theme-change"));
    });
    expect(screen.getByRole("button").getAttribute("aria-label")).toMatch(/light mode/);
  });
});
