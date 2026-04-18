"""Yahoo Finance data fetcher.

Uses the ``yfinance`` library to retrieve index returns and stock info.
All external calls are wrapped in try/except — this module never raises.
"""

from __future__ import annotations

import logging
from typing import Any

import yfinance as yf

from ._series import to_monthly_records
from ._yfinance import extract_close

log = logging.getLogger(__name__)

_DXY_TICKER = "DX-Y.NYB"
_DXY_LOOKBACK = "5y"


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


