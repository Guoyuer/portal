"""Yahoo Finance data fetcher.

Uses the ``yfinance`` library to retrieve index returns and stock info.
All external calls are wrapped in try/except — this module never raises.
"""

from __future__ import annotations

from typing import Any

import yfinance as yf

from ..types import DEFAULT_CNY_RATE, IndexReturn, MarketData


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

    try:
        data = yf.download(tickers, period=period, progress=False)

        if data.empty:
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

                previous = float(closes.iloc[0])
                current = float(closes.iloc[-1])
                return_pct = (current - previous) / previous * 100

                result[ticker] = {
                    "return_pct": round(return_pct, 4),
                    "current": current,
                    "previous": previous,
                }
            except Exception:  # noqa: BLE001
                continue

        return result
    except Exception:  # noqa: BLE001
        return {}


def fetch_cny_rate(fallback: float = DEFAULT_CNY_RATE) -> float:
    """Fetch current USD/CNY exchange rate. Returns fallback on failure."""
    try:
        data = yf.download("CNY=X", period="1d", progress=False)
        if not data.empty:
            return float(data["Close"].iloc[-1])
    except Exception:  # noqa: BLE001
        pass
    return fallback


def build_market_data(cny_rate: float = DEFAULT_CNY_RATE) -> MarketData | None:
    """Fetch index returns and build MarketData. Returns None on failure."""
    tickers = ["SPY", "QQQ", "VT"]
    idx_month = fetch_index_returns(tickers, period="1mo")
    idx_ytd = fetch_index_returns(tickers, period="ytd")
    if not idx_month:
        return None

    names = {"SPY": "S&P 500", "QQQ": "NASDAQ 100", "VT": "Total World"}
    indices = [
        IndexReturn(
            ticker=t,
            name=names.get(t, t),
            month_return=idx_month[t]["return_pct"],
            ytd_return=idx_ytd.get(t, {}).get("return_pct", 0),
            current=idx_month[t]["current"],
        )
        for t in idx_month
    ]
    return MarketData(indices=indices, usd_cny=cny_rate)
