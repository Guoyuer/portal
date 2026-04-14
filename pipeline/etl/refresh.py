"""Single source of truth for the fetch/write refresh-window boundary.

A ``daily_close`` or ``computed_daily`` row is considered *mutable* (may be
overwritten on the next pipeline run to pick up late yfinance corrections or
recomputed values from updated prices) for exactly ``REFRESH_WINDOW_DAYS``
calendar days ending at the run's ``end`` date. Dates strictly before the
boundary are treated as immutable historical fact once persisted.

Callers pick the window via :func:`refresh_window_start` rather than
reinventing the arithmetic. Keeping the math here avoided the drift PR #156
surfaced — prices.py used an 8-day window while build / sync_prices_nightly
used 7, producing subtle boundary discrepancies on the mutable/immutable
split.
"""
from __future__ import annotations

from datetime import date, timedelta

REFRESH_WINDOW_DAYS = 7


def refresh_window_start(end: date) -> date:
    """Earliest date in the refresh window.

    The refresh window is ``[refresh_window_start(end), end]`` inclusive —
    exactly ``REFRESH_WINDOW_DAYS`` calendar days. Dates strictly before this
    boundary are immutable once persisted.
    """
    return end - timedelta(days=REFRESH_WINDOW_DAYS - 1)
