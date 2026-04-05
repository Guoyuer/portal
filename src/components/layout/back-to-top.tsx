"use client";

import { useEffect, useState } from "react";
import { SCROLL_SHOW_THRESHOLD } from "@/lib/style-helpers";

export function BackToTop() {
  const [show, setShow] = useState(false);

  useEffect(() => {
    const onScroll = () => setShow(window.scrollY > SCROLL_SHOW_THRESHOLD);
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  if (!show) return null;

  return (
    <button
      onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })}
      className="fixed bottom-6 right-6 z-50 rounded-full liquid-glass-pill !rounded-full w-10 h-10 flex items-center justify-center text-foreground hover:-translate-y-1 transition-all"
      aria-label="Back to top"
    >
      ↑
    </button>
  );
}
