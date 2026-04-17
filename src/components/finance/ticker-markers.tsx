// ── SVG scatter markers for the ticker chart ────────────────────────────
//
// Two levels of marker:
// - Basic BuyMarker/SellMarker (inline chart, one per day, non-interactive)
// - Cluster markers (dialog chart, aggregated, hover/click-aware)
//
// Colors are the Okabe-Ito palette (see CLAUDE.md accessibility section) and
// always pair with a B/S letter so the signal isn't color-only.

import type { Cluster } from "@/lib/ticker-data";

const BUY_COLOR = "#009E73";
const SELL_COLOR = "#E69F00";

export type MarkerProps = { cx?: number; cy?: number };

export function BuyMarker({ cx, cy }: MarkerProps) {
  if (cx == null || cy == null) return null;
  return (
    <g>
      <circle cx={cx} cy={cy} r={9} fill={BUY_COLOR} />
      <text x={cx} y={cy} textAnchor="middle" dominantBaseline="central" fill="white" fontSize={11} fontWeight={700}>B</text>
    </g>
  );
}

export function SellMarker({ cx, cy }: MarkerProps) {
  if (cx == null || cy == null) return null;
  const r = 9;
  return (
    <g>
      <path d={`M ${cx} ${cy - r} L ${cx + r} ${cy} L ${cx} ${cy + r} L ${cx - r} ${cy} Z`} fill={SELL_COLOR} />
      <text x={cx} y={cy} textAnchor="middle" dominantBaseline="central" fill="white" fontSize={11} fontWeight={700}>S</text>
    </g>
  );
}

// ── Cluster markers (interactive) ───────────────────────────────────────

export type HoverState = {
  cluster: Cluster;
  side: "buy" | "sell";
  dayIso: string;
  close: number;
  x: number;
  y: number;
};

export type Selection = { key: string; dates: string[]; side: "buy" | "sell" };

export function clusterKey(side: "buy" | "sell", c: Cluster): string {
  return `${side}-${c.ts}-${c.count}`;
}

export type ClusterMarkerProps = MarkerProps & {
  payload?: { buyCluster?: Cluster; sellCluster?: Cluster; date?: string; close?: number };
  onEnter?: (h: HoverState) => void;
  onMove?: (x: number, y: number) => void;
  onLeave?: () => void;
  onSelect?: (sel: Selection | null) => void;
  selectedKey?: string | null;
};

function ClusterCountBadge({ cx, cy, r, count, color }: { cx: number; cy: number; r: number; count: number; color: string }) {
  if (count <= 1) return null;
  // Position badge just outside the NE of the marker
  const offsetX = r * 0.75;
  const offsetY = -r * 0.9;
  return (
    <text
      x={cx + offsetX}
      y={cy + offsetY}
      textAnchor="start"
      fill={color}
      stroke="white"
      strokeWidth={3}
      paintOrder="stroke fill"
      fontSize={12}
      fontWeight={800}
    >
      ×{count}
    </text>
  );
}

export function BuyClusterMarker({ cx, cy, payload, onEnter, onMove, onLeave, onSelect, selectedKey }: ClusterMarkerProps) {
  const c = payload?.buyCluster;
  if (cx == null || cy == null || !c) return null;
  const { r, count } = c;
  const fontSize = Math.max(9, Math.min(r * 1.1, 13));
  const key = clusterKey("buy", c);
  const isSelected = selectedKey === key;
  return (
    <g
      onMouseEnter={(e) => onEnter?.({ cluster: c, side: "buy", dayIso: payload?.date ?? "", close: payload?.close ?? 0, x: e.clientX, y: e.clientY })}
      onMouseMove={(e) => onMove?.(e.clientX, e.clientY)}
      onMouseLeave={onLeave}
      onClick={(e) => {
        e.stopPropagation();
        onSelect?.(isSelected ? null : { key, dates: c.memberDates, side: "buy" });
      }}
      style={{ cursor: "pointer" }}
    >
      {isSelected && <circle cx={cx} cy={cy} r={r + 4} fill="none" stroke={BUY_COLOR} strokeWidth={2} />}
      <circle cx={cx} cy={cy} r={r} fill={BUY_COLOR} />
      <text x={cx} y={cy} textAnchor="middle" dominantBaseline="central" fill="white" fontSize={fontSize} fontWeight={700} pointerEvents="none">B</text>
      <ClusterCountBadge cx={cx} cy={cy} r={r} count={count} color={BUY_COLOR} />
    </g>
  );
}

export function SellClusterMarker({ cx, cy, payload, onEnter, onMove, onLeave, onSelect, selectedKey }: ClusterMarkerProps) {
  const c = payload?.sellCluster;
  if (cx == null || cy == null || !c) return null;
  const { r, count } = c;
  const fontSize = Math.max(9, Math.min(r * 1.1, 13));
  const key = clusterKey("sell", c);
  const isSelected = selectedKey === key;
  return (
    <g
      onMouseEnter={(e) => onEnter?.({ cluster: c, side: "sell", dayIso: payload?.date ?? "", close: payload?.close ?? 0, x: e.clientX, y: e.clientY })}
      onMouseMove={(e) => onMove?.(e.clientX, e.clientY)}
      onMouseLeave={onLeave}
      onClick={(e) => {
        e.stopPropagation();
        onSelect?.(isSelected ? null : { key, dates: c.memberDates, side: "sell" });
      }}
      style={{ cursor: "pointer" }}
    >
      {isSelected && (
        <path
          d={`M ${cx} ${cy - r - 4} L ${cx + r + 4} ${cy} L ${cx} ${cy + r + 4} L ${cx - r - 4} ${cy} Z`}
          fill="none"
          stroke={SELL_COLOR}
          strokeWidth={2}
        />
      )}
      <path d={`M ${cx} ${cy - r} L ${cx + r} ${cy} L ${cx} ${cy + r} L ${cx - r} ${cy} Z`} fill={SELL_COLOR} />
      <text x={cx} y={cy} textAnchor="middle" dominantBaseline="central" fill="white" fontSize={fontSize} fontWeight={700} pointerEvents="none">S</text>
      <ClusterCountBadge cx={cx} cy={cy} r={r} count={count} color={SELL_COLOR} />
    </g>
  );
}

export { BUY_COLOR, SELL_COLOR };
