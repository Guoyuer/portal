"""Pre-compute daily[] arrays for frontend consumption."""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from pathlib import Path

from .types import TRADING_DAYS_MONTH, TRADING_DAYS_YEAR

MarketIndexRow = dict[str, str | float]

# ── Market index precomputation ─────────────────────────────────────────────

log = logging.getLogger(__name__)

_INDEX_NAMES: dict[str, str] = {
    "^GSPC": "S&P 500",
    "^NDX": "NASDAQ 100",
    "VXUS": "VXUS",
    "000300.SS": "CSI 300",
}

def _compute_index_row(
    ticker: str, name: str,
    rows: list[tuple[str, float]],
) -> MarketIndexRow | None:
    """Compute market stats for a single index ticker from pre-fetched (date, close) rows.

    Returns a MarketIndexRow ready for INSERT, or None if insufficient data.
    """
    if len(rows) < 2:
        return None

    dates = [r[0] for r in rows]
    closes = [r[1] for r in rows]
    current = closes[-1]

    # Month return (~22 trading days back)
    month_idx = max(0, len(closes) - TRADING_DAYS_MONTH)
    month_return = round((current / closes[month_idx] - 1) * 100, 2)

    # YTD return (first trading day of current year)
    current_year = dates[-1][:4]
    ytd_start = next(
        (c for d, c in zip(dates, closes, strict=False) if d.startswith(current_year)),
        closes[0],
    )
    ytd_return = round((current / ytd_start - 1) * 100, 2)

    # 52-week high/low
    year_closes = closes[-TRADING_DAYS_YEAR:]
    high_52w = max(year_closes)
    low_52w = min(year_closes)

    # Sparkline: last ~1 year of closes
    sparkline = json.dumps(year_closes)

    return {
        "ticker": ticker,
        "name": name,
        "current": current,
        "month_return": month_return,
        "ytd_return": ytd_return,
        "high_52w": high_52w,
        "low_52w": low_52w,
        "sparkline": sparkline,
    }


def precompute_market(db_path: Path) -> None:
    """Precompute market index data into computed_market_indices, and macro
    series + DXY + USD/CNY into econ_series. Clears and rewrites both tables.
    """
    from .db import get_connection

    conn = get_connection(db_path)
    try:
        conn.execute("DELETE FROM computed_market_indices")
        conn.execute("DELETE FROM econ_series")

        _precompute_indices(conn)
        _precompute_fred(conn)
        _precompute_dxy(conn)
        _precompute_cny(conn)

        conn.commit()
    finally:
        conn.close()


def _precompute_indices(conn: sqlite3.Connection) -> None:
    """Batch-fetch index prices and insert computed stats into computed_market_indices."""
    index_tickers = list(_INDEX_NAMES.keys())
    placeholders = ",".join("?" for _ in index_tickers)
    index_data: dict[str, list[tuple[str, float]]] = {t: [] for t in index_tickers}
    for sym, dt, close in conn.execute(
        f"SELECT symbol, date, close FROM daily_close WHERE symbol IN ({placeholders}) ORDER BY symbol, date",
        index_tickers,
    ):
        index_data[sym].append((dt, close))

    for ticker, name in _INDEX_NAMES.items():
        row = _compute_index_row(ticker, name, index_data[ticker])
        if row is not None:
            conn.execute(
                "INSERT INTO computed_market_indices"
                " (ticker, name, current, month_return, ytd_return, high_52w, low_52w, sparkline)"
                " VALUES (:ticker, :name, :current, :month_return, :ytd_return, :high_52w, :low_52w, :sparkline)",
                row,
            )


def _precompute_cny(conn: sqlite3.Connection) -> None:
    """Append USD/CNY monthly history (last close per month) to econ_series.

    Sourced from daily_close where symbol='CNY=X'. The R2 exporter exposes the
    latest value as snapshot.usdCny on /econ.
    """
    last_per_month: dict[str, float] = {}
    for date_str, close in conn.execute(
        "SELECT date, close FROM daily_close WHERE symbol='CNY=X' ORDER BY date"
    ):
        last_per_month[date_str[:7]] = round(float(close), 4)
    if not last_per_month:
        return
    conn.executemany(
        "INSERT INTO econ_series (key, date, value) VALUES ('usdCny', ?, ?)",
        sorted(last_per_month.items()),
    )
    log.info("Stored %d USD/CNY econ_series rows", len(last_per_month))


def _precompute_fred(conn: sqlite3.Connection) -> None:
    """Fetch FRED macro series (if FRED_API_KEY set) and persist to econ_series."""
    fred_key = os.environ.get("FRED_API_KEY", "")
    if not fred_key:
        return

    from .market.fred import fetch_fred_data

    fred = fetch_fred_data(fred_key)
    if not fred or "series" not in fred:
        return

    econ_count = 0
    series: dict[str, list[dict[str, object]]] = fred["series"]  # type: ignore[assignment]
    for skey, points in series.items():
        for pt in points:
            conn.execute(
                "INSERT INTO econ_series (key, date, value) VALUES (?, ?, ?)",
                (skey, pt["date"], pt["value"]),
            )
            econ_count += 1
    log.info("Stored %d FRED econ_series rows", econ_count)


def _precompute_dxy(conn: sqlite3.Connection) -> None:
    """Fetch DXY (US Dollar Index) from Yahoo and append to econ_series."""
    from .market.yahoo import fetch_dxy_monthly

    points = fetch_dxy_monthly()
    if not points:
        return
    conn.executemany(
        "INSERT INTO econ_series (key, date, value) VALUES ('dxy', ?, ?)",
        [(pt["date"], pt["value"]) for pt in points],
    )
    log.info("Stored %d DXY econ_series rows", len(points))
