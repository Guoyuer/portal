"use client";

import { useEffect, useState, useSyncExternalStore } from "react";

/** Non-hook dark mode check for use outside React components (tooltip render functions). */
export function getIsDark(): boolean {
  return typeof document !== "undefined" && document.documentElement.classList.contains("dark");
}

export function useIsDark() {
  const [isDark, setIsDark] = useState(false);
  useEffect(() => {
    const check = () => setIsDark(document.documentElement.classList.contains("dark"));
    check();
    const observer = new MutationObserver(check);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    return () => observer.disconnect();
  }, []);
  return isDark;
}

const MOBILE_MQL = "(max-width: 639px)";

function subscribeMobile(callback: () => void) {
  const mql = window.matchMedia(MOBILE_MQL);
  mql.addEventListener("change", callback);
  return () => mql.removeEventListener("change", callback);
}

function getMobileSnapshot() {
  return window.matchMedia(MOBILE_MQL).matches;
}

export function useIsMobile() {
  return useSyncExternalStore(subscribeMobile, getMobileSnapshot, () => false);
}
