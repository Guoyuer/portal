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

/**
 * Short status message rendered inside a section. Use `kind="unavailable"`
 * (red) when data failed to load or is missing; `kind="empty"` (muted)
 * when the shape is valid but the window contains nothing to show.
 * The `wrap` prop toggles the liquid-glass SectionBody — pass `wrap={false}`
 * when the caller already provides its own container.
 */
export function SectionMessage({
  kind,
  children,
  wrap = true,
  ...rest
}: {
  kind: "unavailable" | "empty";
  children: React.ReactNode;
  wrap?: boolean;
} & Omit<React.HTMLAttributes<HTMLParagraphElement>, "children">) {
  const className = `text-sm ${kind === "unavailable" ? "text-red-400" : "text-muted-foreground"}`;
  const message = <p className={className} {...rest}>{children}</p>;
  return wrap ? <SectionBody>{message}</SectionBody> : message;
}
