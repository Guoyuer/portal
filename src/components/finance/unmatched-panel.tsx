import type { UnmatchedItem } from "@/lib/compute/compute";

const LABELS: Record<UnmatchedItem["source"], string> = {
  fidelity:  "Fidelity",
  robinhood: "Robinhood",
};

export function UnmatchedPanel({ items }: { items: UnmatchedItem[] }) {
  if (items.length === 0) return null;

  const grouped = new Map<UnmatchedItem["source"], UnmatchedItem[]>();
  for (const it of items) {
    const list = grouped.get(it.source) ?? [];
    list.push(it);
    grouped.set(it.source, list);
  }

  return (
    <div className="mt-3 p-3 rounded border border-red-400/30 bg-red-950/20 text-sm">
      {[...grouped.entries()].map(([src, list]) => (
        <div key={src} className="mb-2 last:mb-0">
          <div className="font-medium text-red-300 mb-1">
            {`${LABELS[src]} (${list.length}):`}
          </div>
          <ul className="pl-4 space-y-0.5 text-muted-foreground font-mono text-xs">
            {list.map((it, i) => (
              <li key={`${it.source}-${it.date}-${i}`}>
                {it.date}  ${it.amount.toFixed(2)}
              </li>
            ))}
          </ul>
        </div>
      ))}
    </div>
  );
}
