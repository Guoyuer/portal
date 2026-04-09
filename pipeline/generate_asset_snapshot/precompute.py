"""Pre-compute daily[] and prefix[] arrays for frontend consumption."""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

# ── Key mapping for snake_case → camelCase ───────────────────────────────────
_FLOW_KEY_MAP: dict[str, str] = {
    "net_cash_in": "netCashIn",
    "cc_payments": "ccPayments",
}

_ASSET_KEY_MAP: dict[str, str] = {
    "US Equity": "usEquity",
    "Non-US Equity": "nonUsEquity",
    "Crypto": "crypto",
    "Safe Net": "safeNet",
}


_FLOW_FIELDS = ("income", "expenses", "buys", "sells", "dividends", "net_cash_in", "cc_payments")


def _empty_flow() -> dict[str, float]:
    return {k: 0.0 for k in _FLOW_FIELDS}


def build_daily_flows(
    fidelity_txns: list[dict[str, object]],
    qianji_records: list[dict[str, object]],
    start_iso: str,
    end_iso: str,
) -> list[dict[str, object]]:
    """Aggregate Fidelity + Qianji transactions into per-day flow buckets.

    Only dates within [start_iso, end_iso] (inclusive) are included.
    Returns a sorted list of dicts, one per date that has any activity.
    """
    start = date.fromisoformat(start_iso)
    end = date.fromisoformat(end_iso)
    buckets: dict[date, dict[str, float]] = defaultdict(_empty_flow)

    # ── Fidelity transactions ───────────────────────────────────────────────
    for txn in fidelity_txns:
        dt = datetime.strptime(str(txn["date"]), "%m/%d/%Y").date()
        if dt < start or dt > end:
            continue
        action = txn["action_type"]
        amount = float(txn["amount"])  # type: ignore[arg-type]
        bucket = buckets[dt]
        if action == "buy":
            bucket["buys"] += abs(amount)
        elif action == "sell":
            bucket["sells"] += amount
        elif action == "dividend":
            bucket["dividends"] += amount
        elif action == "reinvestment":
            bucket["dividends"] += amount
            bucket["buys"] += abs(amount)
        elif action in ("deposit", "withdrawal"):
            bucket["net_cash_in"] += amount

    # ── Qianji records ──────────────────────────────────────────────────────
    for rec in qianji_records:
        dt = datetime.strptime(str(rec["date"])[:10], "%Y-%m-%d").date()
        if dt < start or dt > end:
            continue
        rec_type = rec["type"]
        amount = float(rec["amount"])  # type: ignore[arg-type]
        bucket = buckets[dt]
        if rec_type == "income":
            bucket["income"] += amount
        elif rec_type == "expense":
            bucket["expenses"] += amount
        elif rec_type == "repayment":
            bucket["cc_payments"] += amount

    # ── Sort by date and emit ────────────────────────────────────────────────
    result: list[dict[str, object]] = []
    for dt in sorted(buckets):
        entry: dict[str, object] = {"date": dt, **buckets[dt]}
        result.append(entry)
    return result


def compute_daily_series(
    snapshots: dict[date, dict[str, float]],
) -> list[dict[str, object]]:
    """Convert {date: {group: value}} → sorted list with camelCase keys."""
    result: list[dict[str, object]] = []
    for dt in sorted(snapshots):
        row = snapshots[dt]
        entry: dict[str, object] = {"date": dt.isoformat()}
        entry["total"] = round(row["total"], 2)
        for src_key, dst_key in _ASSET_KEY_MAP.items():
            entry[dst_key] = round(row[src_key], 2)
        result.append(entry)
    return result


def compute_prefix_sums(
    daily_flows: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Accumulate daily flow values into cumulative prefix sums with camelCase keys."""
    if not daily_flows:
        return []

    cumulative: dict[str, float] = {}
    result: list[dict[str, object]] = []

    for row in daily_flows:
        entry: dict[str, object] = {}
        for key, value in row.items():
            if key == "date":
                entry["date"] = value.isoformat() if isinstance(value, date) else value
                continue
            out_key = _FLOW_KEY_MAP.get(key, key)
            prev = cumulative.get(out_key, 0.0)
            cumulative[out_key] = prev + float(value)  # type: ignore[arg-type]
            entry[out_key] = round(cumulative[out_key], 2)
        result.append(entry)

    return result


# ── Market index precomputation ─────────────────────────────────────────────

log = logging.getLogger(__name__)

_INDEX_NAMES: dict[str, str] = {
    "^GSPC": "S&P 500",
    "^NDX": "NASDAQ 100",
    "VXUS": "VXUS",
    "000300.SS": "CSI 300",
}

_FRED_SNAPSHOT_KEYS: dict[str, str] = {
    "fedFundsRate": "__fedRate",
    "treasury10y": "__treasury10y",
    "cpiYoy": "__cpi",
    "unemployment": "__unemployment",
    "vix": "__vix",
}


def _compute_index_row(
    conn: sqlite3.Connection, ticker: str, name: str,
) -> tuple[str, str, float, float, float, float, float, str] | None:
    """Compute market stats for a single index ticker from daily_close.

    Returns a tuple ready for INSERT, or None if insufficient data.
    """
    rows = conn.execute(
        "SELECT date, close FROM daily_close WHERE symbol = ? ORDER BY date",
        (ticker,),
    ).fetchall()
    if len(rows) < 2:
        return None

    dates = [r[0] for r in rows]
    closes = [r[1] for r in rows]
    current = closes[-1]

    # Month return (~22 trading days back)
    month_idx = max(0, len(closes) - 23)
    month_return = round((current / closes[month_idx] - 1) * 100, 2)

    # YTD return (first trading day of current year)
    current_year = dates[-1][:4]
    ytd_start = next(
        (c for d, c in zip(dates, closes, strict=False) if d.startswith(current_year)),
        closes[0],
    )
    ytd_return = round((current / ytd_start - 1) * 100, 2)

    # 52-week high/low (~252 trading days)
    year_closes = closes[-252:]
    high_52w = max(year_closes)
    low_52w = min(year_closes)

    # Sparkline: last 252 closes
    sparkline = json.dumps(year_closes)

    return (ticker, name, current, month_return, ytd_return, high_52w, low_52w, sparkline)


def precompute_market(db_path: Path) -> None:
    """Precompute market index data and macro scalars into computed_market.

    Reads daily_close prices, computes returns/sparklines for each index,
    and stores CNY rate + optional FRED data as ``__``-prefixed scalar rows.
    Clears and rewrites computed_market each invocation.
    """
    from .db import get_connection

    conn = get_connection(db_path)
    try:
        conn.execute("DELETE FROM computed_market")

        # ── Index rows ──────────────────────────────────────────────────
        for ticker, name in _INDEX_NAMES.items():
            row = _compute_index_row(conn, ticker, name)
            if row is not None:
                conn.execute(
                    "INSERT INTO computed_market"
                    " (ticker, name, current, month_return, ytd_return, high_52w, low_52w, sparkline)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    row,
                )

        # ── CNY rate ────────────────────────────────────────────────────
        cny_row = conn.execute(
            "SELECT close FROM daily_close WHERE symbol='CNY=X' ORDER BY date DESC LIMIT 1"
        ).fetchone()
        if cny_row is not None:
            conn.execute(
                "INSERT INTO computed_market (ticker, name, current) VALUES (?, ?, ?)",
                ("__usdCny", "USD/CNY", cny_row[0]),
            )

        # ── FRED macro data ─────────────────────────────────────────────
        fred_key = os.environ.get("FRED_API_KEY", "")
        if fred_key:
            try:
                from .market.fred import fetch_fred_data

                fred = fetch_fred_data(fred_key)
                if fred and "snapshot" in fred:
                    snap: dict[str, object] = fred["snapshot"]  # type: ignore[assignment]
                    for src, dst in _FRED_SNAPSHOT_KEYS.items():
                        if src in snap:
                            conn.execute(
                                "INSERT INTO computed_market (ticker, name, current) VALUES (?, ?, ?)",
                                (dst, src, float(snap[src])),  # type: ignore[arg-type]
                            )
            except Exception:  # noqa: BLE001
                log.warning("Failed to fetch FRED data for precompute_market", exc_info=True)

        conn.commit()
    finally:
        conn.close()
