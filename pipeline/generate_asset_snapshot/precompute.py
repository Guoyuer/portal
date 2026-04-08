"""Pre-compute daily[] and prefix[] arrays for frontend consumption."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime

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
