"""Shared yfinance helpers used by the market data module and nightly scripts.

yfinance's :func:`yfinance.download` returns a DataFrame whose shape depends on
the number of tickers requested: single-ticker responses come back with flat
columns (``Open``/``Close``/...), multi-ticker responses use a ``MultiIndex``
(``(field, ticker)``). Three call sites across ``etl/prices.py``,
``scripts/sync_prices_nightly.py``, and ``etl/market/yahoo.py`` handled that
branching independently and inconsistently — :func:`extract_close` normalizes
the extraction into a single place.
"""

from __future__ import annotations

import logging

import pandas as pd

log = logging.getLogger(__name__)


def extract_close(df: pd.DataFrame, symbols: list[str]) -> pd.DataFrame:
    """Return the ``Close`` sub-frame from a yfinance download result.

    Handles the three shapes yfinance emits:
    - ``MultiIndex`` columns (multi-symbol batch) — extract the ``Close``
      level as a flat per-symbol frame.
    - Flat columns with a single requested symbol — rename ``Close`` to the
      symbol so downstream ``df[sym]`` lookups work uniformly.
    - Flat single-symbol response without ``Close`` — fall back to the first
      column (rare; CNY=X has occasionally come back with only ``Adj Close``).

    Rows that are entirely NaN are dropped. Returns an empty frame when no
    ``Close`` data can be located (e.g. malformed multi-symbol batch).
    """
    if df.empty:
        return df
    if isinstance(df.columns, pd.MultiIndex):
        close = df.xs("Close", level=0, axis=1)
    elif "Close" in df.columns:
        close = df[["Close"]].copy()
        if len(symbols) == 1:
            close.columns = [symbols[0]]
    elif len(symbols) == 1:
        # Single-symbol flat frame with no ``Close`` column — use the first.
        close = df.iloc[:, :1].copy()
        close.columns = [symbols[0]]
    else:
        return pd.DataFrame()
    return close.dropna(how="all")
