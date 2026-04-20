// ── SVG scatter markers for the ticker chart ────────────────────────────
//
// Cluster markers render at every scale — inline and dialog. They show a B/S
// letter paired with an `×N` count badge (not color-alone signaling). When
// `onSelect` is supplied, the group is hover/click-aware; otherwise it's
// purely visual and lets parent click handlers through.

import type { Cluster } from "@/lib/format/ticker-data";
import type { HoverState } from "@/lib/hooks/use-hover-state";
import { BUY_COLOR, SELL_COLOR } from "@/lib/format/chart-colors";

export type MarkerProps = { cx?: number; cy?: number };

// ── Cluster markers (interactive when onSelect is provided) ─────────────

export type Selection = { key: string; dates: string[]; side: "buy" | "sell" };

function clusterKey(side: "buy" | "sell", c: Cluster): string {
  return `${side}-${c.ts}-${c.count}`;
}

export type ClusterMarkerProps = MarkerProps & {
  payload?: { buyCluster?: Cluster; sellCluster?: Cluster; date?: string; close?: number; value?: number };
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

const diamondPath = (cx: number, cy: number, r: number) =>
  `M ${cx} ${cy - r} L ${cx + r} ${cy} L ${cx} ${cy + r} L ${cx - r} ${cy} Z`;

function ClusterShape({ side, cx, cy, r, color, selected }: { side: "buy" | "sell"; cx: number; cy: number; r: number; color: string; selected: boolean }) {
  if (side === "buy") {
    return (
      <>
        {selected && <circle cx={cx} cy={cy} r={r + 4} fill="none" stroke={color} strokeWidth={2} />}
        <circle cx={cx} cy={cy} r={r} fill={color} />
      </>
    );
  }
  return (
    <>
      {selected && <path d={diamondPath(cx, cy, r + 4)} fill="none" stroke={color} strokeWidth={2} />}
      <path d={diamondPath(cx, cy, r)} fill={color} />
    </>
  );
}

function ClusterMarker({ side, cx, cy, payload, onEnter, onMove, onLeave, onSelect, selectedKey }: ClusterMarkerProps & { side: "buy" | "sell" }) {
  const c = side === "buy" ? payload?.buyCluster : payload?.sellCluster;
  if (cx == null || cy == null || !c) return null;
  const { r, count } = c;
  const color = side === "buy" ? BUY_COLOR : SELL_COLOR;
  const letter = side === "buy" ? "B" : "S";
  const key = clusterKey(side, c);
  const isSelected = selectedKey === key;
  const interactive = Boolean(onSelect || onEnter);
  const fontSize = Math.max(9, Math.min(r * 1.1, 13));
  return (
    <g
      onMouseEnter={(e) => onEnter?.({ cluster: c, side, dayIso: payload?.date ?? "", close: payload?.close ?? payload?.value ?? 0, x: e.clientX, y: e.clientY })}
      onMouseMove={(e) => onMove?.(e.clientX, e.clientY)}
      onMouseLeave={onLeave}
      onClick={onSelect ? (e) => {
        e.stopPropagation();
        onSelect(isSelected ? null : { key, dates: c.memberDates, side });
      } : undefined}
      style={interactive ? { cursor: "pointer" } : undefined}
    >
      <ClusterShape side={side} cx={cx} cy={cy} r={r} color={color} selected={isSelected} />
      <text x={cx} y={cy} textAnchor="middle" dominantBaseline="central" fill="white" fontSize={fontSize} fontWeight={700} pointerEvents="none">{letter}</text>
      <ClusterCountBadge cx={cx} cy={cy} r={r} count={count} color={color} />
    </g>
  );
}

export const BuyClusterMarker = (props: ClusterMarkerProps) => <ClusterMarker {...props} side="buy" />;
export const SellClusterMarker = (props: ClusterMarkerProps) => <ClusterMarker {...props} side="sell" />;

// ── REINVEST marker: tiny muted dot, non-interactive ──────────────────
export function ReinvestMarker({ cx, cy }: MarkerProps) {
  if (cx == null || cy == null) return null;
  return <circle cx={cx} cy={cy} r={2.5} fill={BUY_COLOR} fillOpacity={0.4} />;
}

