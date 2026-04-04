"""Tests for market data fetchers (Yahoo Finance and FRED API)."""

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Yahoo Finance — fetch_index_returns
# ---------------------------------------------------------------------------


class TestFetchIndexReturns:
    """Tests for generate_asset_snapshot.market.yahoo.fetch_index_returns."""

    @patch("generate_asset_snapshot.market.yahoo.yf.download")
    def test_returns_data_for_spy_qqq(self, mock_download: MagicMock) -> None:
        """Returns return_pct, current, previous for each ticker."""
        # Build a multi-ticker DataFrame that yfinance.download returns.
        dates = pd.to_datetime(["2025-03-01", "2025-04-01"])
        data = pd.DataFrame(
            {"SPY": [500.0, 510.0], "QQQ": [400.0, 420.0]},
            index=dates,
        )
        data.columns = pd.MultiIndex.from_product([["Close"], ["SPY", "QQQ"]])
        mock_download.return_value = data

        from generate_asset_snapshot.market.yahoo import fetch_index_returns

        result = fetch_index_returns(["SPY", "QQQ"], period="1mo")

        assert "SPY" in result
        assert "QQQ" in result
        for ticker in ("SPY", "QQQ"):
            assert "return_pct" in result[ticker]
            assert "current" in result[ticker]
            assert "previous" in result[ticker]

        assert result["SPY"]["current"] == pytest.approx(510.0)
        assert result["SPY"]["previous"] == pytest.approx(500.0)
        assert result["SPY"]["return_pct"] == pytest.approx(2.0)  # (510-500)/500*100

        assert result["QQQ"]["return_pct"] == pytest.approx(5.0)  # (420-400)/400*100

    @patch("generate_asset_snapshot.market.yahoo.yf.download")
    def test_empty_ticker_list(self, mock_download: MagicMock) -> None:
        """Empty ticker list returns empty dict immediately."""
        result = __import__(
            "generate_asset_snapshot.market.yahoo", fromlist=["fetch_index_returns"]
        ).fetch_index_returns([])

        assert result == {}
        mock_download.assert_not_called()

    @patch("generate_asset_snapshot.market.yahoo.yf.download")
    def test_api_failure_returns_empty_dict(self, mock_download: MagicMock) -> None:
        """Exception from yfinance.download is caught; returns empty dict."""
        mock_download.side_effect = Exception("network error")

        from generate_asset_snapshot.market.yahoo import fetch_index_returns

        result = fetch_index_returns(["SPY"])
        assert result == {}


# ---------------------------------------------------------------------------
# Yahoo Finance — fetch_stock_info
# ---------------------------------------------------------------------------


class TestFetchStockInfo:
    """Tests for generate_asset_snapshot.market.yahoo.fetch_stock_info."""

    @patch("generate_asset_snapshot.market.yahoo.yf.Ticker")
    def test_returns_pe_market_cap(self, mock_ticker_cls: MagicMock) -> None:
        """Returns pe_ratio, market_cap, 52w_high, 52w_low for each ticker."""
        mock_info = {
            "trailingPE": 25.3,
            "marketCap": 2_800_000_000_000,
            "fiftyTwoWeekHigh": 530.0,
            "fiftyTwoWeekLow": 410.0,
        }
        mock_ticker = MagicMock()
        mock_ticker.info = mock_info
        mock_ticker_cls.return_value = mock_ticker

        from generate_asset_snapshot.market.yahoo import fetch_stock_info

        result = fetch_stock_info(["AAPL"])

        assert "AAPL" in result
        assert result["AAPL"]["pe_ratio"] == pytest.approx(25.3)
        assert result["AAPL"]["market_cap"] == 2_800_000_000_000
        assert result["AAPL"]["52w_high"] == pytest.approx(530.0)
        assert result["AAPL"]["52w_low"] == pytest.approx(410.0)

    @patch("generate_asset_snapshot.market.yahoo.yf.Ticker")
    def test_missing_fields_return_none(self, mock_ticker_cls: MagicMock) -> None:
        """Missing info keys map to None rather than raising."""
        mock_ticker = MagicMock()
        mock_ticker.info = {}  # empty info dict
        mock_ticker_cls.return_value = mock_ticker

        from generate_asset_snapshot.market.yahoo import fetch_stock_info

        result = fetch_stock_info(["XYZ"])

        assert result["XYZ"]["pe_ratio"] is None
        assert result["XYZ"]["market_cap"] is None
        assert result["XYZ"]["52w_high"] is None
        assert result["XYZ"]["52w_low"] is None

    @patch("generate_asset_snapshot.market.yahoo.yf.Ticker")
    def test_empty_ticker_list(self, mock_ticker_cls: MagicMock) -> None:
        """Empty ticker list returns empty dict."""
        from generate_asset_snapshot.market.yahoo import fetch_stock_info

        result = fetch_stock_info([])
        assert result == {}
        mock_ticker_cls.assert_not_called()

    @patch("generate_asset_snapshot.market.yahoo.yf.Ticker")
    def test_api_failure_returns_empty_dict(self, mock_ticker_cls: MagicMock) -> None:
        """Exception from yf.Ticker is caught; returns empty dict."""
        mock_ticker_cls.side_effect = Exception("API down")

        from generate_asset_snapshot.market.yahoo import fetch_stock_info

        result = fetch_stock_info(["AAPL"])
        assert result == {}


# ---------------------------------------------------------------------------
# FRED API — fetch_fred_series
# ---------------------------------------------------------------------------


class TestFetchFredSeries:
    """Tests for generate_asset_snapshot.market.fred.fetch_fred_series."""

    @patch("generate_asset_snapshot.market.fred.Fred")
    def test_returns_values_for_multiple_series(self, mock_fred_cls: MagicMock) -> None:
        """Returns value and date for GS10, CPIAUCSL, UNRATE."""
        series_map = {
            "GS10": pd.Series([4.25], index=pd.to_datetime(["2025-03-01"])),
            "CPIAUCSL": pd.Series([315.5], index=pd.to_datetime(["2025-02-01"])),
            "UNRATE": pd.Series([3.8], index=pd.to_datetime(["2025-03-01"])),
        }
        mock_fred = MagicMock()
        mock_fred.get_series.side_effect = lambda sid: series_map[sid]
        mock_fred_cls.return_value = mock_fred

        from generate_asset_snapshot.market.fred import fetch_fred_series

        result = fetch_fred_series(["GS10", "CPIAUCSL", "UNRATE"], api_key="fake-key")

        assert "GS10" in result
        assert "CPIAUCSL" in result
        assert "UNRATE" in result

        assert result["GS10"]["value"] == pytest.approx(4.25)
        assert result["GS10"]["date"] == "2025-03-01"
        assert result["CPIAUCSL"]["value"] == pytest.approx(315.5)
        assert result["UNRATE"]["value"] == pytest.approx(3.8)

    @patch("generate_asset_snapshot.market.fred.Fred")
    def test_empty_series_list(self, mock_fred_cls: MagicMock) -> None:
        """Empty series list returns empty dict."""
        from generate_asset_snapshot.market.fred import fetch_fred_series

        result = fetch_fred_series([], api_key="fake-key")
        assert result == {}
        mock_fred_cls.assert_not_called()

    @patch("generate_asset_snapshot.market.fred.Fred")
    def test_api_failure_returns_empty_dict(self, mock_fred_cls: MagicMock) -> None:
        """Exception from Fred client is caught; returns empty dict."""
        mock_fred_cls.side_effect = Exception("invalid API key")

        from generate_asset_snapshot.market.fred import fetch_fred_series

        result = fetch_fred_series(["GS10"], api_key="bad-key")
        assert result == {}
