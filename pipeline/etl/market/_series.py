"""Shared time-series helpers for market / macro data modules.

``fetch_dxy_monthly`` (yahoo) and ``fetch_fred_data`` (fred) both flatten a
daily pandas Series into a JSON-ready ``[{"date": "YYYY-MM", "value": float}]``
record list and round to two decimals. :func:`to_monthly_records` is the one
place where that shape is defined, so snapshots and series emitted by the two
fetchers stay byte-for-byte compatible with the Worker/frontend consumer.

:func:`forward_fill_prices_by_date` is used by the nightly projection path
(``scripts/project_networth_nightly.py`` over D1 rows) to turn a sparse
``daily_close``-style event stream into the dense ``{date: {symbol: price}}``
shape ``etl.projection.project_range`` requires.
"""

from __future__ import annotations

from datetime import date
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


def forward_fill_prices_by_date(
    rows: list[tuple[str, date, float]],
) -> dict[date, dict[str, float]]:
    """Collapse ``(symbol, date, close)`` rows into ``{date: {symbol: price}}``,
    forward-filling each symbol so every observation date carries the latest
    known close for every symbol that has ever traded on or before that date.

    Input rows do not need to be sorted — they are bucketed per symbol and
    sorted internally. Used by ``scripts/project_networth_nightly.py`` to
    convert D1's ``daily_close`` query into the dense shape
    :func:`etl.projection.project_range` consumes.
    """
    by_sym: dict[str, list[tuple[date, float]]] = {}
    all_dates: set[date] = set()
    for sym, d, close in rows:
        by_sym.setdefault(sym, []).append((d, close))
        all_dates.add(d)

    result: dict[date, dict[str, float]] = {d: {} for d in all_dates}
    sorted_dates = sorted(all_dates)
    for sym, points in by_sym.items():
        points.sort()
        carry: float | None = None
        idx = 0
        for d in sorted_dates:
            while idx < len(points) and points[idx][0] <= d:
                carry = points[idx][1]
                idx += 1
            if carry is not None:
                result[d][sym] = carry
    return result
