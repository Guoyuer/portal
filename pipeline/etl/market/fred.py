"""FRED (Federal Reserve Economic Data) fetcher.

Uses the ``fredapi`` library to retrieve macro-economic time series.
All external calls are wrapped in try/except — this module never raises.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

import pandas as pd
from fredapi import Fred

from ._series import resample_daily_to_monthly, to_monthly_records

log = logging.getLogger(__name__)

# ── Series configuration ────────────────────────────────────────────────────

# Daily series: resampled to month-end
_DAILY_SERIES: dict[str, str] = {
    "DFF": "fedFundsRate",
    "DGS10": "treasury10y",
    "DGS2": "treasury2y",
    "VIXCLS": "vix",
    "DCOILWTICO": "oilWti",
}

# Monthly series: used as-is
_MONTHLY_SERIES: dict[str, str] = {
    "UNRATE": "unemployment",
}

# CPI series: converted from raw index to YoY % change
_CPI_SERIES: dict[str, str] = {
    "CPIAUCSL": "cpiYoy",
    "CPILFESL": "coreCpiYoy",
}

_LOOKBACK_YEARS = 5
_CPI_EXTRA_MONTHS = 13  # 12 for YoY + 1 buffer


# ── Helpers ──────────────────────────────────────────────────────────────────


def _compute_yoy_pct(series: pd.Series) -> pd.Series:
    """Convert a raw CPI index series to year-over-year % change."""
    series = series.dropna()
    if len(series) < 13:
        return pd.Series(dtype=float)
    yoy = series.pct_change(periods=12) * 100
    return yoy.dropna()


def _compute_spread_2s10s(
    snapshot: dict[str, float], series: dict[str, list[dict[str, Any]]],
) -> None:
    """Derive the 2s10s spread from already-fetched ``treasury10y`` /
    ``treasury2y`` series, writing into ``snapshot`` / ``series`` in place.
    No-op when either input leg is missing (leg fetch errored)."""
    if "treasury10y" not in series or "treasury2y" not in series:
        return
    t10_map = {e["date"]: e["value"] for e in series["treasury10y"]}
    t2_map = {e["date"]: e["value"] for e in series["treasury2y"]}
    common_dates = sorted(set(t10_map) & set(t2_map))
    series["spread2s10s"] = [
        {"date": d, "value": round(t10_map[d] - t2_map[d], 2)} for d in common_dates
    ]
    if "treasury10y" in snapshot and "treasury2y" in snapshot:
        snapshot["spread2s10s"] = round(snapshot["treasury10y"] - snapshot["treasury2y"], 2)


# ── Main fetcher ─────────────────────────────────────────────────────────────


def fetch_fred_data(api_key: str) -> dict[str, object] | None:
    """Fetch macro-economic data from FRED.

    Parameters
    ----------
    api_key:
        FRED API key. Empty string returns ``None`` immediately.

    Returns
    -------
    dict | None
        ``{"snapshot": {camelCase: float}, "series": {camelCase: [{date, value}]}}``
        Returns ``None`` on total failure.
    """
    if not api_key:
        log.warning("FRED API key is empty — skipping")
        return None

    try:
        fred = Fred(api_key=api_key)
    except Exception:
        log.warning("Failed to initialize FRED client", exc_info=True)
        return None

    end = datetime.now()
    start = end - timedelta(days=_LOOKBACK_YEARS * 365)
    cpi_start = start - timedelta(days=_CPI_EXTRA_MONTHS * 31)

    snapshot: dict[str, float] = {}
    series: dict[str, list[dict[str, Any]]] = {}

    # ── Daily series (resample to monthly) ───────────────────────────────
    for fred_id, key in _DAILY_SERIES.items():
        try:
            raw = fred.get_series(fred_id, observation_start=start.strftime("%Y-%m-%d"))
            monthly = resample_daily_to_monthly(raw)
            if monthly.empty:
                log.warning("FRED %s: empty after resample", fred_id)
                continue
            snapshot[key] = round(float(monthly.iloc[-1]), 2)
            series[key] = to_monthly_records(monthly)
        except Exception:
            log.warning("FRED %s fetch failed", fred_id, exc_info=True)

    _compute_spread_2s10s(snapshot, series)

    # ── Monthly series (use as-is) ───────────────────────────────────────
    for fred_id, key in _MONTHLY_SERIES.items():
        try:
            raw = fred.get_series(fred_id, observation_start=start.strftime("%Y-%m-%d"))
            raw = raw.dropna()
            if raw.empty:
                log.warning("FRED %s: no data", fred_id)
                continue
            snapshot[key] = round(float(raw.iloc[-1]), 2)
            series[key] = to_monthly_records(raw)
        except Exception:
            log.warning("FRED %s fetch failed", fred_id, exc_info=True)

    # ── CPI series (raw index → YoY %) ──────────────────────────────────
    for fred_id, key in _CPI_SERIES.items():
        try:
            raw = fred.get_series(fred_id, observation_start=cpi_start.strftime("%Y-%m-%d"))
            yoy = _compute_yoy_pct(raw)
            if yoy.empty:
                log.warning("FRED %s: insufficient data for YoY", fred_id)
                continue
            # Trim to the original lookback window (drop the extra lookback months)
            yoy = yoy[yoy.index >= pd.Timestamp(start)]
            snapshot[key] = round(float(yoy.iloc[-1]), 2)
            series[key] = to_monthly_records(yoy)
        except Exception:
            log.warning("FRED %s fetch failed", fred_id, exc_info=True)

    if not snapshot and not series:
        log.warning("FRED: all series failed — returning None")
        return None

    log.info("FRED: fetched %d snapshot values, %d series", len(snapshot), len(series))
    return {"snapshot": snapshot, "series": series}
