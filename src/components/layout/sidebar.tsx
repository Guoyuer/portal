"use client";

import { useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import ThemeToggle from "@/components/layout/theme-toggle";

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
    label: "Mail",
    href: "/mail",
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
        <rect x="2" y="4" width="20" height="16" rx="2" />
        <path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7" />
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
        return (
          <Link
            key={item.href}
            href={item.comingSoon ? "#" : item.href}
            onClick={() => setOpen(false)}
            className={`flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors ${
              active
                ? "bg-white/12 text-foreground shadow-[inset_0_0.5px_0_rgba(255,255,255,0.25),inset_0_0_8px_rgba(255,255,255,0.06)] backdrop-blur-sm rounded-xl"
                : item.comingSoon
                  ? "cursor-default text-muted-foreground"
                  : "text-muted-foreground hover:bg-white/8 hover:text-foreground hover:shadow-[inset_0_0.5px_0_rgba(255,255,255,0.15),inset_0_0_6px_rgba(255,255,255,0.04)] hover:backdrop-blur-sm rounded-xl transition-all duration-200"
            }`}
          >
            {item.icon}
            {item.label}
            {item.comingSoon && (
              <span className="ml-auto rounded-md bg-white/8 backdrop-blur-sm px-1.5 py-0.5 text-[10px] text-muted-foreground/60 border border-white/5">
                soon
              </span>
            )}
          </Link>
        );
      })}
    </nav>
  );

  return (
    <>
      {/* Desktop sidebar */}
      <aside className="hidden md:flex md:w-56 md:flex-col md:fixed md:inset-y-0 md:z-40 liquid-glass-sidebar">
        <div className="flex h-14 items-center px-6">
          <span className="text-lg font-semibold text-white tracking-tight">
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
        className="fixed top-3 left-3 z-50 rounded-lg liquid-glass-pill p-2 text-foreground md:hidden"
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
          className="fixed inset-0 z-40 bg-black/50 md:hidden"
          onClick={() => setOpen(false)}
        />
      )}

      {/* Mobile drawer */}
      <aside
        className={`fixed inset-y-0 left-0 z-40 w-56 liquid-glass-sidebar transition-transform duration-200 md:hidden ${
          open ? "translate-x-0" : "-translate-x-full"
        }`}
      >
        <div className="flex h-14 items-center pl-14 pr-6">
          <span className="text-lg font-semibold text-white tracking-tight">
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
