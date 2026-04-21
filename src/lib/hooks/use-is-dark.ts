"use client";

import { useEffect, useState } from "react";

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
