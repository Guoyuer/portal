"""Tests for market data fetchers (Yahoo Finance and FRED API)."""

import pytest

pytest.importorskip("yfinance", reason="yfinance required for market tests")

from unittest.mock import MagicMock, patch  # noqa: E402

import pandas as pd  # noqa: E402

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
