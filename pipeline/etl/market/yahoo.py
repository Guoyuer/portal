"""Yahoo Finance data fetcher.

Uses the ``yfinance`` library to retrieve index returns and stock info.
All external calls are wrapped in try/except — this module never raises.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import yfinance as yf

from ._series import to_monthly_records
from ._yfinance import extract_close

log = logging.getLogger(__name__)

_DXY_TICKER = "DX-Y.NYB"
_DXY_LOOKBACK = "5y"


def fetch_index_returns(tickers: list[str], period: str = "1mo") -> dict[str, Any]:
    """Return period return data for the given tickers.

    Returns
    -------
    dict
        ``{ticker: {"return_pct": float, "current": float, "previous": float}}``
        Empty dict on failure or when *tickers* is empty.
    """
    if not tickers:
        return {}

    t0 = time.time()
    try:
        data = yf.download(tickers, period=period, progress=False)

        if data.empty:
            log.info("Index returns: no data for %s (%s)", tickers, period)
            return {}

        result: dict[str, Any] = {}
        for ticker in tickers:
            try:
                if len(tickers) == 1:
                    closes = data["Close"]
                else:
                    closes = data["Close"][ticker]

                closes = closes.dropna()
                if len(closes) < 2:
                    continue

                previous = float(closes.iloc[0].item())
                current = float(closes.iloc[-1].item())
                return_pct = (current - previous) / previous * 100

                result[ticker] = {
                    "return_pct": round(return_pct, 4),
                    "current": current,
                    "previous": previous,
                }
            except Exception as e:  # noqa: BLE001 — per-ticker parse is best-effort; keep others
                log.warning("Index return for %s failed: %s", ticker, e)
                continue

        log.info("Index returns (%s, %s): %s in %.1fs", period, tickers, list(result.keys()), time.time() - t0)
        return result
    except Exception as e:  # noqa: BLE001 — batch yfinance call; degrade to empty but be loud
        log.warning("Index returns batch fetch failed (%s): %s", tickers, e)
        return {}


def fetch_dxy_monthly() -> list[dict[str, Any]]:
    """Fetch DXY (US Dollar Index) month-end close history from Yahoo (DX-Y.NYB).

    Returns ``[{"date": "YYYY-MM", "value": float}, ...]`` for ~5 years, or an
    empty list on failure / no data. The shape matches the per-key entries
    that ``fetch_fred_data`` produces for ``econ_series`` — so consumers can
    insert directly without a separate adapter.
    """
    try:
        data = yf.download(_DXY_TICKER, period=_DXY_LOOKBACK, progress=False)
        if data.empty:
            log.info("DXY: no data returned")
            return []

        close_df = extract_close(data, [_DXY_TICKER])
        if close_df.empty:
            return []
        closes = close_df.iloc[:, 0].dropna()
        if closes.empty:
            return []

        monthly = closes.resample("ME").last().dropna()
        records = to_monthly_records(monthly)
        log.info("DXY: %d monthly observations", len(records))
        return records
    except Exception as e:  # noqa: BLE001
        log.warning("DXY fetch failed: %s", e)
        return []


def fetch_cny_rate() -> float:
    """Fetch current USD/CNY exchange rate. Raises on failure or bad data."""
    data = yf.download("CNY=X", period="5d", progress=False)
    if data.empty:
        raise RuntimeError("Failed to fetch USD/CNY rate: no data returned")
    rate = float(data["Close"].iloc[-1].item())
    if not 3.0 <= rate <= 10.0:
        raise RuntimeError(f"USD/CNY rate {rate:.4f} outside plausible range [3.0, 10.0]")
    log.info("USD/CNY: %.4f", rate)
    return rate


