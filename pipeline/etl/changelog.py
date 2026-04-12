"""DB state snapshot + diff + HTML/text email body for sync changelog emails.

Flow: ``capture(db_path)`` produces a :class:`SyncSnapshot` before and after a
sync run. ``diff(before, after)`` turns the two into a :class:`SyncChangelog`
describing what rows appeared. ``format_html`` / ``format_text`` render the
changelog into the body of the notification email.

The snapshot is intentionally minimal — full tuple sets for small tables (so we
can diff exact rows), aggregate counts for big tables (``daily_close``,
``econ_series``) where per-row detail is noise.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ── Snapshot ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SyncSnapshot:
    """Captured state of the local DB at one point in time.

    Small tables keep full tuple sets so ``diff`` can enumerate new rows.
    Large tables (``daily_close``, ``econ_series``) keep counts + bounds only.
    """

    # Small tables — full tuple sets for exact diff
    # (run_date, action_type, symbol, quantity, amount)
    fidelity_txns: frozenset[tuple[str, str, str, float, float]] = field(default_factory=frozenset)
    # (date, type, category, amount)
    qianji_txns: frozenset[tuple[str, str, str, float]] = field(default_factory=frozenset)
    # date -> total (computed_daily is small: ~1 row/day)
    computed_daily: dict[str, float] = field(default_factory=dict)

    # Large tables — aggregates only
    daily_close_count: int = 0
    daily_close_max_date: str = ""
    econ_series_keys: frozenset[str] = field(default_factory=frozenset)
    empower_snapshots_count: int = 0


def capture(db_path: Path) -> SyncSnapshot:
    """Read the local DB and build a :class:`SyncSnapshot`.

    Returns an empty snapshot if the DB file does not exist yet (e.g. the build
    step failed before the file was created). Every query is read-only.
    """
    if not db_path.exists():
        return SyncSnapshot()

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        fidelity = frozenset(
            (str(r[0]), str(r[1]), str(r[2]), float(r[3]), float(r[4]))
            for r in conn.execute(
                "SELECT run_date, action_type, symbol, quantity, amount "
                "FROM fidelity_transactions"
            )
        )
        qianji = frozenset(
            (str(r[0]), str(r[1]), str(r[2]), float(r[3]))
            for r in conn.execute(
                "SELECT date, type, category, amount FROM qianji_transactions"
            )
        )
        computed_daily = {
            str(r[0]): float(r[1])
            for r in conn.execute("SELECT date, total FROM computed_daily")
        }
        dc_row = conn.execute(
            "SELECT COUNT(*), COALESCE(MAX(date), '') FROM daily_close"
        ).fetchone()
        dc_count = int(dc_row[0]) if dc_row else 0
        dc_max = str(dc_row[1]) if dc_row else ""
        econ_keys = frozenset(
            str(r[0]) for r in conn.execute("SELECT DISTINCT key FROM econ_series")
        )
        emp_count_row = conn.execute(
            "SELECT COUNT(*) FROM empower_snapshots"
        ).fetchone()
        emp_count = int(emp_count_row[0]) if emp_count_row else 0
    finally:
        conn.close()

    return SyncSnapshot(
        fidelity_txns=fidelity,
        qianji_txns=qianji,
        computed_daily=computed_daily,
        daily_close_count=dc_count,
        daily_close_max_date=dc_max,
        econ_series_keys=econ_keys,
        empower_snapshots_count=emp_count,
    )


# ── Changelog ────────────────────────────────────────────────────────────────


@dataclass
class SyncChangelog:
    """What changed between two snapshots.

    ``fidelity_added`` / ``computed_daily_added`` enumerate new rows; bigger
    tables expose a count delta only. ``econ_refreshed`` reflects whether the
    FRED import ran at all — because FRED does a full replace every run, we
    cannot tell from row counts alone whether values changed, and on its own it
    is NOT considered a meaningful change (see :meth:`has_meaningful_changes`).
    """

    # (run_date, action_type, symbol, quantity, amount), sorted by run_date
    fidelity_added: list[tuple[str, str, str, float, float]] = field(default_factory=list)
    qianji_added_count: int = 0
    # category -> (count, total_amount)
    qianji_added_by_category: dict[str, tuple[int, float]] = field(default_factory=dict)
    # new date -> total (dates that appeared in "after" but not "before")
    computed_daily_added: dict[str, float] = field(default_factory=dict)
    daily_close_added: int = 0
    daily_close_max_before: str = ""
    daily_close_max_after: str = ""
    econ_refreshed: bool = False
    empower_added: int = 0
    net_worth_before: float | None = None
    net_worth_after: float | None = None
    net_worth_delta: float | None = None

    def has_meaningful_changes(self) -> bool:
        """True if this changelog represents a sync that did actual work.

        FRED refresh alone is NOT meaningful — every run touches ``econ_series``
        because the import is a full replace, so it would trigger noise on
        silent runs.
        """
        return bool(
            self.fidelity_added
            or self.qianji_added_count > 0
            or self.computed_daily_added
            or self.daily_close_added > 0
            or self.empower_added > 0
        )

    def net_worth_delta_pct(self) -> float | None:
        """Return delta as % of before (None if either endpoint is missing or before == 0)."""
        if self.net_worth_before is None or self.net_worth_after is None:
            return None
        if self.net_worth_before == 0:
            return None
        return (self.net_worth_after - self.net_worth_before) / self.net_worth_before * 100


def empty_changelog() -> SyncChangelog:
    """Return a blank changelog (no changes). Used when ``snapshot_after`` is None."""
    return SyncChangelog()


def diff(before: SyncSnapshot, after: SyncSnapshot) -> SyncChangelog:
    """Compute the set difference between two snapshots."""
    # Fidelity: sorted by run_date (first tuple element) for stable email output
    fidelity_added = sorted(
        after.fidelity_txns - before.fidelity_txns,
        key=lambda row: (row[0], row[1], row[2]),
    )

    # Qianji: tally count + total $ by category
    qianji_added_rows = after.qianji_txns - before.qianji_txns
    qianji_by_cat: dict[str, tuple[int, float]] = {}
    for _date, _type, category, amount in qianji_added_rows:
        count, total = qianji_by_cat.get(category, (0, 0.0))
        qianji_by_cat[category] = (count + 1, total + amount)

    # computed_daily: dates that weren't in before
    before_dates = set(before.computed_daily.keys())
    new_daily = {
        dt: total for dt, total in after.computed_daily.items() if dt not in before_dates
    }

    # daily_close is counts-only (too many rows for tuple diff)
    daily_close_delta = max(0, after.daily_close_count - before.daily_close_count)

    # Net worth = latest computed_daily total
    nw_before = _latest_value(before.computed_daily)
    nw_after = _latest_value(after.computed_daily)
    nw_delta: float | None
    if nw_before is None or nw_after is None:
        nw_delta = None
    else:
        nw_delta = nw_after - nw_before

    # FRED: full replace every run, so any presence of keys after the sync
    # indicates the import ran. Value-level change detection is deliberately
    # skipped — too granular, too noisy. This flag does NOT contribute to
    # has_meaningful_changes on its own.
    econ_refreshed = bool(after.econ_series_keys)

    return SyncChangelog(
        fidelity_added=fidelity_added,
        qianji_added_count=len(qianji_added_rows),
        qianji_added_by_category=qianji_by_cat,
        computed_daily_added=new_daily,
        daily_close_added=daily_close_delta,
        daily_close_max_before=before.daily_close_max_date,
        daily_close_max_after=after.daily_close_max_date,
        econ_refreshed=econ_refreshed,
        empower_added=max(0, after.empower_snapshots_count - before.empower_snapshots_count),
        net_worth_before=nw_before,
        net_worth_after=nw_after,
        net_worth_delta=nw_delta,
    )


def _latest_value(daily: dict[str, float]) -> float | None:
    """Return the total for the max date (lexicographic = chronological for ISO dates)."""
    if not daily:
        return None
    latest_date = max(daily.keys())
    return daily[latest_date]


# ── Formatting ───────────────────────────────────────────────────────────────


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


def format_text(changelog: SyncChangelog, context: dict[str, Any]) -> str:
    """Plain-text email body. No HTML tags."""
    lines: list[str] = []
    timestamp = context.get("timestamp", "")
    lines.append(f"Portal Sync Report  {timestamp}".rstrip())
    lines.append("")
    status_label = context.get("status_label", "OK")
    lines.append(f"Status: {status_label}")
    exit_code = context.get("exit_code", 0)
    if exit_code != 0:
        lines.append(f"Exit code: {exit_code}")
        error = context.get("error")
        if error:
            lines.append(f"Error: {error}")
    lines.append("")

    # Changes
    lines.append("Changes")
    any_changes = False
    if changelog.fidelity_added:
        any_changes = True
        lines.append(f"  * Fidelity: +{len(changelog.fidelity_added)} transaction(s)")
        for run_date, action_type, symbol, qty, amount in changelog.fidelity_added:
            sym = symbol or "-"
            qty_str = _fmt_qty(qty) if qty else ""
            qty_part = f"  {qty_str} share(s)" if qty_str else ""
            lines.append(
                f"      {run_date}  {action_type.upper():<5} {sym:<6}{qty_part}   {_fmt_delta(amount)}"
            )
    if changelog.qianji_added_count > 0:
        any_changes = True
        total = sum(tot for _c, tot in changelog.qianji_added_by_category.values())
        lines.append(
            f"  * Qianji: +{changelog.qianji_added_count} record(s) ({_fmt_money(total)} total)"
        )
        for cat, (count, tot) in sorted(changelog.qianji_added_by_category.items()):
            label = cat or "(uncategorized)"
            lines.append(f"      {label}: {count} x {_fmt_money(tot)}")
    if changelog.daily_close_added > 0:
        any_changes = True
        through = changelog.daily_close_max_after or "?"
        lines.append(
            f"  * Prices: {changelog.daily_close_added} new close row(s); through {through}"
        )
    if changelog.econ_refreshed:
        lines.append(f"  * FRED: {len(context.get('econ_keys', [])) or '(refreshed)'} indicator(s) refreshed")
    if changelog.empower_added > 0:
        any_changes = True
        lines.append(f"  * Empower: +{changelog.empower_added} 401k snapshot(s)")
    if not any_changes and not changelog.econ_refreshed:
        lines.append("  (no changes detected)")
    lines.append("")

    # Net worth
    if changelog.net_worth_before is not None and changelog.net_worth_after is not None:
        lines.append("Net Worth")
        before_date = max(context.get("before_dates", [""]) or [""])
        after_date = max(context.get("after_dates", [""]) or [""])
        lines.append(f"  {before_date}: {_fmt_money(changelog.net_worth_before)}")
        if changelog.net_worth_delta is not None:
            pct = changelog.net_worth_delta_pct()
            pct_str = f" / {pct:+.2f}%" if pct is not None else ""
            lines.append(
                f"  {after_date}: {_fmt_money(changelog.net_worth_after)}"
                f"  ({_fmt_delta(changelog.net_worth_delta)}{pct_str})"
            )
        lines.append("")

    # D1 sync
    lines.append("D1 Sync")
    if changelog.computed_daily_added:
        dates = sorted(changelog.computed_daily_added.keys())
        dates_str = ", ".join(dates[-3:]) + ("..." if len(dates) > 3 else "")
        lines.append(f"  computed_daily:        +{len(changelog.computed_daily_added)} row(s)  ({dates_str})")
    if changelog.daily_close_added > 0:
        lines.append(f"  daily_close:           +{changelog.daily_close_added} row(s)")
    if changelog.fidelity_added:
        lines.append(f"  fidelity_transactions: +{len(changelog.fidelity_added)} row(s)")
    if changelog.qianji_added_count > 0:
        lines.append(f"  qianji_transactions:   +{changelog.qianji_added_count} row(s)")
    if changelog.empower_added > 0:
        lines.append(f"  empower_snapshots:     +{changelog.empower_added} row(s)")
    if changelog.econ_refreshed:
        lines.append(f"  econ_series:           {len(context.get('econ_keys', []))} key(s) refreshed (full replace)")
    lines.append("")

    # Warnings
    warnings = context.get("warnings") or []
    if warnings:
        lines.append("Warnings (from validation)")
        for w in warnings:
            lines.append(f"  * {w}")
        lines.append("")

    log_file = context.get("log_file", "")
    if log_file:
        lines.append(f"Log: {log_file}")
    return "\n".join(lines)


def format_html(changelog: SyncChangelog, context: dict[str, Any]) -> str:
    """HTML email body. Simple table-less layout with monospace blocks."""
    # Rather than duplicate the whole rendering, wrap the text version in
    # <pre> so spacing stays predictable in Gmail. Add a minimal header.
    text = format_text(changelog, context)
    safe = (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
    status_label = context.get("status_label", "OK")
    exit_code = context.get("exit_code", 0)
    color = "#2e7d32" if exit_code == 0 else "#c62828"
    return (
        "<html><body style=\"font-family: -apple-system, Segoe UI, sans-serif; color: #222;\">"
        f"<h2 style=\"color: {color}; margin-bottom: 8px;\">Portal Sync — {status_label}</h2>"
        f"<pre style=\"font-family: Consolas, Menlo, monospace; font-size: 13px; "
        f"background: #f6f8fa; padding: 14px 16px; border-radius: 6px; "
        f"white-space: pre-wrap; line-height: 1.45;\">{safe}</pre>"
        "</body></html>"
    )


def build_subject(changelog: SyncChangelog, exit_code: int) -> str:
    """Short, informative subject line.

    Successful syncs with changes → summary of counts. Failures → prominent
    [FAIL] tag + exit code.
    """
    if exit_code != 0:
        return f"[Portal Sync] FAIL (exit {exit_code})"

    bits: list[str] = []
    if changelog.fidelity_added:
        bits.append(f"{len(changelog.fidelity_added)} fidelity")
    if changelog.qianji_added_count > 0:
        bits.append(f"{changelog.qianji_added_count} qianji")
    if changelog.empower_added > 0:
        bits.append(f"{changelog.empower_added} empower")
    if changelog.net_worth_delta is not None:
        bits.append(f"nw {_fmt_delta(changelog.net_worth_delta)}")
    if not bits:
        return "[Portal Sync] OK"
    return "[Portal Sync] OK — " + ", ".join(bits)
