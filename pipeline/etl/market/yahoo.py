"""Yahoo Finance data fetcher.

Uses the ``yfinance`` library to retrieve index returns and stock info.
All external calls are wrapped in try/except — this module never raises.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import yfinance as yf

log = logging.getLogger(__name__)


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
            except Exception:  # noqa: BLE001
                continue

        log.info("Index returns (%s, %s): %s in %.1fs", period, tickers, list(result.keys()), time.time() - t0)
        return result
    except Exception:  # noqa: BLE001
        return {}


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


