"use client";

import { useEffect, useState } from "react";
import { SCROLL_SHOW_THRESHOLD } from "@/lib/style-helpers";

export function BackToTop() {
  const [show, setShow] = useState(false);

  useEffect(() => {
    const onScroll = () => setShow(window.scrollY > SCROLL_SHOW_THRESHOLD);
    window.addEventListener("scroll", onScroll);
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  if (!show) return null;

  return (
    <button
      onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })}
      className="fixed bottom-6 right-6 z-50 rounded-full bg-slate-900 dark:bg-slate-100 text-white dark:text-slate-900 w-10 h-10 flex items-center justify-center shadow-lg hover:opacity-80 transition-opacity"
      aria-label="Back to top"
    >
      ↑
    </button>
  );
}
