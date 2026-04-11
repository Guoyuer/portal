"""Tests for yfinance error handling in prices.py."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from generate_asset_snapshot.db import init_db
from generate_asset_snapshot.prices import fetch_and_store_cny_rates, fetch_and_store_prices


@pytest.fixture()
def tmp_db(tmp_path: Path) -> Path:
    db = tmp_path / "test.db"
    init_db(db)
    return db


class TestFetchAndStorePricesErrors:
    """yfinance failures should raise, not silently return."""

    @patch("generate_asset_snapshot.prices.yf")
    def test_download_exception_propagates(self, mock_yf: MagicMock, tmp_db: Path) -> None:
        mock_yf.download.side_effect = Exception("network error")
        periods = {"AAPL": (date(2025, 1, 1), date(2025, 3, 1))}
        with pytest.raises(Exception, match="network error"):
            fetch_and_store_prices(tmp_db, periods, date(2025, 3, 1))

    @patch("generate_asset_snapshot.prices.yf")
    def test_empty_dataframe_raises(self, mock_yf: MagicMock, tmp_db: Path) -> None:
        mock_yf.download.return_value = pd.DataFrame()
        periods = {"AAPL": (date(2025, 1, 1), date(2025, 3, 1))}
        with pytest.raises(RuntimeError, match="empty DataFrame"):
            fetch_and_store_prices(tmp_db, periods, date(2025, 3, 1))


class TestFetchAndStoreCnyRatesErrors:
    """CNY rate fetch failures should raise, not silently return."""

    @patch("generate_asset_snapshot.prices.yf")
    def test_download_exception_propagates(self, mock_yf: MagicMock, tmp_db: Path) -> None:
        mock_yf.download.side_effect = Exception("timeout")
        with pytest.raises(Exception, match="timeout"):
            fetch_and_store_cny_rates(tmp_db, date(2025, 1, 1), date(2025, 3, 1))

    @patch("generate_asset_snapshot.prices.yf")
    def test_empty_dataframe_raises(self, mock_yf: MagicMock, tmp_db: Path) -> None:
        mock_yf.download.return_value = pd.DataFrame()
        with pytest.raises(RuntimeError, match="empty CNY"):
            fetch_and_store_cny_rates(tmp_db, date(2025, 1, 1), date(2025, 3, 1))
