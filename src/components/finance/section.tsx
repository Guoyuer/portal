// ── Finance section layout primitives ────────────────────────────────────

export function SectionHeader({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-foreground font-semibold text-lg tracking-tight mb-3">
      {children}
    </div>
  );
}

export function SectionBody({ children }: { children: React.ReactNode }) {
  return (
    <div className="liquid-glass p-3 sm:p-5">{children}</div>
  );
}
