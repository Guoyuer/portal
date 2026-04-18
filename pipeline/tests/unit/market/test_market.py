"""Tests for market data fetchers (Yahoo Finance and FRED API)."""

import pytest

pytest.importorskip("yfinance", reason="yfinance required for market tests")

from unittest.mock import MagicMock, patch  # noqa: E402

import pandas as pd  # noqa: E402

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
