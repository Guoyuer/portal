"use client";

// ── Shared full-screen chart dialog shell ─────────────────────────────
// Both TickerChartDialog and GroupChartDialog render the same near-full-
// screen resizable <dialog> with body-scroll lock, backdrop-click-close,
// Esc (native), and header close button. Only the header text and inner
// content differ, so the shell lives here and each dialog plugs in its
// own header + children.

import { useEffect, useRef, type ReactNode } from "react";
import { useIsDark } from "@/lib/hooks/hooks";

export function ChartDialog({
  header,
  onClose,
  children,
}: {
  header: ReactNode;
  onClose: () => void;
  children: ReactNode;
}) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const isDark = useIsDark();

  useEffect(() => {
    const el = dialogRef.current;
    if (!el) return;
    el.showModal();
    const onCancel = (e: Event) => { e.preventDefault(); onClose(); };
    el.addEventListener("cancel", onCancel);
    // <dialog> modal doesn't block wheel propagation; lock body scroll manually.
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      el.removeEventListener("cancel", onCancel);
      document.body.style.overflow = prevOverflow;
    };
  }, [onClose]);

  return (
    <dialog
      ref={dialogRef}
      onClick={(e) => {
        e.stopPropagation();
        if (e.target === dialogRef.current) onClose();
      }}
      className="fixed inset-0 m-auto backdrop:bg-black/50 backdrop:backdrop-blur-sm bg-transparent p-0 max-w-none max-h-none border-0 overflow-visible"
    >
      <div className={`${isDark ? "bg-zinc-900 text-zinc-100" : "bg-white text-zinc-900"} rounded-xl shadow-2xl flex flex-col resize overflow-hidden w-[95vw] h-[92vh] min-w-[400px] min-h-[300px] max-w-[99vw] max-h-[98vh]`}>
        <div className="shrink-0 flex items-center justify-between px-5 py-3 border-b border-foreground/10">
          {header}
          <button
            onClick={onClose}
            aria-label="Close"
            className={`w-8 h-8 flex items-center justify-center rounded-full text-2xl leading-none ${isDark ? "hover:bg-zinc-800 text-zinc-300 hover:text-zinc-50" : "hover:bg-zinc-100 text-zinc-500 hover:text-zinc-900"} transition-colors`}
          >
            &times;
          </button>
        </div>
        {children}
      </div>
    </dialog>
  );
}
