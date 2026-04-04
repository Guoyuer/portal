"""FRED (Federal Reserve Economic Data) fetcher.

Uses the ``fredapi`` library to retrieve economic time-series data.
All external calls are wrapped in try/except — this module never raises.
"""

from __future__ import annotations

from typing import Any

from fredapi import Fred


def fetch_fred_series(series_ids: list[str], api_key: str) -> dict[str, Any]:
    """Return the latest observation for each FRED series.

    Parameters
    ----------
    series_ids:
        FRED series identifiers (e.g. ``["GS10", "CPIAUCSL", "UNRATE"]``).
    api_key:
        FRED API key.

    Returns
    -------
    dict
        ``{series_id: {"value": float, "date": str}}``
        Empty dict on failure or when *series_ids* is empty.
    """
    if not series_ids:
        return {}

    try:
        client = Fred(api_key=api_key)
        result: dict[str, Any] = {}

        for sid in series_ids:
            try:
                series = client.get_series(sid)
                series = series.dropna()
                if series.empty:
                    continue
                result[sid] = {
                    "value": float(series.iloc[-1]),
                    "date": str(series.index[-1].date()),
                }
            except Exception:  # noqa: BLE001
                continue

        return result
    except Exception:  # noqa: BLE001
        return {}
