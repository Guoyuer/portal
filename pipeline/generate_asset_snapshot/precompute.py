"""Pre-compute daily[] and prefix[] arrays for frontend consumption."""
from __future__ import annotations

from datetime import date

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
