"""Tests for etl.market._yfinance helpers."""

from __future__ import annotations

import pandas as pd

from etl.market._yfinance import extract_close


class TestExtractClose:
    """``extract_close`` must handle every DataFrame shape yfinance emits."""

    def test_multi_symbol_multiindex(self) -> None:
        dates = pd.to_datetime(["2025-03-01", "2025-04-01"])
        df = pd.DataFrame(
            {"SPY": [500.0, 510.0], "QQQ": [400.0, 420.0]},
            index=dates,
        )
        df.columns = pd.MultiIndex.from_product([["Close"], ["SPY", "QQQ"]])

        result = extract_close(df, ["SPY", "QQQ"])
        assert list(result.columns) == ["SPY", "QQQ"]
        assert result.loc[dates[0], "SPY"] == 500.0
        assert result.loc[dates[1], "QQQ"] == 420.0

    def test_single_symbol_flat(self) -> None:
        dates = pd.to_datetime(["2025-03-01", "2025-04-01"])
        df = pd.DataFrame({"Close": [100.0, 101.0]}, index=dates)

        result = extract_close(df, ["CNY=X"])
        assert list(result.columns) == ["CNY=X"]
        assert result["CNY=X"].tolist() == [100.0, 101.0]

    def test_single_symbol_multiindex(self) -> None:
        dates = pd.to_datetime(["2025-03-01", "2025-04-01"])
        df = pd.DataFrame({"Close_SPY": [500.0, 510.0]}, index=dates)
        df.columns = pd.MultiIndex.from_product([["Close"], ["SPY"]])

        result = extract_close(df, ["SPY"])
        assert list(result.columns) == ["SPY"]
        assert result["SPY"].tolist() == [500.0, 510.0]

    def test_empty_frame(self) -> None:
        result = extract_close(pd.DataFrame(), ["SPY"])
        assert result.empty

    def test_flat_no_close_single_symbol_falls_back_to_first_column(self) -> None:
        dates = pd.to_datetime(["2025-03-01"])
        df = pd.DataFrame({"Adj Close": [100.0]}, index=dates)

        result = extract_close(df, ["CNY=X"])
        assert list(result.columns) == ["CNY=X"]
        assert result["CNY=X"].tolist() == [100.0]

    def test_flat_no_close_multi_symbol_returns_empty(self) -> None:
        """Defensive — never happens in practice with real yfinance responses."""
        dates = pd.to_datetime(["2025-03-01"])
        df = pd.DataFrame({"Foo": [100.0]}, index=dates)

        result = extract_close(df, ["SPY", "QQQ"])
        assert result.empty

    def test_drops_all_nan_rows(self) -> None:
        dates = pd.to_datetime(["2025-03-01", "2025-03-02", "2025-03-03"])
        df = pd.DataFrame(
            {"SPY": [500.0, None, 510.0], "QQQ": [400.0, None, 420.0]},
            index=dates,
        )
        df.columns = pd.MultiIndex.from_product([["Close"], ["SPY", "QQQ"]])

        result = extract_close(df, ["SPY", "QQQ"])
        assert list(result.index) == [dates[0], dates[2]]
