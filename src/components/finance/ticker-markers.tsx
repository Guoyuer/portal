// ── SVG scatter markers for the ticker chart ────────────────────────────
//
// Cluster markers render at every scale — inline and dialog. They show a B/S
// letter paired with an `×N` count badge (not color-alone signaling). When
// `onSelect` is supplied, the group is hover/click-aware; otherwise it's
// purely visual and lets parent click handlers through.

import type { Cluster } from "@/lib/format/ticker-data";
import { BUY_COLOR, SELL_COLOR } from "@/lib/format/chart-colors";

export type MarkerProps = { cx?: number; cy?: number };

// ── Cluster markers (interactive when onSelect is provided) ─────────────

export type HoverState = {
  cluster: Cluster;
  side: "buy" | "sell";
  dayIso: string;
  close: number;
  x: number;
  y: number;
};

export type Selection = { key: string; dates: string[]; side: "buy" | "sell" };

function clusterKey(side: "buy" | "sell", c: Cluster): string {
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
  const interactive = Boolean(onSelect || onEnter);
  return (
    <g
      onMouseEnter={(e) => onEnter?.({ cluster: c, side: "buy", dayIso: payload?.date ?? "", close: payload?.close ?? 0, x: e.clientX, y: e.clientY })}
      onMouseMove={(e) => onMove?.(e.clientX, e.clientY)}
      onMouseLeave={onLeave}
      onClick={onSelect ? (e) => {
        e.stopPropagation();
        onSelect(isSelected ? null : { key, dates: c.memberDates, side: "buy" });
      } : undefined}
      style={interactive ? { cursor: "pointer" } : undefined}
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
  const interactive = Boolean(onSelect || onEnter);
  return (
    <g
      onMouseEnter={(e) => onEnter?.({ cluster: c, side: "sell", dayIso: payload?.date ?? "", close: payload?.close ?? 0, x: e.clientX, y: e.clientY })}
      onMouseMove={(e) => onMove?.(e.clientX, e.clientY)}
      onMouseLeave={onLeave}
      onClick={onSelect ? (e) => {
        e.stopPropagation();
        onSelect(isSelected ? null : { key, dates: c.memberDates, side: "sell" });
      } : undefined}
      style={interactive ? { cursor: "pointer" } : undefined}
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

