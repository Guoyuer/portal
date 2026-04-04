"""Inline SVG chart generators — no external dependencies.

Each function takes structured data and returns a self-contained SVG string
suitable for embedding in HTML. All use viewBox for responsive scaling.
"""

from __future__ import annotations

import math
from datetime import datetime

from ..types import CategoryData, MonthlyFlowPoint, SnapshotPoint

# ── Color palette ──────────────────────────────────────────────────────────

_PALETTE = [
    "#2563eb",  # blue (US Equity)
    "#7c3aed",  # violet (Non-US Equity)
    "#f59e0b",  # amber (Crypto)
    "#10b981",  # emerald (Safe Net)
    "#ef4444",  # red (Hedge)
    "#06b6d4",  # cyan
    "#ec4899",  # pink
    "#8b5cf6",  # purple
]
_CLR_GREEN = "#27ae60"
_CLR_RED = "#e94560"
_CLR_LINE = "#2563eb"
_CLR_FILL = "#dbeafe"
_CLR_GRID = "#e5e7eb"
_CLR_TEXT = "#374151"
_CLR_TEXT_DIM = "#9ca3af"


# ── Donut chart ────────────────────────────────────────────────────────────


def allocation_donut(categories: list[CategoryData], width: int = 280, height: int = 280) -> str:
    """Render a donut chart of category allocation.

    Each category gets a proportional arc segment. Center shows total count.
    Legend is rendered below the donut.
    """
    if not categories:
        return ""

    total_value = sum(c.value for c in categories)
    if total_value <= 0:
        return ""

    cx, cy = width // 2, width // 2
    r_outer = min(cx, cy) - 10
    r_inner = r_outer * 0.6

    paths: list[str] = []
    angle = -90.0  # start at top

    for i, cat in enumerate(categories):
        if cat.pct < 0.5:
            continue
        sweep = cat.pct / 100 * 360
        color = _PALETTE[i % len(_PALETTE)]

        # SVG arc from angle to angle+sweep
        a1 = math.radians(angle)
        a2 = math.radians(angle + sweep)
        large = 1 if sweep > 180 else 0

        x1_o, y1_o = cx + r_outer * math.cos(a1), cy + r_outer * math.sin(a1)
        x2_o, y2_o = cx + r_outer * math.cos(a2), cy + r_outer * math.sin(a2)
        x1_i, y1_i = cx + r_inner * math.cos(a2), cy + r_inner * math.sin(a2)
        x2_i, y2_i = cx + r_inner * math.cos(a1), cy + r_inner * math.sin(a1)

        d = (
            f"M {x1_o:.1f} {y1_o:.1f} "
            f"A {r_outer} {r_outer} 0 {large} 1 {x2_o:.1f} {y2_o:.1f} "
            f"L {x1_i:.1f} {y1_i:.1f} "
            f"A {r_inner} {r_inner} 0 {large} 0 {x2_i:.1f} {y2_i:.1f} Z"
        )
        paths.append(f'<path d="{d}" fill="{color}" stroke="#fff" stroke-width="1.5"/>')
        angle += sweep

    # Center text
    center = (
        f'<text x="{cx}" y="{cy - 6}" text-anchor="middle" '
        f'font-size="22" font-weight="bold" fill="{_CLR_TEXT}">'
        f"${total_value:,.0f}</text>"
        f'<text x="{cx}" y="{cy + 14}" text-anchor="middle" '
        f'font-size="11" fill="{_CLR_TEXT_DIM}">Total</text>'
    )

    # Legend
    legend_y = width + 8
    legend_items: list[str] = []
    for i, cat in enumerate(categories):
        if cat.pct < 0.5:
            continue
        color = _PALETTE[i % len(_PALETTE)]
        col = i % 2
        row = i // 2
        lx = 8 + col * (width // 2)
        ly = legend_y + row * 18
        legend_items.append(
            f'<rect x="{lx}" y="{ly}" width="10" height="10" rx="2" fill="{color}"/>'
            f'<text x="{lx + 14}" y="{ly + 9}" font-size="11" fill="{_CLR_TEXT}">'
            f"{cat.name} {cat.pct:.0f}%</text>"
        )

    legend_rows = (len([c for c in categories if c.pct >= 0.5]) + 1) // 2
    total_h = width + 12 + legend_rows * 18

    return (
        f'<svg viewBox="0 0 {width} {total_h}" xmlns="http://www.w3.org/2000/svg" '
        f'style="font-family:Arial,sans-serif;max-width:{width}px">'
        f"{''.join(paths)}{center}{''.join(legend_items)}</svg>"
    )


# ── Net worth trend line ───────────────────────────────────────────────────


def trend_line(points: list[SnapshotPoint], width: int = 540, height: int = 220) -> str:
    """Render a line chart of portfolio value over time."""
    if len(points) < 2:
        return ""

    pad_l, pad_r, pad_t, pad_b = 60, 20, 20, 30
    chart_w = width - pad_l - pad_r
    chart_h = height - pad_t - pad_b

    vals = [p.total for p in points]
    v_min = min(vals) * 0.95
    v_max = max(vals) * 1.02
    v_range = v_max - v_min or 1

    def x(i: int) -> float:
        return pad_l + i / (len(points) - 1) * chart_w

    def y(v: float) -> float:
        return pad_t + (1 - (v - v_min) / v_range) * chart_h

    # Grid lines + Y labels
    grid: list[str] = []
    n_grid = 4
    for i in range(n_grid + 1):
        gy = pad_t + i / n_grid * chart_h
        gv = v_max - i / n_grid * v_range
        grid.append(
            f'<line x1="{pad_l}" y1="{gy:.0f}" x2="{width - pad_r}" y2="{gy:.0f}" '
            f'stroke="{_CLR_GRID}" stroke-width="0.5"/>'
            f'<text x="{pad_l - 6}" y="{gy + 4:.0f}" text-anchor="end" '
            f'font-size="10" fill="{_CLR_TEXT_DIM}">${gv / 1000:.0f}k</text>'
        )

    # Area fill + line
    line_pts = " ".join(f"{x(i):.1f},{y(p.total):.1f}" for i, p in enumerate(points))
    area_pts = f"{x(0):.1f},{pad_t + chart_h:.1f} {line_pts} {x(len(points) - 1):.1f},{pad_t + chart_h:.1f}"

    # Dots + X labels (show every ~3rd label to avoid overlap)
    dots: list[str] = []
    labels: list[str] = []
    step = max(1, len(points) // 6)
    for i, p in enumerate(points):
        dots.append(f'<circle cx="{x(i):.1f}" cy="{y(p.total):.1f}" r="3" fill="{_CLR_LINE}"/>')
        if i % step == 0 or i == len(points) - 1:
            try:
                label = datetime.strptime(p.date, "%Y-%m-%d").strftime("%b %d")
            except ValueError:
                label = p.date[-5:]
            labels.append(
                f'<text x="{x(i):.1f}" y="{height - 4}" text-anchor="middle" '
                f'font-size="10" fill="{_CLR_TEXT_DIM}">{label}</text>'
            )

    return (
        f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" '
        f'style="font-family:Arial,sans-serif;max-width:{width}px">'
        f"{''.join(grid)}"
        f'<polygon points="{area_pts}" fill="{_CLR_FILL}" opacity="0.5"/>'
        f'<polyline points="{line_pts}" fill="none" stroke="{_CLR_LINE}" stroke-width="2"/>'
        f"{''.join(dots)}{''.join(labels)}</svg>"
    )


# ── Monthly income vs expenses bars ───────────────────────────────────────


def monthly_bars(flows: list[MonthlyFlowPoint], width: int = 540, height: int = 240) -> str:
    """Render grouped bar chart of monthly income (green) vs expenses (red).

    Overlays a savings rate line on a secondary axis.
    """
    if not flows:
        return ""

    # Show at most 12 most recent months
    data = flows[-12:]

    pad_l, pad_r, pad_t, pad_b = 50, 40, 20, 44
    chart_w = width - pad_l - pad_r
    chart_h = height - pad_t - pad_b

    # Cap Y-axis at median * 3 to prevent outliers from crushing normal bars
    all_vals = sorted(max(d.income, d.expenses) for d in data)
    median_val = all_vals[len(all_vals) // 2] if all_vals else 1
    raw_max = max(all_vals) if all_vals else 1
    cap = median_val * 3
    max_val = (min(raw_max, cap) if cap > 0 else raw_max) * 1.1 or 1

    bar_group_w = chart_w / len(data)
    bar_w = bar_group_w * 0.35
    gap = bar_group_w * 0.1

    # Y-axis grid + labels
    grid: list[str] = []
    n_grid = 4
    for i in range(n_grid + 1):
        gy = pad_t + i / n_grid * chart_h
        gv = max_val * (1 - i / n_grid)
        grid.append(
            f'<line x1="{pad_l}" y1="{gy:.0f}" x2="{width - pad_r}" y2="{gy:.0f}" '
            f'stroke="{_CLR_GRID}" stroke-width="0.5"/>'
            f'<text x="{pad_l - 6}" y="{gy + 4:.0f}" text-anchor="end" '
            f'font-size="10" fill="{_CLR_TEXT_DIM}">${gv / 1000:.0f}k</text>'
        )

    # Bars
    bars: list[str] = []
    for i, d in enumerate(data):
        gx = pad_l + i * bar_group_w + gap

        # Income bar (green) — clamp to max_val, show marker if clipped
        income_clamped = min(d.income, max_val)
        ih = income_clamped / max_val * chart_h
        iy = pad_t + chart_h - ih
        bars.append(
            f'<rect x="{gx:.1f}" y="{iy:.1f}" width="{bar_w:.1f}" height="{ih:.1f}" '
            f'fill="{_CLR_GREEN}" rx="2" opacity="0.85"/>'
        )
        if d.income > max_val:
            # Truncation marker + actual value
            bars.append(
                f'<text x="{gx + bar_w / 2:.1f}" y="{iy - 2:.1f}" text-anchor="middle" '
                f'font-size="8" fill="{_CLR_GREEN}">${d.income / 1000:.0f}k</text>'
            )

        # Expense bar (red)
        expense_clamped = min(d.expenses, max_val)
        eh = expense_clamped / max_val * chart_h
        ey = pad_t + chart_h - eh
        bars.append(
            f'<rect x="{gx + bar_w + 2:.1f}" y="{ey:.1f}" width="{bar_w:.1f}" height="{eh:.1f}" '
            f'fill="{_CLR_RED}" rx="2" opacity="0.85"/>'
        )

        # X label
        try:
            label = datetime.strptime(d.month, "%Y-%m").strftime("%b")
        except ValueError:
            label = d.month[-2:]
        lx = gx + bar_group_w / 2 - gap / 2
        bars.append(
            f'<text x="{lx:.1f}" y="{height - 18}" text-anchor="middle" '
            f'font-size="10" fill="{_CLR_TEXT_DIM}">{label}</text>'
        )

    # Savings rate line overlay
    sr_line: list[str] = []
    sr_points: list[str] = []
    for i, d in enumerate(data):
        sx = pad_l + (i + 0.5) * bar_group_w - gap / 2
        # Map savings rate 0-100% to chart height
        sr_clamped = max(0, min(100, d.savings_rate))
        sy = pad_t + (1 - sr_clamped / 100) * chart_h
        sr_points.append(f"{sx:.1f},{sy:.1f}")

    if sr_points:
        sr_line.append(
            f'<polyline points="{" ".join(sr_points)}" fill="none" '
            f'stroke="{_CLR_LINE}" stroke-width="2" stroke-dasharray="4,3"/>'
        )
        # Secondary Y-axis labels (right side)
        for pct in (0, 50, 100):
            ry = pad_t + (1 - pct / 100) * chart_h
            sr_line.append(
                f'<text x="{width - pad_r + 6}" y="{ry + 4:.0f}" font-size="10" fill="{_CLR_LINE}">{pct}%</text>'
            )

    # Legend (below X labels)
    ly = height - 2
    legend = (
        f'<rect x="{pad_l}" y="{ly - 6}" width="8" height="8" rx="1" fill="{_CLR_GREEN}"/>'
        f'<text x="{pad_l + 11}" y="{ly}" font-size="9" fill="{_CLR_TEXT_DIM}">Income</text>'
        f'<rect x="{pad_l + 60}" y="{ly - 6}" width="8" height="8" rx="1" fill="{_CLR_RED}"/>'
        f'<text x="{pad_l + 71}" y="{ly}" font-size="9" fill="{_CLR_TEXT_DIM}">Expenses</text>'
        f'<line x1="{pad_l + 130}" y1="{ly - 3}" x2="{pad_l + 148}" y2="{ly - 3}" '
        f'stroke="{_CLR_LINE}" stroke-width="2" stroke-dasharray="4,3"/>'
        f'<text x="{pad_l + 152}" y="{ly}" font-size="9" fill="{_CLR_TEXT_DIM}">Savings %</text>'
    )

    return (
        f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" '
        f'style="font-family:Arial,sans-serif;max-width:{width}px">'
        f"{''.join(grid)}{''.join(bars)}{''.join(sr_line)}{legend}</svg>"
    )
