"""Tests for yfinance error handling in prices.py."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from etl.prices import fetch_and_store_cny_rates, fetch_and_store_prices


class TestFetchAndStorePricesErrors:
    """yfinance failures should raise, not silently return."""

    @patch("etl.prices.fetch.yf")
    def test_download_exception_propagates(self, mock_yf: MagicMock, empty_db: Path) -> None:
        mock_yf.download.side_effect = Exception("network error")
        periods = {"AAPL": (date(2025, 1, 1), date(2025, 3, 1))}
        with pytest.raises(Exception, match="network error"):
            fetch_and_store_prices(empty_db, periods, date(2025, 3, 1))

    @patch("etl.prices.fetch.yf")
    def test_empty_dataframe_raises(self, mock_yf: MagicMock, empty_db: Path) -> None:
        mock_yf.download.return_value = pd.DataFrame()
        periods = {"AAPL": (date(2025, 1, 1), date(2025, 3, 1))}
        with pytest.raises(RuntimeError, match="empty DataFrame"):
            fetch_and_store_prices(empty_db, periods, date(2025, 3, 1))


class TestFetchAndStoreCnyRatesErrors:
    """CNY rate fetch failures should raise, not silently return."""

    @patch("etl.prices.fetch.yf")
    def test_download_exception_propagates(self, mock_yf: MagicMock, empty_db: Path) -> None:
        mock_yf.download.side_effect = Exception("timeout")
        with pytest.raises(Exception, match="timeout"):
            fetch_and_store_cny_rates(empty_db, date(2025, 1, 1), date(2025, 3, 1))

    @patch("etl.prices.fetch.yf")
    def test_empty_dataframe_raises(self, mock_yf: MagicMock, empty_db: Path) -> None:
        mock_yf.download.return_value = pd.DataFrame()
        with pytest.raises(RuntimeError, match="empty CNY"):
            fetch_and_store_cny_rates(empty_db, date(2025, 1, 1), date(2025, 3, 1))
