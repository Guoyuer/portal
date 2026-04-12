"""Pre-compute daily[] arrays for frontend consumption."""
from __future__ import annotations

import json
import logging
import os
from datetime import date
from pathlib import Path

from .types import TRADING_DAYS_MONTH, TRADING_DAYS_YEAR

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



# ── Market index precomputation ─────────────────────────────────────────────

log = logging.getLogger(__name__)

_INDEX_NAMES: dict[str, str] = {
    "^GSPC": "S&P 500",
    "^NDX": "NASDAQ 100",
    "VXUS": "VXUS",
    "000300.SS": "CSI 300",
}

_FRED_SNAPSHOT_KEYS: dict[str, str] = {
    "fedFundsRate": "fedRate",
    "treasury10y": "treasury10y",
    "cpiYoy": "cpi",
    "unemployment": "unemployment",
    "vix": "vix",
}


def _compute_index_row(
    ticker: str, name: str,
    rows: list[tuple[str, float]],
) -> tuple[str, str, float, float, float, float, float, str] | None:
    """Compute market stats for a single index ticker from pre-fetched (date, close) rows.

    Returns a tuple ready for INSERT, or None if insufficient data.
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

    return (ticker, name, current, month_return, ytd_return, high_52w, low_52w, sparkline)


def precompute_market(db_path: Path) -> None:
    """Precompute market index data and macro scalars into separate tables.

    Reads daily_close prices, computes returns/sparklines for each index
    into computed_market_indices, and stores CNY rate + optional FRED data
    into computed_market_indicators.
    Clears and rewrites both tables each invocation.
    """
    from .db import get_connection

    conn = get_connection(db_path)
    try:
        conn.execute("DELETE FROM computed_market_indices")
        conn.execute("DELETE FROM computed_market_indicators")

        # ── Index rows (batch-fetch all index prices) ─────────────────
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
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    row,
                )

        # ── CNY rate ────────────────────────────────────────────────────
        cny_row = conn.execute(
            "SELECT close FROM daily_close WHERE symbol='CNY=X' ORDER BY date DESC LIMIT 1"
        ).fetchone()
        if cny_row is not None:
            conn.execute(
                "INSERT INTO computed_market_indicators (key, value) VALUES (?, ?)",
                ("usdCny", cny_row[0]),
            )

        # ── FRED macro data (fetch_fred_data returns None on API error) ──
        fred_key = os.environ.get("FRED_API_KEY", "")
        if fred_key:
            from .market.fred import fetch_fred_data

            fred = fetch_fred_data(fred_key)
            if fred and "snapshot" in fred:
                snap: dict[str, object] = fred["snapshot"]  # type: ignore[assignment]
                for src, dst in _FRED_SNAPSHOT_KEYS.items():
                    if src in snap:
                        conn.execute(
                            "INSERT INTO computed_market_indicators (key, value) VALUES (?, ?)",
                            (dst, float(snap[src])),  # type: ignore[arg-type]
                        )
            if fred and "series" in fred:
                conn.execute("DELETE FROM econ_series")
                econ_count = 0
                series: dict[str, list[dict[str, object]]] = fred["series"]  # type: ignore[assignment]
                for skey, points in series.items():
                    for pt in points:
                        conn.execute(
                            "INSERT INTO econ_series (key, date, value) VALUES (?, ?, ?)",
                            (skey, pt["date"], pt["value"]),
                        )
                        econ_count += 1
                log.info("Stored %d econ_series rows", econ_count)

        conn.commit()
    finally:
        conn.close()


# ── Holdings detail precomputation ─────────────────────────────────────────


def precompute_holdings_detail(db_path: Path) -> None:
    """Precompute per-ticker performance data into computed_holdings_detail.

    Reads computed_daily_tickers for the latest date, filters to "real" tickers
    (ASCII, no spaces, <=5 chars), then computes month return, start/end value,
    52-week high/low, and vs_high from daily_close prices.
    Clears and rewrites the table each invocation.
    """
    from .db import get_connection

    conn = get_connection(db_path)
    try:
        conn.execute("DELETE FROM computed_holdings_detail")

        # Latest date in computed_daily_tickers
        row = conn.execute("SELECT date FROM computed_daily_tickers ORDER BY date DESC LIMIT 1").fetchone()
        if row is None:
            conn.commit()
            return
        latest_date: str = row[0]

        # Tickers with value > 0 on that date
        ticker_rows = conn.execute(
            "SELECT ticker, value FROM computed_daily_tickers WHERE date = ? AND value > 0",
            (latest_date,),
        ).fetchall()

        # Filter to real tickers: ASCII, no spaces, <=5 chars
        real_tickers: dict[str, float] = {
            t: v for t, v in ticker_rows if t.isascii() and " " not in t and len(t) <= 5
        }
        if not real_tickers:
            conn.commit()
            return

        # Batch-fetch all prices in one query
        placeholders = ",".join("?" for _ in real_tickers)
        all_prices: dict[str, list[float]] = {t: [] for t in real_tickers}
        for sym, close in conn.execute(
            f"SELECT symbol, close FROM daily_close WHERE symbol IN ({placeholders}) ORDER BY symbol, date",
            list(real_tickers.keys()),
        ):
            all_prices[sym].append(close)

        # Compute per-ticker stats
        insert_rows: list[tuple[str, float, float, float, float, float, float]] = []
        for ticker, value in real_tickers.items():
            prices = all_prices[ticker]
            if len(prices) < 2:
                continue
            current = prices[-1]

            # Month return (~22 trading days)
            month_idx = max(0, len(prices) - TRADING_DAYS_MONTH)
            month_ret = round((current / prices[month_idx] - 1) * 100, 2)
            start_value = round(value / (1 + month_ret / 100), 2) if month_ret != -100 else 0.0

            # 52-week high/low
            year_prices = prices[-TRADING_DAYS_YEAR:]
            high = max(year_prices)
            low = min(year_prices)
            vs_high = round((current / high - 1) * 100, 2)

            insert_rows.append((ticker, month_ret, start_value, round(value, 2), high, low, vs_high))

        if insert_rows:
            conn.executemany(
                "INSERT INTO computed_holdings_detail"
                " (ticker, month_return, start_value, end_value, high_52w, low_52w, vs_high)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                insert_rows,
            )

        conn.commit()
    finally:
        conn.close()
