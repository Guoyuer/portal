"use client";

import { useCallback, useEffect, useState, useSyncExternalStore } from "react";

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

export function useIsMobile() {
  const subscribe = useCallback((callback: () => void) => {
    const mql = window.matchMedia("(max-width: 639px)");
    mql.addEventListener("change", callback);
    return () => mql.removeEventListener("change", callback);
  }, []);

  const getSnapshot = useCallback(
    () => window.matchMedia("(max-width: 639px)").matches,
    [],
  );

  return useSyncExternalStore(subscribe, getSnapshot, () => false);
}
