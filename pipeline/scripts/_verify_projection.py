"""Verify etl.projection accuracy against real local DB.

Picks a past date ``N``, replays the projection from ``N - lookback`` using
only prices, then diffs the projected computed_daily rows against the
authoritative rows the local pipeline already wrote to the DB.

A small diff is expected (projection assumes no new transactions; real
window may include contribs/spending). Big diffs signal a bug.
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import date
from pathlib import Path

_PIPELINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PIPELINE))

from etl.projection import ProjectedDay, TickerRow, project_range  # noqa: E402

DB = _PIPELINE / "data" / "timemachine.db"


def _load_tickers(conn: sqlite3.Connection, d: date) -> list[TickerRow]:
    rows = conn.execute(
        "SELECT ticker, value, category, subtype, cost_basis"
        " FROM computed_daily_tickers WHERE date = ?",
        (d.isoformat(),),
    ).fetchall()
    return [TickerRow(t, v, c, s, cb) for t, v, c, s, cb in rows]


def _load_prices_by_date(conn: sqlite3.Connection) -> dict[date, dict[str, float]]:
    """Build ffill'd {date: {symbol: price}} from daily_close."""
    rows = conn.execute(
        "SELECT symbol, date, close FROM daily_close ORDER BY symbol, date"
    ).fetchall()
    # per-symbol list, then ffill into per-date dict
    by_sym: dict[str, list[tuple[date, float]]] = {}
    all_dates: set[date] = set()
    for sym, d, close in rows:
        dd = date.fromisoformat(d)
        by_sym.setdefault(sym, []).append((dd, close))
        all_dates.add(dd)
    result: dict[date, dict[str, float]] = {d: {} for d in all_dates}
    for sym, points in by_sym.items():
        points.sort()
        carry = None
        prev_idx = 0
        for d in sorted(all_dates):
            while prev_idx < len(points) and points[prev_idx][0] <= d:
                carry = points[prev_idx][1]
                prev_idx += 1
            if carry is not None:
                result[d][sym] = carry
    return result


def _load_actual(conn: sqlite3.Connection, d: date) -> dict[str, float]:
    row = conn.execute(
        "SELECT total, us_equity, non_us_equity, crypto, safe_net, liabilities"
        " FROM computed_daily WHERE date = ?",
        (d.isoformat(),),
    ).fetchone()
    if not row:
        return {}
    return {
        "total": row[0], "us_equity": row[1], "non_us_equity": row[2],
        "crypto": row[3], "safe_net": row[4], "liabilities": row[5],
    }


def _diff(proj: ProjectedDay, actual: dict[str, float]) -> dict[str, tuple[float, float, float]]:
    """Return {field: (proj, actual, abs_err_pct)}."""
    out = {}
    for f in ("total", "us_equity", "non_us_equity", "crypto", "safe_net", "liabilities"):
        p = getattr(proj, f)
        a = actual.get(f, 0.0)
        denom = abs(a) if abs(a) > 1 else 1.0
        err_pct = abs(p - a) / denom * 100
        out[f] = (p, a, err_pct)
    return out


def main(start_iso: str, end_iso: str) -> None:
    start = date.fromisoformat(start_iso)
    end = date.fromisoformat(end_iso)

    conn = sqlite3.connect(str(DB))
    print(f"Seed state = {start}; project forward through {end}")

    initial = _load_tickers(conn, start)
    print(f"  initial tickers: {len(initial)}")
    prices = _load_prices_by_date(conn)
    print(f"  price dates loaded: {len(prices)}")

    projected = project_range(initial, start, end, prices)
    print(f"  projected {len(projected)} weekdays")

    print()
    print(f"{'date':<12}{'field':<16}{'projected':>15}{'actual':>15}{'abs err %':>12}")
    print("-" * 70)
    for p in projected:
        actual = _load_actual(conn, p.date)
        if not actual:
            print(f"{p.date.isoformat():<12}  (no actual row in DB)")
            continue
        diffs = _diff(p, actual)
        for f, (pv, av, err) in diffs.items():
            flag = "  !" if err > 0.5 else ""
            print(f"{p.date.isoformat():<12}{f:<16}{pv:>15,.2f}{av:>15,.2f}{err:>11.3f}%{flag}")
        print()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        # Default: project the last trading week
        start, end = "2026-04-06", "2026-04-13"
    else:
        start, end = sys.argv[1], sys.argv[2]
    main(start, end)
