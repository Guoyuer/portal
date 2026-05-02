"""Shared time-series helpers for market / macro data modules.

``fetch_dxy_monthly`` (yahoo) and ``fetch_fred_data`` (fred) both flatten a
daily pandas Series into a JSON-ready ``[{"date": "YYYY-MM", "value": float}]``
record list and round to two decimals. :func:`to_monthly_records` is the one
place where that shape is defined, so snapshots and series emitted by the two
fetchers stay byte-for-byte compatible with the Worker/frontend consumer.
"""

from __future__ import annotations

from typing import Any

import pandas as pd


def to_monthly_records(series: pd.Series) -> list[dict[str, Any]]:
    """Flatten a pandas Series to ``[{"date": "YYYY-MM", "value": rounded}]``.

    Entries with NaN values are skipped. The input may be either already
    month-end-resampled or raw daily data; callers that need month-end
    semantics should pre-resample via
    :func:`resample_daily_to_monthly` so ``monthly.iloc[-1]`` stays
    available for the snapshot field.
    """
    records: list[dict[str, Any]] = []
    for dt, val in series.items():
        if pd.notna(val):
            records.append(
                {"date": pd.Timestamp(dt).strftime("%Y-%m"), "value": round(float(val), 2)}
            )
    return records


def resample_daily_to_monthly(series: pd.Series) -> pd.Series:
    """Resample a daily series to month-end, keeping the last valid observation.

    Returns an empty series when input is empty or entirely NaN.
    """
    series = series.dropna()
    if series.empty:
        return series
    return series.resample("ME").last().dropna()
