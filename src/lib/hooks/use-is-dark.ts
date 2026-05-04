"use client";

import { useSyncExternalStore } from "react";

/** Non-hook dark mode check for use outside React components (tooltip render functions). */
export function getIsDark(): boolean {
  return typeof document !== "undefined" && document.documentElement.classList.contains("dark");
}

function subscribeDark(callback: () => void) {
  const observer = new MutationObserver(callback);
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
  return () => observer.disconnect();
}

export function useIsDark() {
  return useSyncExternalStore(subscribeDark, getIsDark, () => false);
}
