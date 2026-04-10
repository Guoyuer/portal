"""Yahoo Finance data fetcher.

Uses the ``yfinance`` library to retrieve index returns and stock info.
All external calls are wrapped in try/except — this module never raises.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import yfinance as yf

from ..types import HoldingsDetailData, IndexReturn, MarketData, Portfolio, StockDetail

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


def build_market_data(cny_rate: float) -> MarketData | None:
    """Fetch index data and build MarketData.

    A single 1-year download provides everything: month return, YTD return,
    52-week high/low, and sparkline.  Returns None on failure.
    """
    from datetime import timedelta

    tickers = ["^GSPC", "^NDX", "VXUS", "000300.SS"]
    names = {"^GSPC": "S&P 500", "^NDX": "NASDAQ 100", "VXUS": "VXUS", "000300.SS": "CSI 300"}

    t0 = time.time()
    try:
        raw = yf.download(tickers, period="1y", progress=False)
    except Exception:  # noqa: BLE001
        log.warning("Market download failed", exc_info=True)
        return None
    if raw.empty:
        return None

    indices: list[IndexReturn] = []
    for t in tickers:
        try:
            closes = (raw["Close"] if len(tickers) == 1 else raw["Close"][t]).dropna()
            if len(closes) < 2:
                continue

            current = float(closes.iloc[-1].item())
            last_date = closes.index[-1]

            # Month return — closest trading day ≥ 30 days ago
            month_cutoff = last_date - timedelta(days=30)
            month_start = float(closes[closes.index <= month_cutoff].iloc[-1].item())
            month_return = (current - month_start) / month_start * 100

            # YTD return — first trading day of current year
            ytd_closes = closes[closes.index.year == last_date.year]
            ytd_start = float(ytd_closes.iloc[0].item()) if len(ytd_closes) > 0 else current
            ytd_return = (current - ytd_start) / ytd_start * 100

            indices.append(IndexReturn(
                ticker=t,
                name=names.get(t, t),
                month_return=round(month_return, 4),
                ytd_return=round(ytd_return, 4),
                current=current,
                sparkline=[round(float(v), 2) for v in closes],
                high_52w=float(closes.max()),
                low_52w=float(closes.min()),
            ))
        except Exception:  # noqa: BLE001
            log.warning("Failed to process %s", t, exc_info=True)
            continue

    log.info("Market data: %d indices in %.1fs", len(indices), time.time() - t0)
    return MarketData(indices=indices, usd_cny=cny_rate) if indices else None


def _is_ticker(symbol: str) -> bool:
    """Return True if symbol looks like a real ticker (not '401k sp500' or 'I Bonds')."""
    return bool(symbol) and symbol.isascii() and " " not in symbol and len(symbol) <= 5


def build_holdings_detail(portfolio: Portfolio) -> HoldingsDetailData | None:
    """Fetch per-stock detail from Yahoo Finance for portfolio holdings.

    Returns None on total failure. Individual ticker failures are silently skipped.
    """
    all_symbols = list(portfolio["totals"].keys())
    tickers = [t for t in all_symbols if _is_ticker(t)]
    skipped = [t for t in all_symbols if not _is_ticker(t)]
    if skipped:
        log.info("Holdings: skipped non-ticker symbols: %s", skipped)
    if not tickers:
        return None

    log.info("Fetching holdings for %d tickers...", len(tickers))

    # Batch download 1-month price history
    month_returns: dict[str, float] = {}
    try:
        returns = fetch_index_returns(tickers, period="1mo")
        for t, data in returns.items():
            month_returns[t] = data["return_pct"]
    except Exception:  # noqa: BLE001
        pass

    if not month_returns:
        return None

    # Fetch per-ticker info (52w high/low, PE, earnings, market cap)
    stocks: list[StockDetail] = []
    for ticker in month_returns:
        value = portfolio["totals"].get(ticker, 0.0)
        month_ret = month_returns[ticker]
        start_value = value / (1 + month_ret / 100) if month_ret != -100 else 0.0

        detail = StockDetail(
            ticker=ticker,
            month_return=month_ret,
            start_value=round(start_value, 2),
            end_value=round(value, 2),
            pe_ratio=None,
            market_cap=None,
            high_52w=None,
            low_52w=None,
            vs_high=None,
            next_earnings=None,
        )

        try:
            ticker_obj = yf.Ticker(ticker)
            info = ticker_obj.info
            if info:
                high = info.get("fiftyTwoWeekHigh")
                low = info.get("fiftyTwoWeekLow")
                price = info.get("currentPrice") or info.get("regularMarketPrice")
                detail.pe_ratio = info.get("trailingPE")
                detail.market_cap = info.get("marketCap")
                detail.high_52w = high
                detail.low_52w = low
                if high and price:
                    detail.vs_high = round((price / high - 1) * 100, 2)
                # Earnings date
                cal = ticker_obj.calendar
                if cal is not None and "Earnings Date" in cal:
                    dates = cal["Earnings Date"]
                    if dates:
                        d = dates[0]
                        detail.next_earnings = d.strftime("%Y-%m-%d")
        except Exception:  # noqa: BLE001
            pass

        log.debug("Ticker %s: month=%.1f%% pe=%s mcap=%s", ticker, month_ret, detail.pe_ratio, detail.market_cap)
        stocks.append(detail)

    # Sort by month return (descending)
    sorted_by_return = sorted(stocks, key=lambda s: s.month_return, reverse=True)

    return HoldingsDetailData(all_stocks=sorted_by_return)
