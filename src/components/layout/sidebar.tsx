"use client";

import { useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { ThemeToggle } from "@/components/layout/theme-toggle";

const financeSections = [
  { label: "Overview", hash: "#timemachine" },
  { label: "Fidelity", hash: "#fidelity-activity" },
  { label: "Cash Flow", hash: "#cashflow" },
  { label: "Market", hash: "#market" },
];

const navItems = [
  {
    label: "Finance",
    href: "/finance",
    comingSoon: false,
    icon: (
      <svg
        xmlns="http://www.w3.org/2000/svg"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth={2}
        strokeLinecap="round"
        strokeLinejoin="round"
        className="h-5 w-5"
      >
        <path d="M3 3v18h18" />
        <path d="M18 17V9" />
        <path d="M13 17V5" />
        <path d="M8 17v-3" />
      </svg>
    ),
  },
  {
    label: "News",
    href: "/news",
    comingSoon: true,
    icon: (
      <svg
        xmlns="http://www.w3.org/2000/svg"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth={2}
        strokeLinecap="round"
        strokeLinejoin="round"
        className="h-5 w-5"
      >
        <path d="M4 22h16a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2H8a2 2 0 0 0-2 2v16a2 2 0 0 1-2 2Zm0 0a2 2 0 0 1-2-2v-9c0-1.1.9-2 2-2h2" />
        <path d="M18 14h-8" />
        <path d="M15 18h-5" />
        <path d="M10 6h8v4h-8V6Z" />
      </svg>
    ),
  },
  {
    label: "Economy",
    href: "/econ",
    comingSoon: false,
    icon: (
      <svg
        xmlns="http://www.w3.org/2000/svg"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth={2}
        strokeLinecap="round"
        strokeLinejoin="round"
        className="h-5 w-5"
      >
        <polyline points="22 7 13.5 15.5 8.5 10.5 2 17" />
        <polyline points="16 7 22 7 22 13" />
      </svg>
    ),
  },
];

export default function Sidebar() {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);

  const nav = (
    <nav className="flex flex-col gap-1 px-3">
      {navItems.map((item) => {
        const active = pathname.startsWith(item.href);
        const showSubSections = active && item.href === "/finance";
        return (
          <div key={item.href}>
            <Link
              href={item.comingSoon ? "#" : item.href}
              onClick={() => setOpen(false)}
              className={`flex items-center gap-3 rounded-xl px-3 py-2 text-sm transition-all duration-200 ${
                active
                  ? "bg-black/8 dark:bg-white/12 font-semibold border border-black/10 dark:border-white/20 backdrop-blur-sm"
                  : item.comingSoon
                    ? "cursor-default text-current/40"
                    : "text-current/70 hover:text-current hover:bg-white/10"
              }`}
            >
              {item.icon}
              {item.label}
              {item.comingSoon && (
                <span className="ml-auto rounded-md bg-white/10 px-1.5 py-0.5 text-[10px] border border-white/10">
                  soon
                </span>
              )}
            </Link>
            {showSubSections && (
              <div className="mt-0.5 ml-8 flex flex-col gap-0.5">
                {financeSections.map((s) => (
                  <a
                    key={s.hash}
                    href={s.hash}
                    onClick={() => setOpen(false)}
                    className="rounded-lg px-3 py-1 text-xs text-current/60 hover:text-current hover:bg-white/10 transition-colors"
                  >
                    {s.label}
                  </a>
                ))}
              </div>
            )}
          </div>
        );
      })}
    </nav>
  );

  return (
    <>
      {/* Desktop sidebar */}
      <aside className="hidden md:flex md:w-56 md:flex-col md:fixed md:inset-y-0 md:z-40 liquid-glass-sidebar">
        <div className="flex h-14 items-center px-6">
          <span className="text-lg font-semibold tracking-tight">
            Portal
          </span>
        </div>
        {nav}
        <div className="mt-auto px-3 pb-4">
          <ThemeToggle />
        </div>
      </aside>

      {/* Mobile hamburger button */}
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="fixed top-3 left-3 z-50 rounded-full bg-white/20 dark:bg-white/10 backdrop-blur-md border border-white/40 dark:border-white/20 p-2 md:hidden"
        aria-label="Toggle navigation"
      >
        <svg
          xmlns="http://www.w3.org/2000/svg"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={2}
          strokeLinecap="round"
          strokeLinejoin="round"
          className="h-5 w-5"
        >
          {open ? (
            <>
              <path d="M18 6 6 18" />
              <path d="m6 6 12 12" />
            </>
          ) : (
            <>
              <path d="M4 6h16" />
              <path d="M4 12h16" />
              <path d="M4 18h16" />
            </>
          )}
        </svg>
      </button>

      {/* Mobile overlay */}
      {open && (
        <div
          className="fixed inset-0 z-40 bg-black/30 md:hidden"
          onClick={() => setOpen(false)}
        />
      )}

      {/* Mobile drawer */}
      <aside
        className={`fixed inset-y-0 left-0 z-40 w-48 liquid-glass-sidebar transition-transform duration-200 md:hidden ${
          open ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <div className="flex h-14 items-center pl-14 pr-6">
          <span className="text-lg font-semibold tracking-tight">
            Portal
          </span>
        </div>
        {nav}
        <div className="mt-auto px-3 pb-4">
          <ThemeToggle />
        </div>
      </aside>
    </>
  );
}
