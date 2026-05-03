"""Small before/after receipt used by automation emails.

The email is an operator notification, not a correctness gate. Correctness is
handled by R2 artifact verification, manifest hashes, row counts, Zod parsing,
and automation logs. This module formats only the summary fields useful in an
inbox: row-count deltas, latest net worth, published artifact version, warnings,
duration, and failure stage.
"""
from __future__ import annotations

import html
import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..db import get_readonly_connection

_ROW_TABLES: dict[str, str] = {
    "daily": "computed_daily",
    "dailyTickers": "computed_daily_tickers",
    "fidelityTxns": "fidelity_transactions",
    "qianjiTxns": "qianji_transactions",
    "robinhoodTxns": "robinhood_transactions",
    "empowerContributions": "empower_contributions",
    "dailyClose": "daily_close",
    "econSeries": "econ_series",
    "marketIndices": "computed_market_indices",
    "holdingsDetail": "computed_holdings_detail",
}

_EXIT_GATE_NAMES: dict[int, str] = {
    1: "build",
    2: "artifact verification (r2_artifacts.py)",
    3: "R2 publish",
    4: "positions check (verify_positions)",
}


@dataclass(frozen=True)
class NetWorthPoint:
    date: str
    value: float


@dataclass(frozen=True)
class SyncSnapshot:
    row_counts: dict[str, int] = field(default_factory=dict)
    net_worth: NetWorthPoint | None = None


@dataclass(frozen=True)
class PublishSummary:
    version: str
    generated_at: str
    latest_date: str
    total_bytes: int
    object_count: int
    row_counts: dict[str, int]
    price_symbols: int
    price_rows: int
    price_transaction_rows: int


@dataclass
class SyncReceipt:
    before: SyncSnapshot = field(default_factory=SyncSnapshot)
    after: SyncSnapshot | None = None

    @property
    def row_deltas(self) -> list[tuple[str, int, int, int]]:
        if self.after is None:
            return []
        keys = sorted(set(self.before.row_counts) | set(self.after.row_counts))
        return [
            (
                key,
                self.before.row_counts.get(key, 0),
                self.after.row_counts.get(key, 0),
                self.after.row_counts.get(key, 0) - self.before.row_counts.get(key, 0),
            )
            for key in keys
        ]

    @property
    def net_worth_delta(self) -> float | None:
        before = self.before.net_worth
        after = self.after.net_worth if self.after else None
        if before is None or after is None:
            return None
        return after.value - before.value

    def net_worth_delta_pct(self) -> float | None:
        before = self.before.net_worth
        after = self.after.net_worth if self.after else None
        if before is None or before.value == 0 or after is None:
            return None
        return (after.value - before.value) / before.value * 100

    def has_meaningful_changes(self) -> bool:
        return bool(
            any(delta != 0 for _, _, _, delta in self.row_deltas)
            or (self.net_worth_delta is not None and abs(self.net_worth_delta) >= 0.01)
        )


def capture(db_path: Path) -> SyncSnapshot:
    if not db_path.exists():
        return SyncSnapshot()

    conn = get_readonly_connection(db_path)
    try:
        row_counts = {
            label: _count_rows(conn, table)
            for label, table in _ROW_TABLES.items()
        }
        nw = _latest_net_worth(conn)
    finally:
        conn.close()
    return SyncSnapshot(row_counts=row_counts, net_worth=nw)


def diff(before: SyncSnapshot, after: SyncSnapshot) -> SyncReceipt:
    return SyncReceipt(before=before, after=after)


def load_publish_summary(path: Path) -> PublishSummary | None:
    if not path.exists():
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return None
    return _publish_summary_from_mapping(raw)


def format_text(receipt: SyncReceipt, context: Mapping[str, Any]) -> str:
    lines: list[str] = [
        f"Portal Sync Report  {context.get('timestamp', '')}",
        "",
        f"Status: {context.get('status_label', 'OK')}",
    ]

    exit_code = int(context.get("exit_code", 0) or 0)
    if exit_code:
        lines.append(f"Exit code: {exit_code}")
        lines.append(f"Blocked at: {_gate_for_exit(exit_code)}")
        if context.get("error"):
            lines.append(f"Error: {context['error']}")
    lines.append("")

    summary = context.get("publish_summary")
    if isinstance(summary, PublishSummary):
        publish_mode = str(context.get("publish_mode") or "?")
        publish_status = "skipped (dry-run)" if context.get("dry_run") else publish_mode
        lines.extend([
            "Artifact",
            f"  Version: {summary.version}",
            f"  Latest date: {summary.latest_date or '?'}",
            f"  Generated: {summary.generated_at or '?'}",
            f"  Publish: {publish_status}",
            f"  Objects: {summary.object_count} ({_fmt_bytes(summary.total_bytes)})",
            (
                "  Prices: "
                f"{summary.price_symbols} symbols, "
                f"{summary.price_rows:,} price rows, "
                f"{summary.price_transaction_rows:,} transaction rows"
            ),
            "",
        ])

    lines.append("Snapshot")
    _append_net_worth(lines, receipt)
    changed = [row for row in receipt.row_deltas if row[3] != 0]
    if changed:
        lines.append("  Row count changes:")
        for name, before, after, delta in changed:
            lines.append(f"    {name}: {before:,} -> {after:,} ({_fmt_int_delta(delta)})")
    else:
        lines.append("  Row count changes: none")
    lines.append("")

    warnings = list(context.get("warnings") or [])
    if warnings:
        lines.append("Warnings")
        for warning in warnings:
            lines.append(f"  * {warning}")
        lines.append("")

    if context.get("log_file"):
        lines.append(f"Log: {context['log_file']}")
    if context.get("duration"):
        lines.append(f"Duration: {context['duration']}")
    return "\n".join(lines)


def format_html(receipt: SyncReceipt, context: Mapping[str, Any]) -> str:
    exit_code = int(context.get("exit_code", 0) or 0)
    color = "#2e7d32" if exit_code == 0 else "#c62828"
    text = html.escape(format_text(receipt, context), quote=False)
    return f"<h2 style=\"color:{color}\">Portal Sync</h2><pre>{text}</pre>"


def build_subject(
    receipt: SyncReceipt,
    exit_code: int,
    status_label: str | None = None,
    publish_summary: PublishSummary | None = None,
) -> str:
    if exit_code != 0:
        if status_label is None:
            return f"[Portal Sync] FAIL (exit {exit_code})"
        return f"[Portal Sync] FAIL - {status_label}"

    bits: list[str] = []
    if publish_summary and publish_summary.latest_date:
        bits.append(publish_summary.latest_date)
    if receipt.net_worth_delta is not None:
        bits.append(f"nw {_fmt_delta(receipt.net_worth_delta)}")
    changed_rows = sum(abs(delta) for _, _, _, delta in receipt.row_deltas if delta != 0)
    if changed_rows:
        bits.append(f"{changed_rows:,} row delta")
    return "[Portal Sync] OK" if not bits else "[Portal Sync] OK - " + ", ".join(bits)


def _count_rows(conn: sqlite3.Connection, table: str) -> int:
    try:
        row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row[0]) if row else 0


def _latest_net_worth(conn: sqlite3.Connection) -> NetWorthPoint | None:
    try:
        row = conn.execute(
            "SELECT date, total, liabilities FROM computed_daily ORDER BY date DESC LIMIT 1"
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    if not row:
        return None
    return NetWorthPoint(date=str(row[0]), value=float(row[1]) + float(row[2]))


def _publish_summary_from_mapping(raw: Mapping[str, Any]) -> PublishSummary:
    price_counts = raw.get("priceRowCounts")
    if not isinstance(price_counts, dict):
        price_counts = {}
    row_counts = raw.get("rowCounts")
    if not isinstance(row_counts, dict):
        row_counts = {}
    source = raw.get("source")
    if not isinstance(source, dict):
        source = {}
    return PublishSummary(
        version=str(raw.get("version") or ""),
        generated_at=str(raw.get("generatedAt") or ""),
        latest_date=str(source.get("latestDate") or ""),
        total_bytes=int(raw.get("totalBytes") or 0),
        object_count=int(raw.get("objectCount") or 0),
        row_counts={str(k): int(v) for k, v in row_counts.items()},
        price_symbols=len(price_counts),
        price_rows=sum(int(v.get("priceRows") or 0) for v in price_counts.values() if isinstance(v, dict)),
        price_transaction_rows=sum(
            int(v.get("transactionRows") or 0) for v in price_counts.values() if isinstance(v, dict)
        ),
    )


def _append_net_worth(lines: list[str], receipt: SyncReceipt) -> None:
    before = receipt.before.net_worth
    after = receipt.after.net_worth if receipt.after else None
    delta = receipt.net_worth_delta
    pct = receipt.net_worth_delta_pct()
    if before is not None and after is not None and delta is not None:
        pct_text = f" / {pct:+.2f}%" if pct is not None else ""
        lines.append(
            f"  Net worth: {before.date} {_fmt_money(before.value)} -> "
            f"{after.date} {_fmt_money(after.value)} ({_fmt_delta(delta)}{pct_text})"
        )
    elif after is not None:
        lines.append(f"  Net worth: {after.date} {_fmt_money(after.value)} (no prior snapshot)")
    elif before is not None:
        lines.append(f"  Net worth: {before.date} {_fmt_money(before.value)} (no after snapshot)")
    else:
        lines.append("  Net worth: unavailable")


def _gate_for_exit(exit_code: int) -> str:
    return _EXIT_GATE_NAMES.get(exit_code, f"step (exit {exit_code})")


def _fmt_money(v: float) -> str:
    if v < 0:
        return f"-${abs(v):,.2f}"
    return f"${v:,.2f}"


def _fmt_delta(v: float) -> str:
    if v >= 0:
        return f"+${v:,.2f}"
    return f"-${abs(v):,.2f}"


def _fmt_int_delta(v: int) -> str:
    return f"+{v:,}" if v >= 0 else f"{v:,}"


def _fmt_bytes(v: int) -> str:
    if v >= 1024 * 1024:
        return f"{v / (1024 * 1024):.1f} MB"
    if v >= 1024:
        return f"{v / 1024:.1f} KB"
    return f"{v} B"
