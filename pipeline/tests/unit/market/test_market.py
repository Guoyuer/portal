"""Tests for market data fetchers (Yahoo Finance and FRED API)."""

import pytest

pytest.importorskip("yfinance", reason="yfinance required for market tests")

from unittest.mock import MagicMock, patch  # noqa: E402

import pandas as pd  # noqa: E402

# ---------------------------------------------------------------------------
# Yahoo Finance — fetch_index_returns
# ---------------------------------------------------------------------------


class TestFetchIndexReturns:
    """Tests for etl.market.yahoo.fetch_index_returns."""

    @patch("etl.market.yahoo.yf.download")
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

        from etl.market.yahoo import fetch_index_returns

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

    @patch("etl.market.yahoo.yf.download")
    def test_empty_ticker_list(self, mock_download: MagicMock) -> None:
        """Empty ticker list returns empty dict immediately."""
        result = __import__(
            "etl.market.yahoo", fromlist=["fetch_index_returns"]
        ).fetch_index_returns([])

        assert result == {}
        mock_download.assert_not_called()

    @patch("etl.market.yahoo.yf.download")
    def test_api_failure_returns_empty_dict(self, mock_download: MagicMock) -> None:
        """Exception from yfinance.download is caught; returns empty dict."""
        mock_download.side_effect = Exception("network error")

        from etl.market.yahoo import fetch_index_returns

        result = fetch_index_returns(["SPY"])
        assert result == {}


# ---------------------------------------------------------------------------
# Yahoo Finance — fetch_dxy_monthly
# ---------------------------------------------------------------------------


class TestFetchDxyMonthly:
    """Tests for etl.market.yahoo.fetch_dxy_monthly."""

    @patch("etl.market.yahoo.yf.download")
    def test_resamples_daily_to_monthly_last(self, mock_download: MagicMock) -> None:
        """Daily closes resample to month-end last observation."""
        # 5 daily closes spanning 3 month-ends.
        dates = pd.to_datetime(["2025-01-30", "2025-01-31", "2025-02-27", "2025-02-28", "2025-03-31"])
        df = pd.DataFrame({"Close": [108.1, 108.2, 109.5, 109.7, 110.3]}, index=dates)
        mock_download.return_value = df

        from etl.market.yahoo import fetch_dxy_monthly

        result = fetch_dxy_monthly()
        assert result == [
            {"date": "2025-01", "value": 108.2},
            {"date": "2025-02", "value": 109.7},
            {"date": "2025-03", "value": 110.3},
        ]

    @patch("etl.market.yahoo.yf.download")
    def test_empty_dataframe_returns_empty(self, mock_download: MagicMock) -> None:
        mock_download.return_value = pd.DataFrame()

        from etl.market.yahoo import fetch_dxy_monthly

        assert fetch_dxy_monthly() == []

    @patch("etl.market.yahoo.yf.download")
    def test_api_failure_swallowed(self, mock_download: MagicMock) -> None:
        mock_download.side_effect = Exception("no network")

        from etl.market.yahoo import fetch_dxy_monthly

        assert fetch_dxy_monthly() == []
