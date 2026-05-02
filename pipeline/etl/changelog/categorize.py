"""Category-specific aggregation helpers used when rendering the changelog.

Holds the per-asset-category utilities that sit between the changelog's raw
numbers and the pretty-printed email blocks:
  * :data:`_LARGE_MOVE_USD` / :data:`_LARGE_MOVE_PCT` — thresholds for the
    ``[LARGE MOVE]`` header flag on the net-worth block.
  * :func:`_component_check_line` — consistency check that asset categories
    sum back to the stored ``total``.
  * :func:`_render_component_row` — per-category before/after/delta row.
  * :data:`_EXIT_GATE_NAMES` / :func:`_gate_for_exit` — category-like mapping
    from a non-zero exit code to the pipeline gate that blocked.

Kept out of ``render.py`` so the category rules live in one place and the
render layer focuses on layout.
"""
from __future__ import annotations

from .snapshot import NetWorthPoint

# ── Money formatting ────────────────────────────────────────────────────────


def _fmt_money(v: float) -> str:
    """``-$1,234.56`` style (sign outside the $)."""
    if v < 0:
        return f"-${abs(v):,.2f}"
    return f"${v:,.2f}"


def _fmt_delta(v: float) -> str:
    """``+$100.00`` or ``-$100.00`` — always sign-prefixed."""
    if v >= 0:
        return f"+${v:,.2f}"
    return f"-${abs(v):,.2f}"


def _fmt_qty(v: float) -> str:
    """Format a share qty — strip trailing zeros, keep up to 4 decimals."""
    if v == int(v):
        return f"{int(v)}"
    return f"{v:.4f}".rstrip("0").rstrip(".")


# ── Exit-gate category mapping ──────────────────────────────────────────────

# Keep in sync with run_automation.EXIT_* constants. Hard-coded rather than
# imported to avoid a cycle (run_automation imports from changelog).
_EXIT_GATE_NAMES: dict[int, str] = {
    1: "build",
    2: "artifact verification (r2_artifacts.py)",
    3: "R2 publish",
    4: "positions check (verify_positions)",
}


def _gate_for_exit(exit_code: int) -> str:
    """Human label for the step that blocked when the sync exited non-zero."""
    return _EXIT_GATE_NAMES.get(exit_code, f"step (exit {exit_code})")


# ── Net-worth category aggregation ──────────────────────────────────────────

# Net-worth anomaly threshold — flag single-run moves beyond EITHER bar.
# Calibrated against the user's portfolio size (~$400k) so a normal daily
# price swing doesn't trip the flag while a silent data-loss bug would.
_LARGE_MOVE_USD = 5_000.0
_LARGE_MOVE_PCT = 3.0


def _component_check_line(point: NetWorthPoint) -> str | None:
    """Return a ``[!] ... drift`` line when stored ``total`` disagrees with the
    sum of the positive asset categories by more than a penny, else None.

    This guards the allocation invariant (``total == us+non_us+crypto+safe_net``
    per :func:`etl.allocation.compute_daily_allocation`). A non-zero drift
    means a new asset class landed in ``computed_daily.total`` but wasn't
    wired into a category column — e.g. a forgotten frontend refactor.
    """
    drift = point.total - point.asset_sum
    if abs(drift) <= 0.01:
        return None
    return (
        f"  [!] asset categories don't sum to stored total by {_fmt_delta(drift)} "
        f"(total={_fmt_money(point.total)}, assets_sum={_fmt_money(point.asset_sum)})"
    )


def _render_component_row(label: str, before: float, after: float) -> str:
    before_str = _fmt_money(before).rjust(13)
    after_str = _fmt_money(after).rjust(13)
    delta_str = _fmt_delta(after - before).rjust(12)
    return f"    {label:<14}{before_str}  →  {after_str}   ({delta_str})"
