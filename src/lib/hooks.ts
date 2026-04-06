"use client";

import { useEffect, useRef, useState } from "react";

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

/** Track which section ID is currently in the viewport. */
export function useActiveSection(ids: string[], ready = true) {
  const [active, setActive] = useState("");
  const manualRef = useRef(false);

  useEffect(() => {
    if (!ready) return;

    const visibleSet = new Set<string>();
    const observer = new IntersectionObserver(
      (entries) => {
        if (manualRef.current) return;
        for (const entry of entries) {
          if (entry.isIntersecting) visibleSet.add(entry.target.id);
          else visibleSet.delete(entry.target.id);
        }
        // Pick the first visible section in DOM order
        const top = ids.find((id) => visibleSet.has(id));
        if (top) setActive(top);
      },
      { rootMargin: "0px 0px -50% 0px", threshold: 0 },
    );

    for (const id of ids) {
      const el = document.getElementById(id);
      if (el) observer.observe(el);
    }
    return () => observer.disconnect();
  }, [ids, ready]);

  const scrollTo = (id: string) => {
    manualRef.current = true;
    setActive(id);
    document.getElementById(id)?.scrollIntoView({ behavior: "smooth", block: "start" });
    setTimeout(() => { manualRef.current = false; }, 1000);
  };

  return { active, scrollTo };
}

export function useIsMobile(breakpoint = 640) {
  const [isMobile, setIsMobile] = useState(false);
  useEffect(() => {
    const check = () => setIsMobile(window.innerWidth < breakpoint);
    check();
    window.addEventListener("resize", check);
    return () => window.removeEventListener("resize", check);
  }, [breakpoint]);
  return isMobile;
}
